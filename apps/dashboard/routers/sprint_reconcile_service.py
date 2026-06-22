"""Background GitHub ↔ DB lifecycle reconcile (lifecycle P3).

Stale-while-revalidate: ``GET /api/sprints/history`` returns cached DB rows
immediately and schedules a background pass that re-checks terminal sprints
against GitHub, updates drift in the DB, and broadcasts deltas to the UI.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Callable, Awaitable

_log = logging.getLogger(__name__)

# B4 (history perf): the project history GET fires reconcile_project_background as
# a BackgroundTask on every page load. For a project with stuck/unmerged sprints
# that means an N+1 `gh` subprocess fan-out per load. Throttle to at most once per
# project per TTL so rapid refreshes coalesce. In-process best-effort; a restart
# clears it (fine — the next load reconciles).
_RECONCILE_TTL_SECONDS = 60.0
_last_reconcile_at: dict[str, float] = {}

_TERMINAL_STATES = frozenset({
    "ready_to_merge", "needs_rework", "completed",
    "cancelled", "failed",  # legacy stored values
})

# Outcome strings in agent_runs that indicate a ticket passed (tester approved).
_AGENT_RUN_DONE = frozenset({
    "merged", "pass", "passed", "success", "done", "complete",
    "completed", "uat", "shipped",
})
# Outcome strings that indicate a ticket failed/was rejected.
_AGENT_RUN_FAILED = frozenset({
    "fail", "failed", "reject", "rejected", "crash", "crashed",
    "skipped", "error",
})

# Repo root: apps/dashboard/routers/ → apps/dashboard/ → apps/ → repo root
_REPO_ROOT = Path(__file__).parent.parent.parent.parent


def _db():
    import db  # noqa: PLC0415
    return db


def _manager_pid_file(label: str) -> Path:
    """Return the per-sprint manager PID file path for the given sprint label."""
    return _REPO_ROOT / ".commander" / "sprints" / f"{label}-pid"


def _is_manager_pid_alive(label: str) -> bool:
    """Return True if the sprint-manager process owning *label* is still running."""
    pid_path = _manager_pid_file(label)
    if not pid_path.exists():
        return False
    try:
        pid = int(pid_path.read_text().strip())
    except (ValueError, OSError):
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # process exists but is owned by another user


def transition_sprint_state(
    label: str,
    state: str,
    actor: str,
    end_reason: str | None = None,
    project: str = "",
) -> bool:
    """Route a reconciler state transition through the DB write layer.

    All reconciler writes must go through this function so that a future
    foundation guard can intercept non-owner running→terminal transitions.
    Returns True when the transition was applied.
    """
    if state == "needs_rework":
        _db().record_sprint_needs_rework(label, end_reason=end_reason, project=project)
    elif state == "ready_to_merge":
        _db().record_sprint_ready_to_merge(label, end_reason=end_reason, project=project)
    else:
        _db().record_sprint_finish(label, end_reason=end_reason, project=project)
    return True


def _lineage_fully_in_develop(label: str, project: str) -> bool:
    """True when *label* is a lineage member whose work is already in develop.

    Used to auto-complete a superseded ancestor (B2). Two conditions:
      1. The sprint is part of a rerun lineage — it is itself a child
         (``sprint-N.M``) or it has at least one child row. A standalone
         needs_rework sprint with no children is a genuine failed run awaiting
         rerun and must never be auto-completed.
      2. Its sprint branch has no commits missing from develop — i.e. every
         commit it produced has merged up the chain into develop.

    Both checks are conservative: a missing repo, a still-open base→develop PR,
    or any compare error leaves commits "unmerged" and blocks promotion.
    """
    if not project:
        return False
    try:
        import server as srv  # noqa: PLC0415 — lazy, avoids import cycle
    except Exception:
        return False
    is_child = srv._is_child_sprint_label(label)
    has_children = bool(_db().get_sprint_children(label, project=project or None))
    if not (is_child or has_children):
        return False
    branch = srv._sprint_branch_name(label)
    return not srv._branch_has_unmerged_commits(project, branch, "develop")


def _github_reconcile_row(label: str, project: str, row: dict) -> dict | None:
    """Return updated fields when GitHub state diverges from the DB row, else None."""
    try:
        import server as srv  # noqa: PLC0415 — lazy to avoid import cycle at load time
    except Exception:
        return None

    stored = row.get("state") or ""
    if stored not in _TERMINAL_STATES and stored != "running":
        return None

    # When the sprint is running, only a *confirmed orphan* may be settled — a
    # manager PID file that exists but whose process is dead (issue #1088):
    #  • live PID             → actively running, no patch (AC1/AC2)
    #  • PID file present+dead → orphaned, settle below (AC3)
    #  • PID file absent       → unknown; a pure rework signal must NOT flip a
    #                            running sprint (issue #1095), so leave it running.
    if stored == "running":
        if not _manager_pid_file(label).exists():
            return None
        if _is_manager_pid_alive(label):
            return None

    try:
        has_rework = srv._has_rework_tickets(label, project)
    except Exception:
        return None

    # AC3: confirmed-orphan running sprint — settle state based on ticket outcomes.
    if stored == "running":
        if has_rework:
            return {"state": "needs_rework", "end_reason": "reconcile-orphan"}
        return {"state": "ready_to_merge", "end_reason": "reconcile-orphan"}

    canonical = _db().canonical_lifecycle(stored)
    # needs_rework→completed is never reconciler-driven: parent/ancestor sprints
    # stay open until an explicit Merge Sprint or Bulk complete (manager actor).
    if has_rework and canonical in ("ready_to_merge", "completed"):
        # A natural successful run end must not be downgraded because GitHub
        # labels lag (e.g. ticket still OPEN in UAT before Finish sprint).
        end_reason = row.get("end_reason") or ""
        if end_reason == "natural":
            try:
                import json
                issues = json.loads(row.get("issues_json") or "[]")
                if issues and all(
                    (i.get("state") or "").lower() == "merged"
                    or (i.get("agent_status") or "").lower() in ("completed", "done")
                    for i in issues
                ):
                    return None
            except Exception:
                pass
        return {"state": "needs_rework", "end_reason": end_reason or "github-reconcile"}
    if not has_rework and canonical == "needs_rework" and stored in ("needs_rework", "failed", "cancelled"):
        # All tickets settled on GitHub — promote to ready_to_merge when plausible.
        failed_in_db = False
        try:
            import json
            issues = json.loads(row.get("issues_json") or "[]")
            failed_in_db = any(
                (i.get("agent_status") or "").lower() == "failed" or i.get("failure_reason")
                for i in issues
            )
        except Exception:
            pass
        if not failed_in_db:
            return {"state": "ready_to_merge", "end_reason": row.get("end_reason") or "github-reconcile"}
    return None


def _issues_from_agent_runs(label: str) -> list[dict]:
    """Derive per-issue states from agent_runs records for a sprint.

    Mirrors the logic in sprint_history_service._issues_from_agent_runs to avoid
    a circular import. Any outcome in _AGENT_RUN_DONE marks the issue done (merged);
    otherwise it is failed or open (unknown).
    """
    try:
        rows = _db().agent_runs_for_sprint(label)
    except Exception:
        return []
    agg: dict[int, dict] = {}
    for row in rows:
        try:
            tid = int(row.get("issue_number") or 0)
        except (TypeError, ValueError):
            continue
        if tid <= 0:
            continue
        outcome = (row.get("outcome") or "").strip().lower()
        rec = agg.setdefault(tid, {"done": False, "failed": False})
        if outcome in _AGENT_RUN_DONE:
            rec["done"] = True
        elif outcome in _AGENT_RUN_FAILED:
            rec["failed"] = True
    result = []
    for tid in sorted(agg):
        rec = agg[tid]
        if rec["done"]:
            state, agent_status = "merged", "completed"
        elif rec["failed"]:
            state, agent_status = "closed", "failed"
        else:
            state, agent_status = "open", None
        result.append({
            "ticket_id": tid,
            "number": tid,
            "state": state,
            "agent_status": agent_status,
        })
    return result


def _reconcile_counts(label: str, row: dict, project: str = "") -> bool:
    """Re-derive issues_json and count columns from agent_runs for a terminal sprint.

    Non-terminal rows are skipped (AC4).  The function:
    1. Fetches agent_runs for *label* and derives per-issue states.
    2. Unions those with the stored issues_json — keeping title/pr_number/time_spent
       from existing entries while overriding state/agent_status from agent_runs.
    3. If any drift is found, persists the merged issues_json plus recomputed counts
       via db.update_sprint_run_counts() and calls db.update_sprint_reconciliation().

    Returns True when the DB row was updated.
    """
    import json as _json

    state = row.get("state") or ""
    if state not in _TERMINAL_STATES:
        return False

    ar_issues = _issues_from_agent_runs(label)
    if not ar_issues:
        return False

    # Build index of existing issues_json entries (keeps title, pr_number, time_spent)
    existing: list[dict] = []
    try:
        existing = _json.loads(row.get("issues_json") or "[]")
    except (ValueError, TypeError):
        existing = []

    by_id: dict[int, dict] = {}
    for iss in existing:
        tid_raw = iss.get("ticket_id") or iss.get("number")
        try:
            tid = int(tid_raw or 0)
        except (TypeError, ValueError):
            continue
        if tid > 0:
            by_id[tid] = dict(iss)

    changed = False
    for ar in ar_issues:
        tid = ar["ticket_id"]
        if tid in by_id:
            # Only override when agent_runs has a definitive (non-open) outcome so
            # we never downgrade an issue that already has a positive state.
            if ar["state"] != "open":
                cur = by_id[tid]
                state_diff = cur.get("state") != ar["state"]
                agent_diff = ar["agent_status"] and cur.get("agent_status") != ar["agent_status"]
                if state_diff or agent_diff:
                    cur["state"] = ar["state"]
                    if ar["agent_status"]:
                        cur["agent_status"] = ar["agent_status"]
                    changed = True
        else:
            # New issue from agent_runs with a definitive outcome
            if ar["state"] != "open":
                by_id[tid] = ar
                changed = True

    if not changed:
        return False

    merged = [by_id[k] for k in sorted(by_id)]

    # Recompute denormalized counts from the merged list
    settled_done = sum(
        1 for i in merged
        if (i.get("state") or "").lower() == "merged"
        or (i.get("agent_status") or "").lower() in ("completed", "done")
    )
    failure_count = sum(
        1 for i in merged
        if (i.get("agent_status") or "").lower() == "failed"
        or bool(i.get("failure_reason"))
    )
    uat_count = sum(
        1 for i in merged
        if (i.get("status") or "").lower() == "uat"
    )

    new_json = _json.dumps(merged)
    _proj = (project or row.get("project") or "").strip()
    _db().update_sprint_run_counts(
        label, new_json, settled_done, uat_count, failure_count, project=_proj,
    )
    _db().update_sprint_reconciliation(label, {
        "source": "count-reconcile",
        "fixed": True,
        "settled_done": settled_done,
        "failure_count": failure_count,
    })
    return True


def reconcile_sprint_label(label: str, project: str) -> bool:
    """Reconcile one sprint row against GitHub. Returns True if DB was updated."""
    row = _db().get_sprint(label)
    if not row:
        return False
    if project and row.get("project") and row.get("project") != project:
        return False
    _eff_project = project or row.get("project") or ""
    patch = _github_reconcile_row(label, _eff_project, row)
    lifecycle_updated = False
    if patch:
        # AC4 (original): all lifecycle writes go through transition_sprint_state.
        lifecycle_updated = transition_sprint_state(
            label,
            patch["state"],
            actor="reconcile",
            end_reason=patch.get("end_reason"),
            project=_eff_project,
        )
        if lifecycle_updated:
            # Re-fetch so _reconcile_counts sees the updated state.
            row = _db().get_sprint(label, project=_eff_project or None) or row
    # AC1: re-derive counts for terminal sprints alongside lifecycle correction.
    counts_updated = _reconcile_counts(label, row, project=_eff_project)
    return lifecycle_updated or counts_updated


def reconcile_project(project: str, limit: int = 40) -> list[str]:
    """Reconcile terminal sprints for *project*. Returns labels that were updated."""
    updated: list[str] = []
    rows = _db().list_sprints_lifecycle()
    checked = 0
    for row in rows:
        if checked >= limit:
            break
        label = row.get("label") or ""
        if not label:
            continue
        if project and row.get("project") != project:
            continue
        state = row.get("state") or ""
        if state == "running" or state in ("draft", "planned", "planning"):
            continue
        checked += 1
        if reconcile_sprint_label(label, project):
            updated.append(label)
    return updated


def _stored_reconciliation(row: dict, state: dict | None) -> dict:
    recon = (state or {}).get("reconciliation") or {}
    raw = row.get("reconciliation_json")
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                recon = parsed
        except (TypeError, ValueError):
            pass
    return recon if isinstance(recon, dict) else {}


def _reconciliation_needs_refresh(recon: dict) -> bool:
    """True when persisted post-sprint checks may be stale (e.g. PR merged since finish)."""
    if not recon:
        return False
    checks = recon.get("checks") or []
    return any(c.get("name") == "sprint_pr" and not c.get("ok") for c in checks)


def _ticket_numbers_for_reconciliation(row: dict, state: dict | None) -> list[int]:
    nums: list[int] = []
    seen: set[int] = set()
    for source in (
        (state or {}).get("issues") or [],
        json.loads(row.get("issues_json") or "[]") if row.get("issues_json") else [],
    ):
        for iss in source:
            n = iss.get("number", iss.get("ticket_id", iss.get("issue_number")))
            if n is None:
                continue
            try:
                num = int(n)
            except (TypeError, ValueError):
                continue
            if num not in seen:
                seen.add(num)
                nums.append(num)
    return nums


def _pr_url_for_reconciliation(repo: str, state: dict | None, row: dict, recon: dict) -> str | None:
    state = state or {}
    pr_url = state.get("sprint_pr_url") or state.get("pr_url")
    if pr_url:
        return str(pr_url)
    pr_number = row.get("pr_number")
    if pr_number is None:
        for chk in recon.get("checks") or []:
            if chk.get("name") == "sprint_pr" and chk.get("pr_number") is not None:
                pr_number = chk.get("pr_number")
                break
    if pr_number is None:
        pr_number = state.get("pr_number")
    if pr_number is not None and repo:
        return f"https://github.com/{repo}/pull/{int(pr_number)}"
    return None


def refresh_post_sprint_reconciliation(label: str, project: str) -> bool:
    """Re-run GitHub post-sprint reconciliation when loose ends may have cleared."""
    row = _db().get_sprint(label)
    if not row:
        return False
    if project and row.get("project") and row.get("project") != project:
        return False
    state = row.get("state") or ""
    if state == "running" or state in ("draft", "planned", "planning"):
        return False

    try:
        import server as srv  # noqa: PLC0415
        from . import sprint_artifact_service  # noqa: PLC0415
        from services.sprint_manager.reconciliation import (  # noqa: PLC0415
            gather_inputs_via_gh,
            run_reconciliation,
        )
    except Exception:
        return False

    repo = project or row.get("project") or ""
    if not repo:
        return False

    try:
        project_root = srv._project_root_path(repo)
        sprints_dir = project_root / ".commander" / "sprints"
        state_path = sprint_artifact_service.resolve_state_path(sprints_dir, label)
        state_data = sprint_artifact_service.load_state_file(sprints_dir, label) or {}
    except Exception:
        state_data = {}
        state_path = None

    recon = _stored_reconciliation(row, state_data)
    if not _reconciliation_needs_refresh(recon):
        return False

    pr_url = _pr_url_for_reconciliation(repo, state_data, row, recon)
    ticket_numbers = _ticket_numbers_for_reconciliation(row, state_data)
    try:
        rec_inputs = gather_inputs_via_gh(label, repo, pr_url, ticket_numbers)
        result = run_reconciliation(
            sprint_label=label,
            project=repo,
            state_path=state_path,
            summary_issues=rec_inputs["summary_issues"],
            pr_info=rec_inputs["pr_info"],
            tickets=rec_inputs["tickets"],
            emit_event=None,
        )
    except Exception as exc:
        _log.warning("post-sprint reconciliation refresh failed for %s: %s", label, exc)
        return False

    prior_fp = recon.get("fingerprint")
    if prior_fp and prior_fp == result.get("fingerprint"):
        return False

    if state_path is not None:
        try:
            merged = dict(state_data)
            merged["reconciliation"] = result
            state_path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
        except Exception:
            pass
    try:
        _db().update_sprint_reconciliation(label, result)
    except Exception:
        pass
    return True


def refresh_post_sprint_reconciliations(project: str, limit: int = 40) -> list[str]:
    """Refresh stale post-sprint reconciliation blocks for terminal sprints."""
    updated: list[str] = []
    rows = _db().list_sprints_lifecycle()
    checked = 0
    for row in rows:
        if checked >= limit:
            break
        label = row.get("label") or ""
        if not label:
            continue
        if project and row.get("project") != project:
            continue
        state = row.get("state") or ""
        if state == "running" or state in ("draft", "planned", "planning"):
            continue
        checked += 1
        if refresh_post_sprint_reconciliation(label, project):
            updated.append(label)
    return updated


async def reconcile_project_background(
    project: str,
    broadcast: Callable[[dict], Awaitable[None]] | None = None,
) -> None:
    """Background task: reconcile lifecycle + post-sprint loose ends, then notify."""
    now = time.monotonic()
    last = _last_reconcile_at.get(project)
    if last is not None and (now - last) < _RECONCILE_TTL_SECONDS:
        return  # coalesce rapid history refreshes — within TTL, skip the gh fan-out
    _last_reconcile_at[project] = now
    lifecycle_updated: list[str] = []
    reconciliation_updated: list[str] = []

    def _run_sync() -> tuple[list[str], list[str]]:
        # Both passes shell out to `gh` (blocking). Run them in a worker thread so
        # the spawning never stalls the event loop and starves concurrent
        # requests (history loads were taking ~5s while this ran inline).
        return reconcile_project(project), refresh_post_sprint_reconciliations(project)

    try:
        lifecycle_updated, reconciliation_updated = await asyncio.to_thread(_run_sync)
    except Exception as exc:
        _log.warning("sprint reconcile failed for %s: %s", project, exc)
        return
    updated = sorted(set(lifecycle_updated) | set(reconciliation_updated))
    if not updated or broadcast is None:
        return
    try:
        await broadcast({
            "type": "update",
            "event": {
                "event_type": "sprint_reconciled",
                "project": project,
                "labels": updated,
            },
        })
    except Exception as exc:
        _log.warning("sprint reconcile broadcast failed: %s", exc)
