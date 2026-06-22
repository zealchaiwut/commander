"""Finish-sprint background progress service (issue #929).

Runs the finish-sprint steps as a background asyncio task and emits
normalized ProgressActivity snapshots to SSE subscribers via asyncio.Queue.

The background task persists after SSE clients disconnect — closing the
modal does not cancel the operation (AC5).

Job store:
  _FINISH_JOBS: dict[job_key → latest snapshot]
  _FINISH_SUBS: dict[job_key → list[asyncio.Queue]]

A snapshot has the ProgressActivity shape (AC1, AC2, AC3, AC4):
  { status, mode, done, total, current, log_tail, result?, error? }
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
if str(_DASHBOARD_ROOT) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_ROOT))

# ── Job store ─────────────────────────────────────────────────────────────────

_FINISH_JOBS: dict[str, dict] = {}
_FINISH_SUBS: dict[str, list[asyncio.Queue]] = {}


def job_key(owner: str, repo_name: str, label: str) -> str:
    """Canonical key for a finish job: '<label>@<owner>/<repo>'."""
    return f"{label}@{owner}/{repo_name}"


def get_snapshot(key: str) -> dict | None:
    """Return the latest snapshot for a job, or None if no job exists."""
    return _FINISH_JOBS.get(key)


def is_running(key: str) -> bool:
    snap = _FINISH_JOBS.get(key)
    return snap is not None and snap.get("status") == "running"


def subscribe(key: str) -> asyncio.Queue:
    """Register a new subscriber queue for a job (AC5: queue survives disconnect)."""
    q: asyncio.Queue = asyncio.Queue(maxsize=256)
    _FINISH_SUBS.setdefault(key, []).append(q)
    return q


def unsubscribe(key: str, q: asyncio.Queue) -> None:
    subs = _FINISH_SUBS.get(key, [])
    if q in subs:
        subs.remove(q)


def _norm_error_str(err) -> str:
    """Coerce merge/close errors to a string for logging and comparisons."""
    if err is None:
        return ""
    if isinstance(err, str):
        return err
    if isinstance(err, (list, tuple)):
        parts = [_norm_error_str(x) for x in err if x is not None]
        return "; ".join(p for p in parts if p)
    return str(err)


def _merge_failure_message(errors: list[str]) -> str:
    joined = "; ".join(_norm_error_str(e) for e in errors if e)
    if any(_norm_error_str(e) and "merge conflict" in _norm_error_str(e).lower() for e in errors):
        return f"Merge conflict — sprint not completed. {joined}".strip()
    return f"Merge failed — sprint not completed. {joined}".strip()


async def _emit(key: str, snapshot: dict) -> None:
    """Persist snapshot and fan-out to all subscriber queues."""
    _FINISH_JOBS[key] = snapshot
    for q in list(_FINISH_SUBS.get(key, [])):
        try:
            q.put_nowait(snapshot)
        except asyncio.QueueFull:
            pass
    await asyncio.sleep(0)  # yield to event loop


def _server():
    """Deferred import of server.py — avoids import-time circular dependency."""
    import server as _srv  # noqa: PLC0415
    return _srv


# ── Background task ───────────────────────────────────────────────────────────

async def run_finish_sprint(
    key: str,
    repo: str,
    label: str,
    selected_nums: list[int],
    selected_tickets: list[dict],
    merge_pr: bool,
    sprint_pr_url: Optional[str],
    move_non_uat_to: str,
) -> None:
    """Run finish-sprint steps in the background, emitting progress events.

    Steps (merge must succeed before any settlement):
      1. Merge sprint branch chain      (1 step — done=1)
      2. Close each selected issue      (N steps — done=1+i+1 per issue)
      3. Update plan.json + caches      (1 step — done=total)

    Total = N + 2  (AC2)
    """
    title_map = {t["number"]: t.get("title", f"#{t['number']}") for t in selected_tickets}
    n_issues = len(selected_nums)
    total = n_issues + 2
    log_tail: list[str] = []

    def _log(msg: str) -> None:
        log_tail.append(msg)
        if len(log_tail) > 150:
            log_tail.pop(0)

    snapshot: dict = {
        "status": "running",
        "mode": "bar",
        "done": 0,
        "total": total,
        "current": "Starting…",
        "log_tail": [],
    }
    await _emit(key, snapshot)

    closed = 0
    errors: list[str] = []

    try:
        srv = _server()

        # ── Step 1: Merge sprint branches ─────────────────────────────────────
        _log("Merging sprint branches…")
        snapshot = {**snapshot, "current": "Merging sprint branches…", "log_tail": list(log_tail)}
        await _emit(key, snapshot)

        # Fatal if label can't be parsed — propagates to outer except (AC8).
        base_label = srv._sprint_label_base(label)
        merge_errors: list[str] = []
        merge_pr_number: int | None = None

        try:
            merge_errors, merge_pr_number = await asyncio.to_thread(
                srv._merge_sprint_branches_for_label, repo, label
            )
            if not isinstance(merge_errors, list):
                merge_errors = []
            for err in merge_errors:
                _log(f"Merge error: {err}")
            if not merge_errors:
                _log("Sprint branches merged.")
        except Exception as exc:
            merge_errors = [f"Merge step failed: {exc}"]
            _log(f"Merge error: {exc}")

        if merge_pr and sprint_pr_url:
            _url = sprint_pr_url

            def _merge_pr() -> str | None:
                r = subprocess.run(
                    ["gh", "pr", "merge", _url, "--repo", repo, "--merge", "--delete-branch"],
                    capture_output=True, text=True, check=False,
                )
                if r.returncode != 0:
                    return r.stderr.strip() or "PR merge failed"
                return None

            try:
                pr_err = await asyncio.to_thread(_merge_pr)
                if pr_err:
                    merge_errors.append(f"PR merge failed: {pr_err}")
                    _log(f"PR merge failed: {pr_err}")
                else:
                    _log("Sprint PR merged.")
            except Exception as exc:
                merge_errors.append(f"PR merge error: {exc}")
                _log(f"PR merge error: {exc}")

        if merge_errors:
            msg = _merge_failure_message(merge_errors)
            _log(msg)
            error_snap: dict = {
                "status": "error",
                "mode": "bar",
                "error": msg,
                "log_tail": list(log_tail),
                "done": 1,
                "total": total,
                "errors": merge_errors,
            }
            await _emit(key, error_snap)
            return

        snapshot = {**snapshot, "done": 1, "log_tail": list(log_tail)}
        await _emit(key, snapshot)

        # ── Steps 2..N+1: Close each selected issue ───────────────────────────
        gh = srv.github_client
        for i, num in enumerate(selected_nums):
            title = title_map.get(num, f"#{num}")
            current = f"Closing #{num} — {title}"
            _log(current)
            snapshot = {**snapshot, "current": current, "log_tail": list(log_tail)}
            await _emit(key, snapshot)

            try:
                _n = num

                def _close(n=_n):
                    gh.close_issue(n, repo_name=repo, reason="completed")

                await asyncio.to_thread(_close)
                closed += 1
                _log(f"Closed #{num}")
            except subprocess.CalledProcessError as exc:
                err_msg = exc.stderr.strip() if exc.stderr else str(exc)
                errors.append(f"#{num}: {err_msg}")
                _log(f"Error closing #{num}: {err_msg}")

            snapshot = {**snapshot, "done": 1 + i + 1, "log_tail": list(log_tail)}
            await _emit(key, snapshot)

        # ── Step N+2: Update plan.json + invalidate caches ────────────────────
        _log("Updating sprint state…")
        snapshot = {**snapshot, "current": "Updating sprint state…", "log_tail": list(log_tail)}
        await _emit(key, snapshot)

        try:
            project_root = srv._project_root_path(repo)
            if srv._is_child_sprint_label(label):
                finish_labels = [label]
            else:
                finish_labels = [
                    base_label,
                    *srv.children_of(base_label, project_root, project=repo),
                ]
            for lbl in finish_labels:
                try:
                    srv._plan_json_set_state(
                        project_root, lbl, "completed",
                        ended_at=datetime.now(timezone.utc).isoformat(),
                        end_reason="merge_sprint",
                    )
                except Exception as exc:
                    errors.append(f"plan.json update failed for {lbl}: {exc}")
                    _log(f"plan.json error for {lbl}: {exc}")
                srv._sprint_db_set_state(
                    lbl, repo, "completed",
                    ended_at=datetime.now(timezone.utc).isoformat(),
                    end_reason="merge_sprint",
                )
            if merge_pr_number:
                try:
                    import db as _db  # noqa: PLC0415
                    _db.update_sprint_pr_number(base_label, merge_pr_number, repo)
                except Exception:
                    pass
        except Exception as exc:
            errors.append(f"Cleanup error: {exc}")
            _log(f"Cleanup error: {exc}")

        try:
            for prefix in ("open_issues_body:", "open_issues:", "issues:", "recent_closed:"):
                srv.github_client.invalidate(prefix)
        except Exception:
            pass

        _log("Done.")

        parts: list[str] = [f"{closed} issue{'s' if closed != 1 else ''} closed", "sprint merged"]
        result = ", ".join(parts)
        if errors:
            n_err = len(errors)
            result += f" ({n_err} warning{'s' if n_err != 1 else ''})"

        done_snap: dict = {
            "status": "done",
            "mode": "bar",
            "done": total,
            "total": total,
            "current": "Done",
            "result": result,
            "log_tail": list(log_tail),
            "closed": closed,
            "errors": errors,
        }
        await _emit(key, done_snap)

        try:
            asyncio.create_task(srv.broadcast({
                "type": "update",
                "event": {"event_type": "sprint_finished", "sprint_label": label},
            }))
        except Exception:
            pass

    except Exception as exc:
        _log(f"Fatal error: {exc}")
        error_snap: dict = {
            "status": "error",
            "mode": "bar",
            "error": str(exc),
            "log_tail": list(log_tail),
            "done": snapshot.get("done", 0),
            "total": total,
        }
        await _emit(key, error_snap)
