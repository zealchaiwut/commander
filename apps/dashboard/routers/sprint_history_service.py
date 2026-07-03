"""Service logic for the sprint-history router (issue #805).

Builds enriched sprint-history rows for ``GET /api/sprints/history`` from
local sources ONLY — the durable DB tables and on-disk sprint state/summary
files. **No GitHub API call is made at read time** (AC5); that is the whole
point of the endpoint, so the ledger feed renders instantly and offline.

Sources, in priority order per sprint label:

1. ``sprint_history`` table — terminal snapshots (e.g. ``state='deleted'``)
   captured at the moment the event happened. Authoritative when present.
2. ``sprints`` lifecycle table — running/completed/cancelled/failed rows,
   enriched with per-ticket data from the matching ``<label>-state.json``.
3. ``<label>-state.json`` / ``<label>.json`` plan files — fallback for sprints
   that never made it into the lifecycle DB (legacy / file-only sprints).

The delete path calls :func:`record_deleted_sprint` BEFORE stripping labels so
a deleted sprint is queryable here immediately afterwards (AC7/AC8).
"""
from __future__ import annotations

import json
import re
import sys as _sys
from pathlib import Path

# apps/dashboard is on sys.path so ``import db`` resolves (see server bootstrap).
_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
if str(_DASHBOARD_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_DASHBOARD_ROOT))

# The dashboard's own sprint state/summary directory, mirroring server.SPRINTS_DIR.
DEFAULT_SPRINTS_DIR = _DASHBOARD_ROOT / "sprints"


def _resolve_sprints_search_dirs(project: str | None = None) -> list[Path]:
    """Return sprint artifact dirs to search, commander project roots first.

    Bulk-complete and sprint_manager write plan/state under
    ``<project_root>/.commander/sprints``; the dashboard also keeps a local
  ``apps/dashboard/sprints`` tree for legacy/test rows. History must read both.
    """
    dirs: list[Path] = []
    seen: set[str] = set()

    def _add(p: Path) -> None:
        if not p.is_dir():
            return
        try:
            key = str(p.resolve())
        except OSError:
            key = str(p)
        if key in seen:
            return
        seen.add(key)
        dirs.append(p)

    repos: list[str] = []
    if project:
        repos = [project]
    else:
        try:
            import projects as projects_module  # noqa: PLC0415
            for proj in projects_module.load_projects():
                repo = proj.get("repo")
                if repo:
                    repos.append(repo)
        except Exception:
            pass

    for repo in repos:
        try:
            import server as srv  # noqa: PLC0415
            root = srv._project_root_path(repo)
            _add(srv._commander_dir(root) / "sprints")
        except Exception:
            continue

    _add(DEFAULT_SPRINTS_DIR)
    return dirs


def _as_sprints_dirs(sprints_dir: Path | list[Path] | None, project: str | None) -> list[Path]:
    """Normalize the optional test override vs project-scoped search list."""
    if sprints_dir is not None:
        return [sprints_dir] if isinstance(sprints_dir, Path) else list(sprints_dir)
    return _resolve_sprints_search_dirs(project)


def _db():
    """Deferred import of the db module (honours a patched DB_PATH at call time)."""
    import db  # noqa: PLC0415
    return db


# ── shape helpers ─────────────────────────────────────────────────────────────

# Local ticket dispositions → the merged/closed/open vocabulary the AC mandates.
_MERGED_STATUSES = {"done", "shipped", "merged", "passed", "complete", "completed", "uat"}
_CLOSED_STATUSES = {"skipped", "failed", "cancelled", "closed", "rejected", "blocked"}


def _map_issue_state(raw: str | None) -> str:
    """Map a local ticket status / GitHub issue state to merged|closed|open."""
    s = (raw or "").strip().lower()
    if s in _MERGED_STATUSES:
        return "merged"
    if s in _CLOSED_STATUSES:
        return "closed"
    return "open"


def _seconds_between(start: str | None, end: str | None) -> int | None:
    """Whole seconds between two ISO-8601 timestamps, or None if unusable.

    Timestamps come from mixed sources: some carry a Z/offset (aware after
    fromisoformat), others are bare naive-UTC strings. Normalize both to aware
    UTC — subtracting naive from aware raises TypeError, which 500'd the whole
    History endpoint.
    """
    if not start or not end:
        return None
    from datetime import datetime, timezone
    try:
        s = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        e = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if s.tzinfo is None:
        s = s.replace(tzinfo=timezone.utc)
    if e.tzinfo is None:
        e = e.replace(tzinfo=timezone.utc)
    return round((e - s).total_seconds())


def _issue_time_spent(iss: dict) -> int | None:
    """Per-ticket wall-clock seconds from coder start to the latest finish."""
    start = iss.get("coder_started_at") or iss.get("tester_started_at")
    end = iss.get("tester_finished_at") or iss.get("coder_finished_at")
    return _seconds_between(start, end)


def _parse_issue_num_from_url(url: str | None) -> int | None:
    if not url:
        return None
    m = re.search(r"/issues/(\d+)", str(url))
    return int(m.group(1)) if m else None


def _parse_pr_number(state: dict) -> int | None:
    """Best-effort PR number from a sprint state file or reconciliation block."""
    recon = state.get("reconciliation") or {}
    for chk in recon.get("checks") or []:
        if chk.get("name") == "sprint_pr":
            pr = chk.get("pr_number")
            if pr is not None:
                return int(pr)
            if chk.get("ok"):
                detail = str(chk.get("detail") or "")
                m = re.search(r"#(\d+)", detail)
                if m:
                    return int(m.group(1))
    pr_url = state.get("pr_url") or state.get("sprint_pr_url")
    if pr_url:
        m = re.search(r"/pull/(\d+)", str(pr_url))
        if m:
            return int(m.group(1))
    pr = state.get("pr_number")
    return int(pr) if pr is not None else None


def _normalize_issue(iss: dict) -> dict:
    """Project a state-file ticket dict into the AC3 issue shape."""
    ticket_id = iss.get("number", iss.get("ticket_id", iss.get("issue_number")))
    pr = iss.get("pr_number")
    if pr is None and isinstance(iss.get("pr"), dict):
        pr = iss["pr"].get("number")
    agent_status = (iss.get("agent_status") or "").strip().lower() or None
    failure_reason = iss.get("failure_reason")
    title = iss.get("title")
    out = {
        "ticket_id": ticket_id,
        "state": _map_issue_state(iss.get("status") or iss.get("state")),
        "time_spent": iss.get("time_spent", _issue_time_spent(iss)),
        "pr_number": pr,
        "title": str(title) if title else "",
    }
    if agent_status:
        out["agent_status"] = agent_status
    if failure_reason:
        out["failure_reason"] = str(failure_reason)
    return out


def _failed_tickets_from_raw(issues_raw: list[dict]) -> list[dict]:
    """Tickets that failed during the sprint, with their failure reason."""
    failed: list[dict] = []
    for iss in issues_raw:
        agent = (iss.get("agent_status") or "").lower()
        reason = iss.get("failure_reason")
        if agent == "failed" or reason:
            tid = iss.get("number", iss.get("ticket_id", iss.get("issue_number")))
            failed.append({
                "ticket_id": tid,
                "failure_reason": str(reason or "Agent failed"),
            })
    return failed


def _compute_estimate_accuracy(state: dict) -> float | None:
    """Estimated-vs-actual ratio from a state file, or None when not computable.

    Uses estimator minutes against actual wall-clock; >1.0 means the sprint ran
    longer than estimated, <1.0 means it beat the estimate.
    """
    est_min = state.get("estimator_total_minutes")
    actual_secs = state.get("wall_clock_secs")
    if not est_min or not actual_secs:
        return None
    est_secs = float(est_min) * 60.0
    try:
        return round(float(actual_secs) / est_secs, 3) if est_secs else None
    except (ZeroDivisionError, TypeError, ValueError):
        return None


# ── file readers ──────────────────────────────────────────────────────────────

def _read_state_file(sprints_dirs: Path | list[Path], label: str) -> dict | None:
    """Load state file for *label* (per-label path with legacy fallback)."""
    from . import sprint_artifact_service  # noqa: PLC0415
    dirs = [sprints_dirs] if isinstance(sprints_dirs, Path) else list(sprints_dirs)
    for sprints_dir in dirs:
        state = sprint_artifact_service.load_state_file(sprints_dir, label)
        if state is not None:
            return state
    return None


def _read_plan_file(sprints_dirs: Path | list[Path], label: str) -> dict | None:
    """Load plan state from ``<label>-plan.json`` (canonical) or ``<label>.json``."""
    from . import sprint_artifact_service  # noqa: PLC0415
    dirs = [sprints_dirs] if isinstance(sprints_dirs, Path) else list(sprints_dirs)
    for sprints_dir in dirs:
        for root in sprint_artifact_service._sprint_roots(sprints_dir):
            for name in (f"{label}-plan.json", f"{label}.json"):
                path = root / name
                if not path.is_file():
                    continue
                try:
                    return json.loads(path.read_text(encoding="utf-8"))
                except (ValueError, OSError):
                    return None
    return None


# agent_runs.outcome values that indicate a ticket's work landed / was rejected.
_AGENT_RUN_MERGED = {"merged", "pass", "passed", "success", "done", "complete",
                     "completed", "uat", "shipped"}
_AGENT_RUN_FAILED = {"fail", "failed", "reject", "rejected", "crash", "crashed",
                     "skipped", "error"}


def _issues_from_agent_runs(label: str, project: str | None = None) -> list[dict]:
    """Synthesize issue rows from agent_runs, deriving each ticket's disposition
    from its run outcomes.

    Used both as a fallback when state.json has no tickets AND to UNION in
    tickets that ran under this sprint but were dropped from the latest state
    file (e.g. merged in an earlier run of the same sprint) so the History ledger
    matches the Board rather than hiding successes.
    """
    try:
        rows = _db().agent_runs_for_sprint(label, project=project)
    except Exception:
        return []
    agg: dict[int, dict] = {}
    for row in rows:
        num = row.get("issue_number")
        try:
            tid = int(num)
        except (TypeError, ValueError):
            continue
        if tid <= 0:
            continue
        outcome = (row.get("outcome") or "").strip().lower()
        rec = agg.setdefault(tid, {"merged": False, "failed": False})
        if outcome in _AGENT_RUN_MERGED:
            rec["merged"] = True
        elif outcome in _AGENT_RUN_FAILED:
            rec["failed"] = True
    issues: list[dict] = []
    for tid in sorted(agg):
        rec = agg[tid]
        state = "merged" if rec["merged"] else ("closed" if rec["failed"] else "open")
        issues.append({
            "ticket_id": tid,
            "state": state,
            "time_spent": None,
            "pr_number": None,
            "from_agent_runs": True,
        })
    return issues


def _reconcile_issue_outcomes_with_agent_runs(records: list[dict]) -> None:
    """Promote per-ticket state from agent_runs when the state file lags GitHub.

    A ticket that finished coder+tester (agent_runs outcome merged) but still
    shows OPEN·UAT in the ingested roster must read as merged on History cards
    (e.g. #818 on sprint-97.5 before the sprint crashed on the next ticket).
    """
    for rec in records:
        label = rec.get("label") or ""
        project = rec.get("project") or None
        by_tid = {i["ticket_id"]: i for i in _issues_from_agent_runs(label, project)}
        if not by_tid:
            continue
        for iss in rec.get("issues") or []:
            tid = iss.get("ticket_id")
            syn = by_tid.get(tid)
            if not syn:
                continue
            syn_st = (syn.get("state") or "").lower()
            cur_st = (iss.get("state") or "").lower()
            if syn_st == "merged" and cur_st != "merged":
                iss["state"] = "merged"
            elif syn_st == "closed" and cur_st == "open":
                iss.setdefault("agent_status", "failed")


def _find_summary_path(sprints_dirs: Path | list[Path], label: str) -> str | None:
    """Most recent ``<label>-summary-*.md`` path for a sprint, or None."""
    dirs = [sprints_dirs] if isinstance(sprints_dirs, Path) else list(sprints_dirs)
    from . import sprint_artifact_service  # noqa: PLC0415
    cands: list[Path] = []
    for sprints_dir in dirs:
        if not sprints_dir.exists():
            continue
        for root in sprint_artifact_service._sprint_roots(sprints_dir):
            cands.extend(root.glob(f"{label}-summary-*.md"))
    if not cands:
        return None
    cands.sort(key=lambda p: p.name, reverse=True)
    return str(cands[0])


def _enrich_from_state(label: str, sprints_dirs: Path | list[Path]) -> dict:
    """Per-ticket issues, tokens, duration, estimate_accuracy from local files."""
    state = _read_state_file(sprints_dirs, label)
    plan = _read_plan_file(sprints_dirs, label) or {}
    out: dict = {
        "issues": [],
        "issues_raw": [],
        "tokens": None,
        "duration": None,
        "estimate_accuracy": None,
        "summary_path": _find_summary_path(sprints_dirs, label),
        "reconciliation": None,
        "pr_number": None,
        "summary_issue_url": None,
        "summary_issue_num": None,
        "end_reason": plan.get("end_reason"),
        "plan_status": plan.get("state") or plan.get("status"),
        "failed_tickets": [],
        "failure_reason": None,
    }
    if not state:
        return out
    issues_raw = state.get("issues", [])
    out["issues_raw"] = issues_raw
    out["issues"] = [_normalize_issue(i) for i in issues_raw]
    # Union with tickets recorded in agent_runs but missing from the current state
    # file — e.g. tickets that merged in an EARLIER run of this sprint and were
    # dropped from the latest state. Without this the History ledger shows fewer
    # tickets than the Board (sprint-73 showed 7 vs the board's 10, hiding the
    # already-merged #901/#903/#926). Hotfix B.
    _have = {i.get("ticket_id") for i in out["issues"]}
    for _extra in _issues_from_agent_runs(label):
        if _extra.get("ticket_id") not in _have:
            out["issues"].append(_extra)
            _have.add(_extra.get("ticket_id"))
    out["failed_tickets"] = _failed_tickets_from_raw(out["issues"])
    tin = state.get("total_tokens_in") or 0
    tout = state.get("total_tokens_out") or 0
    out["tokens"] = int(tin) + int(tout)
    wc = state.get("wall_clock_secs")
    out["duration"] = round(wc) if isinstance(wc, (int, float)) else None
    out["estimate_accuracy"] = _compute_estimate_accuracy(state)
    # Post-sprint reconciliation block (issue #856) — surfaced verbatim so the
    # history card can render the loose-ends checklist with no GitHub call.
    out["reconciliation"] = state.get("reconciliation")
    out["pr_number"] = _parse_pr_number(state)
    surl = state.get("summary_issue_url")
    out["summary_issue_url"] = surl
    out["summary_issue_num"] = _parse_issue_num_from_url(surl)
    if out["failed_tickets"]:
        out["failure_reason"] = out["failed_tickets"][-1]["failure_reason"]
    if _issues_all_shipped(out["issues"]):
        out["failed_tickets"] = []
        out["failure_reason"] = None
    out["post_sprint"] = _build_post_sprint(state)
    return out


def _build_post_sprint(state: dict) -> dict | None:
    """Post-sprint documenter + reviewer outcomes persisted in ``<label>-state.json``."""
    doc_status = state.get("documenter_status")
    rev_status = state.get("reviewer_status")
    if not doc_status and not rev_status:
        return None

    doc_files = state.get("documenter_files_touched") or []
    if isinstance(doc_files, str):
        doc_files = [doc_files]
    doc_files = [str(f).strip() for f in doc_files if str(f).strip()]

    findings = state.get("reviewer_findings") if isinstance(state.get("reviewer_findings"), dict) else {}
    follow_ups: list[int] = []
    for raw in findings.get("follow_up_tickets") or []:
        try:
            follow_ups.append(int(raw))
        except (TypeError, ValueError):
            continue

    documenter = None
    if doc_status:
        documenter = {
            "status": str(doc_status),
            "files_touched": doc_files,
            "commit_sha": state.get("documenter_commit_sha"),
        }

    reviewer = None
    if rev_status:
        reviewer = {
            "status": str(rev_status),
            "comment_url": state.get("reviewer_comment_url"),
            "blockers": int(findings.get("blockers") or 0),
            "suggestions": int(findings.get("suggestions") or 0),
            "nits": int(findings.get("nits") or 0),
            "follow_up_tickets": follow_ups,
        }

    # Surface the block when either agent ran, or when we have concrete outputs.
    has_output = bool(doc_files or follow_ups)
    ran = (
        (doc_status and doc_status != "skipped")
        or (rev_status and rev_status != "skipped")
    )
    if not ran and not has_output:
        return None

    return {
        "note": "Agents ran after ticket work finished",
        "documenter": documenter,
        "reviewer": reviewer,
    }


# ── lifecycle-state normalization ─────────────────────────────────────────────
#
# One enum for every pane (docs/architecture/sprint-lifecycle.md). Legacy rows
# (`cancelled`, `failed`, `finished`, `planning`, …) render through the display
# mapping in db.canonical_lifecycle — forward-only migration, no DB rewrite.


def _normalize_state(raw: str | None) -> str:
    return _db().canonical_lifecycle(raw)


def _issues_all_shipped(issues: list[dict]) -> bool:
    """True when every ticket in the sprint run merged or completed successfully."""
    if not issues:
        return False
    return all(
        (i.get("state") or "").lower() == "merged"
        or (i.get("agent_status") or "").lower() in ("completed", "done")
        for i in issues
    )


def _lifecycle_display_state(lifecycle: str, end_reason: str | None, issues: list[dict]) -> str:
    """Correct mis-tagged terminal rows before History renders them (issue #1137)."""
    if lifecycle in ("completed", "deleted"):
        return lifecycle
    if lifecycle in ("needs_rework", "failed") and _issues_all_shipped(issues):
        return "ready_to_merge"
    if lifecycle != "needs_rework" or (end_reason or "") != "natural":
        return lifecycle
    if not issues:
        return lifecycle
    if all(
        (i.get("state") or "").lower() == "merged"
        or (i.get("agent_status") or "").lower() in ("completed", "done")
        for i in issues
    ):
        return "ready_to_merge"
    return lifecycle


def _clear_stale_failure_signals(rec: dict) -> None:
    """Drop phantom failed_tickets when every issue actually shipped."""
    st = (rec.get("lifecycle_state") or "").lower()
    if st in ("completed", "deleted"):
        return
    if (rec.get("end_reason") or "").strip() == "bulk_complete":
        return
    issues = rec.get("issues") or []
    if not _issues_all_shipped(issues):
        return
    rec["failed_tickets"] = []
    rec["failure_reason"] = None
    if st in ("needs_rework", "failed"):
        rec["lifecycle_state"] = "ready_to_merge"


# ── record builders ───────────────────────────────────────────────────────────

def _record_from_history(rec: dict) -> dict:
    """Build the response row from a sprint_history snapshot (authoritative)."""
    issues = [_normalize_issue(i) if "ticket_id" not in i else i for i in rec.get("issues", [])]
    failed_tickets = [
        {"ticket_id": i.get("ticket_id"), "failure_reason": i.get("failure_reason") or "Agent failed"}
        for i in issues
        if i.get("failure_reason") or (i.get("agent_status") or "").lower() == "failed"
    ]
    lifecycle_state = _lifecycle_display_state(
        _normalize_state(rec.get("lifecycle_state")),
        rec.get("end_reason"),
        issues,
    )
    if _issues_all_shipped(issues):
        failed_tickets = []
    return {
        "label": rec.get("label"),
        "project": rec.get("project", ""),
        "lifecycle_state": lifecycle_state,
        "end_reason": rec.get("end_reason"),
        "duration": rec.get("duration"),
        "tokens": rec.get("tokens"),
        "estimate_accuracy": rec.get("estimate_accuracy"),
        "pr_number": rec.get("pr_number"),
        "summary_path": rec.get("summary_path"),
        "summary_issue_url": rec.get("summary_issue_url"),
        "summary_issue_num": rec.get("summary_issue_num"),
        "reconciliation": rec.get("reconciliation"),
        "issues": issues,
        "failed_tickets": failed_tickets,
        "failure_reason": rec.get("failure_reason") or (failed_tickets[-1]["failure_reason"] if failed_tickets else None),
        "post_sprint": rec.get("post_sprint"),
        "_sort_key": rec.get("created_at") or "",
        "_source": "history",
    }


def _record_from_lifecycle(row: dict, sprints_dirs: Path | list[Path]) -> dict:
    """Build the response row from a `sprints` lifecycle row + DB/disk enrichment.

    Uses a lazy-ingest pattern (issue #1160): when run_ingested_at is NULL and
    a disk artifact exists, ingest it into the DB first so all subsequent reads
    are served from SQLite only — no dual render-time read path.
    """
    label = row.get("label")
    if not row.get("run_ingested_at"):
        state = _read_state_file(sprints_dirs, label)
        if state is not None:
            try:
                summary_path = _find_summary_path(sprints_dirs, label)
                _db().ingest_sprint_run_artifact(
                    label, state,
                    project=row.get("project") or "",
                    summary_path=summary_path,
                )
                refreshed = _db().get_sprint(label)
                if refreshed:
                    row = refreshed
            except Exception:
                pass
    from . import sprint_artifact_service  # noqa: PLC0415
    if row.get("run_ingested_at"):
        enrich = sprint_artifact_service.enrichment_from_db_row(row)
    else:
        enrich = sprint_artifact_service.enrichment_from_db_row({})
    # Plan files are not run artifacts and are never ingested — read them at
    # render time to supply bulk_complete lifecycle signals (plan_status,
    # plan-derived end_reason) that have no DB column.
    plan = _read_plan_file(sprints_dirs, label) or {}
    if not enrich.get("plan_status"):
        enrich["plan_status"] = plan.get("state") or plan.get("status")
    duration = _seconds_between(row.get("started_at"), row.get("ended_at"))
    if duration is None:
        duration = enrich["duration"]
    import sprint_state  # noqa: PLC0415 — top-level canonical accessor (issue #1692)
    _raw_lifecycle = sprint_state.current(label, row.get("project"))
    lifecycle_state = (
        _normalize_state(row.get("state"))
        if _raw_lifecycle is None or _raw_lifecycle == "unknown"
        else _raw_lifecycle
    )
    end_reason = row.get("end_reason") or enrich.get("end_reason") or plan.get("end_reason")
    issues = enrich["issues"]
    lifecycle_state = _lifecycle_display_state(lifecycle_state, end_reason, issues)
    pr_number = row.get("pr_number") if row.get("pr_number") is not None else enrich["pr_number"]
    # Prefer the durable `sprints` columns (written by the finish flow) over the
    # state file, so PR/Summary links on the ledger card don't go missing when an
    # older state.json lacks them.
    summary_issue_url = row.get("summary_issue_url") or enrich["summary_issue_url"]
    summary_issue_num = (
        _parse_issue_num_from_url(summary_issue_url)
        if summary_issue_url else enrich["summary_issue_num"]
    )
    summary_path = row.get("summary_path") or enrich["summary_path"]
    return {
        "label": label,
        "project": row.get("project", ""),
        "lifecycle_state": lifecycle_state,
        "end_reason": end_reason,
        "duration": duration,
        "tokens": enrich["tokens"],
        "estimate_accuracy": enrich["estimate_accuracy"],
        "pr_number": pr_number,
        "summary_path": summary_path,
        "summary_issue_url": summary_issue_url,
        "summary_issue_num": summary_issue_num,
        "reconciliation": enrich["reconciliation"],
        "issues": enrich["issues"],
        "failed_tickets": enrich["failed_tickets"],
        "failure_reason": enrich["failure_reason"],
        "plan_status": enrich.get("plan_status"),
        "post_sprint": enrich.get("post_sprint"),
        "_sort_key": row.get("ended_at") or row.get("started_at") or row.get("created_at") or "",
        "ended_at": row.get("ended_at"),
        "_source": "lifecycle",
    }


def _record_from_files(label: str, sprints_dirs: Path | list[Path]) -> dict:
    """Build a response row purely from on-disk state/plan files (last resort)."""
    enrich = _enrich_from_state(label, sprints_dirs)
    plan = _read_plan_file(sprints_dirs, label) or {}
    state_raw = plan.get("status") or plan.get("state")
    # Sort chronologically like the DB-backed builders (ISO timestamps), NOT by
    # the raw label string — otherwise "sprint-999"/"sprint-8" sort lexically
    # above "sprint-69" and float to the top of History (issue: sprint-69 buried
    # under older runs). Prefer plan/state timestamps; fixtures with no timestamp
    # fall back to "" and sort to the bottom under reverse=True.
    state_file = _read_state_file(sprints_dirs, label) or {}
    sort_key = (
        plan.get("ended_at")
        or plan.get("started_at")
        or state_file.get("start_timestamp")
        or ""
    )
    lifecycle_state = _lifecycle_display_state(
        _normalize_state(state_raw) if state_raw else "unknown",
        enrich.get("end_reason"),
        enrich["issues"],
    )
    return {
        "label": label,
        "project": plan.get("project", ""),
        "lifecycle_state": lifecycle_state,
        "end_reason": enrich.get("end_reason"),
        "duration": enrich["duration"],
        "tokens": enrich["tokens"],
        "estimate_accuracy": enrich["estimate_accuracy"],
        "pr_number": enrich["pr_number"],
        "summary_path": enrich["summary_path"],
        "summary_issue_url": enrich["summary_issue_url"],
        "summary_issue_num": enrich["summary_issue_num"],
        "reconciliation": enrich["reconciliation"],
        "issues": enrich["issues"],
        "failed_tickets": enrich["failed_tickets"],
        "failure_reason": enrich["failure_reason"],
        "plan_status": enrich.get("plan_status"),
        "post_sprint": enrich.get("post_sprint"),
        "_sort_key": sort_key,
        "_source": "files",
    }


# Real sprint labels only — sprint-N or sprint-N.M[.K…]. Keeps sibling
# artifacts (sprint-1-estimate.json, sprint-1-preflight-<date>.json, plan
# files, test debris) from surfacing as zombie History rows.
_LABEL_RE = re.compile(r"^sprint-\d+(?:\.\d+)*$")
_SUMMARY_TITLE_NUM_RE = re.compile(r"^Sprint (\d+(?:\.\d+)*)\s+Executive Summary$")


def _label_sub_index(label: str | None) -> int:
    m = _LABEL_RE.match(label or "")
    if not m:
        return 0
    parts = (label or "").split(".")
    return int(parts[1]) if len(parts) > 1 else 0


def _label_base(label: str | None) -> str:
    m = re.match(r"^(sprint-\d+)", label or "")
    return m.group(1) if m else (label or "")


# Lifecycle authority when deduping competing rows for the same (label, project).
_SETTLED_MERGE_STATES = frozenset({"completed", "deleted"})
_MERGE_STATE_RANK: dict[str, int] = {
    "deleted": 100,
    "completed": 90,
    "ready_to_merge": 50,
    "needs_rework": 40,
    "failed": 35,
    "partial_finished": 30,
    "running": 25,
    "planned": 20,
    "draft": 15,
    "unknown": 10,
    "cancelled": 10,
}
_ACTIVE_RERUN_STATES = frozenset({"draft", "planned", "running", "ready_to_merge"})


def _record_recency(rec: dict) -> str:
    for field in ("_sort_key", "ended_at", "started_at", "created_at"):
        val = rec.get(field)
        if val:
            return str(val)
    return ""


def _merge_state_rank(rec: dict) -> int:
    return _MERGE_STATE_RANK.get((rec.get("lifecycle_state") or "").lower(), 0)


def _merge_history_record(existing: dict | None, new: dict) -> dict:
    """Pick the winning row when multiple sources share (label, project).

    Settled ``completed``/``deleted`` rows beat stale history snapshots unless
    the challenger is clearly a newer lifecycle rerun (sprint-99 guard).
    """
    if existing is None:
        return new
    st_e = (existing.get("lifecycle_state") or "").lower()
    st_n = (new.get("lifecycle_state") or "").lower()
    rec_e = _record_recency(existing)
    rec_n = _record_recency(new)

    if st_e in _SETTLED_MERGE_STATES and st_n not in _SETTLED_MERGE_STATES:
        return existing
    if st_n in _SETTLED_MERGE_STATES and st_e not in _SETTLED_MERGE_STATES:
        return new

    src_e = existing.get("_source") or ""
    src_n = new.get("_source") or ""
    if (
        src_e == "history"
        and src_n == "lifecycle"
        and rec_n >= rec_e
        and st_n in _ACTIVE_RERUN_STATES
    ):
        return new

    rank_e = _merge_state_rank(existing)
    rank_n = _merge_state_rank(new)
    if rank_n != rank_e:
        return new if rank_n > rank_e else existing
    return new if rec_n >= rec_e else existing


# Child states that close the partial_finished chain (sprint-lifecycle.md): a parent
# stays partial_finished until every descendant reaches Merge Sprint (completed).
# ready_to_merge means "run done, awaiting sign-off" — not chain-complete.
_CHILD_SETTLED_STATES = frozenset({"completed", "deleted"})


def _union_planned_roster(rec: dict, sprints_dirs: Path | list[Path]) -> None:
    """Add plan.json planned tickets missing from a running sprint's issue list.

    Queued-but-not-yet-dispatched tickets have no agent_runs, so the agent-runs
    rebuild drops them. Add them as ``open``/queued placeholders so History
    matches the live Running roster. In-place; no-op when no plan.json roster.
    """
    plan = _read_plan_file(sprints_dirs, rec.get("label") or "") or {}
    raw = plan.get("tickets") if isinstance(plan, dict) else None
    if not isinstance(raw, list):
        return
    planned: list[int] = []
    for n in raw:
        try:
            planned.append(int(n))
        except (TypeError, ValueError):
            continue
    if not planned:
        return
    have = {i.get("ticket_id") for i in (rec.get("issues") or [])}
    issues = rec.setdefault("issues", [])
    for num in planned:
        if num not in have:
            issues.append({
                "ticket_id": num,
                "state": "open",
                "time_spent": None,
                "pr_number": None,
                "queued": True,
            })


# Actionable lifecycle states for the active_only History inbox (sprint-lifecycle
# redesign). Finished work awaiting sign-off, failures, and partial_finished
# lineage parents. ``running`` uses the Running tab; ``draft``/``planned`` are
# pre-run board states — never History inbox rows.
_ACTIONABLE_STATES = frozenset({
    "ready_to_merge", "needs_rework", "failed", "partial_finished",
})
# Optional context tail on active_only — completed/deleted only (never running).
_CLOSED_TAIL_STATES = frozenset({"completed", "deleted"})


def _finalize_lineage(records: list[dict]) -> None:
    """Cross-record lifecycle rollup (in-place): rerun-child flags + derived
    partial_finished/completed states.

    Needs the WHOLE lineage (a parent's state depends on its children), so it
    runs on the full record set BEFORE any windowing. Deliberately uses ONLY
    labels + lifecycle_state + failed_tickets + plan_status — never agent_runs or
    the per-record issue list — so the expensive issue enrichment can be deferred
    to the visible window (_finalize_issues).

    The pre-redesign failed-state heuristics are retired: needs_rework is written
    at the source and legacy rows render through the display mapping. The one
    promotion kept is a fact, not a guess: a row whose run recorded failed
    tickets is needs_rework. `partial_finished` is derived here, never stored.
    """
    children_by_base: dict[str, list[str]] = {}
    for rec in records:
        label = rec.get("label") or ""
        base = _label_base(label)
        if _label_sub_index(label) > 0:
            children_by_base.setdefault(base, []).append(label)

    state_by_label: dict[str, str] = {}
    for rec in records:
        label = rec.get("label") or ""
        base = _label_base(label)
        siblings = sorted(children_by_base.get(base, []), key=_label_sub_index)
        sub = _label_sub_index(label)
        rec["has_rerun_child"] = any(_label_sub_index(c) > sub for c in siblings)
        if sub == 0:
            rec["has_rerun_child"] = bool(siblings)

        _clear_stale_failure_signals(rec)

        # Failed tickets are recorded facts — unless the sprint is already settled.
        if rec.get("failed_tickets") and rec.get("lifecycle_state") not in (
            "running", "deleted", "completed", "ready_to_merge",
        ):
            rec["lifecycle_state"] = "needs_rework"
            if not rec.get("failure_reason"):
                rec["failure_reason"] = rec["failed_tickets"][-1].get("failure_reason")

        plan_st_raw = rec.pop("plan_status", None)
        if (
            _normalize_state(plan_st_raw or "") == "completed"
            and (rec.get("end_reason") or "") == "bulk_complete"
        ):
            rec["lifecycle_state"] = "completed"

        state_by_label[label] = rec.get("lifecycle_state") or "unknown"

    # Derived partial_finished pass — walk deepest children first so promotions
    # to completed update state_by_label before ancestors are evaluated.
    partial_order = sorted(
        records,
        key=lambda r: _label_sub_index(r.get("label") or ""),
        reverse=True,
    )
    for rec in partial_order:
        label = rec.get("label") or ""
        if not rec.get("has_rerun_child"):
            continue
        own = rec.get("lifecycle_state") or ""
        if own in ("running", "deleted"):
            continue
        sub = _label_sub_index(label)
        descendants = [
            c for c in children_by_base.get(_label_base(label), [])
            if _label_sub_index(c) > sub
        ]
        unsettled = [
            c for c in descendants
            if state_by_label.get(c, "unknown") not in _CHILD_SETTLED_STATES
        ]
        if unsettled:
            rec["lifecycle_state"] = "partial_finished"
            rec["partial_children"] = sorted(unsettled, key=_label_sub_index)
        elif descendants and own in ("needs_rework", "partial_finished"):
            # Superseded parent only — not a sibling still at ready_to_merge.
            if all(
                state_by_label.get(c, "unknown") in _CHILD_SETTLED_STATES
                for c in descendants
            ):
                rec["lifecycle_state"] = "completed"
                state_by_label[label] = "completed"


def _finalize_issues(
    records: list[dict],
    sprints_dirs: Path | list[Path],
    title_map: dict | None = None,
    ran_by_label: dict[str, set[int]] | None = None,
) -> None:
    """Per-record issue enrichment (in-place).

    Safe to run on the visible WINDOW only for disk/file reads. Pass
    *ran_by_label* from the full sprint list (before pagination) so lineage
    sibling attribution sees reruns outside the window.

    Steps: fill the issue list from agent_runs when empty, union the running
    roster, fill missing PR/summary links, attribute tickets to the sprint that
    actually ran them, drop cross-project leakage, then backfill blank titles
    from `title_map` ({issue_number: title}, sourced from the local issues mirror
    — no GitHub call) so completed sprints whose rows are synthesized from
    agent_runs still show ticket text.
    """
    for rec in records:
        label = rec.get("label") or ""
        if not rec.get("issues"):
            run_issues = _issues_from_agent_runs(label)
            if run_issues:
                rec["issues"] = run_issues
        # A running sprint's list is rebuilt from agent_runs (dispatched tickets
        # only); union plan.json's planned roster as queued placeholders so
        # History matches the live Running view.
        if (rec.get("lifecycle_state") or "") == "running":
            _union_planned_roster(rec, sprints_dirs)

    _fill_missing_links(records, sprints_dirs)
    _reconcile_issue_outcomes_with_agent_runs(records)
    _attribute_issues_to_runs(records, ran_by_label)
    _drop_cross_project_issues(records)

    db_mod = _db()
    for rec in records:
        project_key = (rec.get("project") or "").strip()
        for iss in rec.get("issues") or []:
            if (iss.get("title") or "").strip():
                continue
            tid = iss.get("ticket_id")
            if tid is None:
                continue
            t = None
            if title_map:
                try:
                    t = title_map.get(int(tid))
                except (TypeError, ValueError):
                    pass
                if not t:
                    t = title_map.get(tid)
            if not t and project_key:
                try:
                    row = db_mod.get_mirrored_issue(project_key, int(tid))
                    if row and row.get("title"):
                        t = row["title"]
                except Exception:
                    pass
            if t:
                iss["title"] = t

    # Honesty gate: a sprint shown completed / ready_to_merge whose OWN open work
    # tickets still carry rework / SIT / non-DONE labels is not actually done —
    # downgrade to needs_rework so the card offers Re-run, not Complete (a
    # completed sprint can hide unfixed needs-rework/SIT tickets). Mirror-backed:
    # _has_rework_tickets reads the local issues mirror, so no GitHub calls. Runs
    # on the visible window only.
    try:
        import server as _srv  # noqa: PLC0415
        _has_rework = getattr(_srv, "_has_rework_tickets", None)
    except Exception:
        _has_rework = None
    if _has_rework is not None:
        for rec in records:
            if (rec.get("lifecycle_state") or "").lower() not in ("completed", "ready_to_merge"):
                continue
            label = (rec.get("label") or "").strip()
            project = (rec.get("project") or "").strip()
            if not label or not project:
                continue
            try:
                if _has_rework(label, project):
                    rec["lifecycle_state"] = "needs_rework"
                    if not rec.get("end_reason"):
                        rec["end_reason"] = "ticket-rework"
            except Exception:
                pass


def _finalize_records(records: list[dict], sprints_dirs: Path | list[Path],
                      title_map: dict | None = None) -> None:
    """Back-compat full finalize: lineage rollup + per-record issue enrichment on
    the SAME records. get_sprint_history splits these so the issue pass runs on
    the window only; this wrapper keeps existing callers/tests working.
    """
    _finalize_lineage(records)
    _finalize_issues(records, sprints_dirs, title_map=title_map)


def _filter_active_records(records: list[dict], keep_completed: int = 0) -> list[dict]:
    """Action inbox: actionable sprints only (+ optional recent completed tail).

    Runs AFTER _finalize_lineage. When any lineage member is actionable, every
    row sharing that base label is kept so the UI renders one parent group
    (sprint-97 + 97.5) instead of orphan child cards beside unrelated sprints.
    """
    by_label = {r.get("label"): r for r in records if r.get("label")}
    actionable_bases: set[str] = set()
    for rec in records:
        st = (rec.get("lifecycle_state") or "").lower()
        if st in _ACTIONABLE_STATES:
            actionable_bases.add(_label_base(rec.get("label") or ""))

    include_labels: set[str] = set()
    for lbl, rec in by_label.items():
        st = (rec.get("lifecycle_state") or "").lower()
        if st in _ACTIONABLE_STATES:
            include_labels.add(lbl)
    for base in actionable_bases:
        for lbl in by_label:
            if _label_base(lbl) == base:
                include_labels.add(lbl)

    inbox = [r for r in records if r.get("label") in include_labels]
    if keep_completed <= 0:
        return inbox

    closed = [
        r for r in records
        if (r.get("lifecycle_state") or "").lower() in _CLOSED_TAIL_STATES
        and r.get("label") not in include_labels
    ]
    closed.sort(key=lambda r: r.get("_sort_key") or "", reverse=True)
    return inbox + closed[:keep_completed]


def _build_ran_by_label(records: list[dict]) -> dict[str, set[int]]:
    """Map sprint label → issue numbers with agent_runs under that label."""
    ran_by_label: dict[str, set[int]] = {}
    for rec in records:
        label = rec.get("label")
        if not label:
            continue
        try:
            rows = _db().agent_runs_for_sprint(label, project=rec.get("project") or None)
        except Exception:
            rows = []
        nums: set[int] = set()
        for r in rows:
            try:
                n = int(r.get("issue_number"))
            except (TypeError, ValueError):
                continue
            if n > 0:
                nums.add(n)
        ran_by_label[label] = nums
    return ran_by_label


def _ran_in_later_lineage_siblings(
    label: str,
    ran_by_label: dict[str, set[int]],
) -> set[int]:
    """Issue numbers that ran in a later rerun sibling (sprint-N.M, same base N).

    Rerun children are flat siblings (sprint-97.1, sprint-97.2, …), not nested
    under sprint-97.1.* — so ``label + "."`` prefix matching was wrong.
    """
    base = _label_base(label)
    sub = _label_sub_index(label)
    out: set[int] = set()
    for other, nums in ran_by_label.items():
        if other == label or _label_base(other) != base:
            continue
        if _label_sub_index(other) > sub:
            out |= nums
    return out


def _attribute_issues_to_runs(
    records: list[dict],
    ran_by_label: dict[str, set[int]] | None = None,
) -> None:
    """Filter each sprint's issue list to the tickets it actually owns.

    A sprint owns a ticket only if that ticket actually RAN under its label
    (agent_runs) AND did not re-run in a later lineage sibling (where it now
    belongs). This fixes two long-standing History mismatches:
      - a parent listing tickets that moved to a child (e.g. sprint-63 showing
        #572/#574 after they re-ran in sprint-63.1), and
      - a child listing carried-over already-done tickets it never re-ran (e.g.
        sprint-90.1's ingested roster of 7 when only #1338/#1340 actually ran).

    Pass *ran_by_label* built from the full sprint list (not just the paginated
    window) so a visible sprint-97.1 row still knows #867 re-ran in sprint-97.4.

    Sprints with no agent_runs at all (legacy / file-only) are left untouched so
    we never blank a sprint that simply predates run tracking.
    """
    if ran_by_label is None:
        ran_by_label = _build_ran_by_label(records)

    for rec in records:
        label = rec.get("label") or ""
        ran_here = ran_by_label.get(label) or set()
        if not ran_here:
            continue  # no run record — leave the list as-is (legacy sprints)
        ran_in_later = _ran_in_later_lineage_siblings(label, ran_by_label)
        # A running sprint keeps its full planned roster (incl. queued tickets
        # unioned in above) so History matches the live view; only finished
        # sprints are narrowed to tickets that actually ran under this label.
        is_running = (rec.get("lifecycle_state") or "") == "running"
        rec["issues"] = [
            i for i in (rec.get("issues") or [])
            if i.get("ticket_id") not in ran_in_later
            and (is_running or i.get("ticket_id") in ran_here)
        ]


def _drop_cross_project_issues(records: list[dict]) -> None:
    """Remove tickets that don't belong to the sprint's project (Tier 1).

    Sprint labels are unique only per repo, and agent_runs / ingested issues_json
    are keyed by label alone — so a sprint can pick up tickets from another
    project's same-numbered sprint (e.g. perf-coach sprint-64 showing commander's
    #839-844). Filter each sprint's issue list to numbers present in THIS
    project's issue mirror (repo-scoped). Skipped when the project's mirror is
    empty so we never blank a sprint just because its mirror hasn't synced.
    """
    mirror_by_project: dict[str, set[int]] = {}
    for rec in records:
        project = rec.get("project") or ""
        if not project or project in mirror_by_project:
            continue
        nums: set[int] = set()
        try:
            for i in _db().get_mirrored_issues(project):
                n = i.get("number", i.get("issue_number"))
                try:
                    n = int(n)
                except (TypeError, ValueError):
                    continue
                if n > 0:
                    nums.add(n)
        except Exception:
            nums = set()
        mirror_by_project[project] = nums

    for rec in records:
        project = rec.get("project") or ""
        owned = mirror_by_project.get(project)
        if not owned:
            continue  # no mirror for this project — don't risk blanking the list
        rec["issues"] = [
            i for i in (rec.get("issues") or [])
            if i.get("ticket_id") in owned
        ]


def _pr_from_issues(issues: list | None) -> int | None:
    """First ticket PR on the sprint row, when the sprint PR field is empty."""
    for iss in issues or []:
        pr = iss.get("pr_number")
        if pr is None:
            continue
        try:
            return int(pr)
        except (TypeError, ValueError):
            continue
    return None


def _links_from_events(project: str, label: str) -> dict:
    """PR + summary issue numbers from the events feed (same source as daily brief)."""
    out: dict = {"pr_number": None, "summary_issue_num": None}
    if not project or not label:
        return out
    try:
        with _db().get_conn() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT detail FROM events WHERE project = ? AND target = ? "
                "ORDER BY timestamp DESC LIMIT 50",
                (project, label),
            ).fetchall()]
    except Exception:
        return out
    for ev in rows:
        data = ev.get("detail")
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except (ValueError, TypeError):
                data = None
        if not isinstance(data, dict):
            continue
        if out["pr_number"] is None and data.get("pr_number") is not None:
            out["pr_number"] = data["pr_number"]
        if out["summary_issue_num"] is None and data.get("summary_issue_number") is not None:
            out["summary_issue_num"] = data["summary_issue_number"]
    return out


def _github_summary_by_label(project: str) -> dict[str, dict]:
    """Map sprint label → {number, url} from cached GitHub sprint-summary issues."""
    if not project:
        return {}
    try:
        import github_client  # noqa: PLC0415
        issues = github_client.list_summary_issues(repo_name=project)
    except Exception:
        return {}
    result: dict[str, dict] = {}
    for iss in issues:
        m = _SUMMARY_TITLE_NUM_RE.match(iss.get("title", "") or "")
        if not m:
            continue
        label = f"sprint-{m.group(1)}"
        prev = result.get(label)
        if prev is None or (iss.get("number") or 0) > (prev.get("number") or 0):
            result[label] = {
                "number": iss.get("number"),
                "url": iss.get("url"),
            }
    return result


def _fill_missing_links(records: list[dict], sprints_dirs: Path | list[Path]) -> None:
    """Backfill PR / summary targets from events, GitHub cache, and parents (DB-only; no disk reads)."""
    by_label = {r.get("label"): r for r in records if r.get("label")}
    github_by_project: dict[str, dict[str, dict]] = {}

    for rec in records:
        label = rec.get("label")
        if not label:
            continue

        if rec.get("pr_number") is None:
            rec["pr_number"] = _pr_from_issues(rec.get("issues"))

        if not rec.get("summary_issue_num") and rec.get("summary_issue_url"):
            rec["summary_issue_num"] = _parse_issue_num_from_url(rec["summary_issue_url"])

        project = rec.get("project") or ""
        if rec.get("pr_number") is None or rec.get("summary_issue_num") is None:
            meta = _links_from_events(project, label)
            if rec.get("pr_number") is None and meta.get("pr_number") is not None:
                rec["pr_number"] = meta["pr_number"]
            if rec.get("summary_issue_num") is None and meta.get("summary_issue_num") is not None:
                rec["summary_issue_num"] = meta["summary_issue_num"]
                if not rec.get("summary_issue_url") and project:
                    rec["summary_issue_url"] = (
                        f"https://github.com/{project}/issues/{meta['summary_issue_num']}"
                    )

        if rec.get("summary_issue_num") is None and project:
            if project not in github_by_project:
                github_by_project[project] = _github_summary_by_label(project)
            gh_sum = github_by_project[project].get(label)
            if gh_sum:
                rec["summary_issue_num"] = gh_sum.get("number")
                if not rec.get("summary_issue_url") and gh_sum.get("url"):
                    rec["summary_issue_url"] = gh_sum["url"]

    for rec in records:
        if rec.get("pr_number") is not None:
            continue
        label = rec.get("label") or ""
        base = _label_base(label)
        if base == label:
            continue
        parent = by_label.get(base)
        if parent and parent.get("pr_number") is not None:
            rec["pr_number"] = parent["pr_number"]


def _discover_file_labels(sprints_dirs: Path | list[Path]) -> set[str]:
    """Sprint labels that have a state.json or plan.json on disk."""
    dirs = [sprints_dirs] if isinstance(sprints_dirs, Path) else list(sprints_dirs)
    labels: set[str] = set()
    for sprints_dir in dirs:
        if not sprints_dir.exists():
            continue
        for p in sprints_dir.glob("*-state.json"):
            labels.add(p.name[: -len("-state.json")])
        for p in sprints_dir.glob("*-plan.json"):
            labels.add(p.name[: -len("-plan.json")])
        for p in sprints_dir.glob("sprint-*.json"):
            if p.name.endswith("-state.json") or p.name.endswith("-plan.json"):
                continue
            labels.add(p.stem)
    return {lbl for lbl in labels if _LABEL_RE.match(lbl)}


def _resolve_sprint_project(
    label: str,
    declared: str,
    sprints_dirs: Path | list[Path],
    db_module,
    scope_project: str | None = None,
) -> str:
    """Infer owner/repo when the sprints row was ingested without project (child reruns)."""
    declared_clean = (declared or "").strip()
    scope = (scope_project or "").strip()
    # Never reassign a row already owned by another project (perf-coach sprint-77
    # must not appear in commander History because commander has a同名 plan file).
    if declared_clean and scope and declared_clean != scope:
        return ""
    if declared_clean and (not scope or declared_clean == scope):
        return declared_clean

    def _project_in_scope(proj: str) -> bool:
        if not proj:
            return False
        return not scope or proj == scope

    plan = _read_plan_file(sprints_dirs, label) or {}
    proj = (plan.get("project") or "").strip()
    if _project_in_scope(proj):
        return proj
    state = _read_state_file(sprints_dirs, label) or {}
    proj = (state.get("project") or "").strip()
    if _project_in_scope(proj):
        return proj
    parent = (plan.get("parent") or "").strip()
    if parent:
        prow = db_module.get_sprint(parent, project=scope_project)
        if prow and (prow.get("project") or "").strip():
            return prow["project"].strip()
    row = db_module.get_sprint(label, project=scope_project)
    if row:
        parent = (row.get("parent_label") or "").strip()
        if parent:
            prow = db_module.get_sprint(parent, project=scope_project)
            if prow and (prow.get("project") or "").strip():
                return prow["project"].strip()
    return ""


# ── public API ────────────────────────────────────────────────────────────────

def get_sprint_history(offset: int = 0, limit: int = 20, sprints_dir: Path | None = None,
                       project: str | None = None, active_only: bool = False) -> dict:
    """Return paginated, enriched sprint-history rows. No GitHub calls (AC5).

    ``project`` (owner/repo) scopes the ledger to one project — without it the
    board showed every project's sprints plus project-less junk rows.

    ``active_only`` returns the action inbox: ready_to_merge / needs_rework /
    failed / partial_finished lineage groups (plus optional recent completed
    tail when keep_completed > 0). Running, draft, and planned are excluded.
    """
    search_dirs = _as_sprints_dirs(sprints_dir, project)
    offset = max(0, int(offset))
    limit = max(0, int(limit))
    db = _db()

    records: list[dict] = []
    candidates: list[dict] = []
    db_backed_labels: set[str] = set()

    def _key(label, proj) -> tuple[str, str]:
        return (label or "", (proj or "").strip())

    for rec in db.list_sprint_history():
        lbl = rec.get("label") or ""
        if lbl:
            db_backed_labels.add(lbl)
        candidates.append(_record_from_history(rec))

    for row in db.list_sprints_lifecycle():
        lbl = row.get("label") or ""
        if lbl:
            db_backed_labels.add(lbl)
        candidates.append(_record_from_lifecycle(row, search_dirs))

    for label in _discover_file_labels(search_dirs):
        if label in db_backed_labels:
            continue
        candidates.append(_record_from_files(label, search_dirs))

    if project:
        scoped: list[dict] = []
        for rec in candidates:
            cur = (rec.get("project") or "").strip()
            if cur and cur != project:
                continue  # hard exclude — never resolve across projects
            if not cur:
                resolved = _resolve_sprint_project(
                    rec.get("label") or "",
                    cur,
                    search_dirs,
                    db,
                    scope_project=project,
                )
                if resolved:
                    rec["project"] = resolved
            if rec.get("project") == project:
                scoped.append(rec)
        candidates = scoped

    by_key: dict[tuple[str, str], dict] = {}
    for rec in candidates:
        key = _key(rec.get("label"), rec.get("project"))
        by_key[key] = _merge_history_record(by_key.get(key), rec)
    records = list(by_key.values())

    # Lineage rollup needs every record (a parent's state depends on its
    # children), so run it on the full set. The expensive per-record issue
    # enrichment (agent_runs queries + disk) is deferred to the visible window
    # below — that was the dominant History cost when run for every sprint.
    _finalize_lineage(records)

    # Agent-run map for the whole project feed — siblings outside the paginated
    # window must still suppress tickets on earlier lineage runs (sprint-97.1 vs
    # sprint-97.4).
    ran_by_label = _build_ran_by_label(records)

    if active_only:
        records = _filter_active_records(records)

    records.sort(key=lambda r: r.get("_sort_key") or "", reverse=True)
    total = len(records)
    window = records[offset:offset + limit] if limit else records[offset:]

    # Titles for synthesized (agent_runs) issue rows come from the local issues
    # mirror — one query, no GitHub call — so completed sprints still show text.
    title_map: dict[int, str] = {}
    if project:
        try:
            for mi in db.get_mirrored_issues(project):
                n = mi.get("number")
                if n is not None and mi.get("title"):
                    title_map[int(n)] = mi["title"]
        except Exception:
            pass

    _finalize_issues(window, search_dirs, title_map=title_map, ran_by_label=ran_by_label)

    for r in window:
        r.pop("_sort_key", None)
        r.pop("_source", None)
        r.pop("ended_at", None)

    return {"sprints": window, "offset": offset, "limit": limit, "total": total}


def record_deleted_sprint(
    label: str,
    project: str,
    issues: list[dict] | None,
    commander_dir: Path | None = None,
    end_reason: str = "deleted via dashboard",
) -> None:
    """Persist a ``state='deleted'`` history snapshot (issue #805, AC7).

    Called from the delete-sprint handler BEFORE any label is stripped, so the
    deleted sprint is queryable via :func:`get_sprint_history` immediately after
    deletion (AC8). ``issues`` is the raw GitHub-issue list for the sprint; it is
    normalized to the AC3 shape. When the sprint's ``<label>-state.json`` is
    still on disk it enriches per-ticket timing, tokens and duration.

    Best-effort: any failure is swallowed so the deletion itself never breaks.
    """
    try:
        db = _db()
        snapshot = [_normalize_issue(i) for i in (issues or [])]

        tokens = duration = estimate_accuracy = None
        summary_path = None
        pr_number = None
        if commander_dir is not None:
            sprints_dir = Path(commander_dir) / "sprints"
            enrich = _enrich_from_state(label, sprints_dir)
            tokens = enrich["tokens"]
            duration = enrich["duration"]
            estimate_accuracy = enrich["estimate_accuracy"]
            summary_path = enrich["summary_path"]
            pr_number = enrich["pr_number"]
            # Prefer the richer per-ticket data from the state file when present.
            if enrich["issues"]:
                by_id = {i["ticket_id"]: i for i in enrich["issues"]}
                for snap in snapshot:
                    match = by_id.get(snap["ticket_id"])
                    if match and snap.get("time_spent") is None:
                        snap["time_spent"] = match.get("time_spent")

        db.record_sprint_history(
            label=label,
            project=project or "",
            lifecycle_state="deleted",
            end_reason=end_reason,
            duration=duration,
            tokens=tokens,
            estimate_accuracy=estimate_accuracy,
            pr_number=pr_number,
            summary_path=summary_path,
            issues=snapshot,
        )
    except Exception:
        # Never let a ledger write block a sprint deletion.
        pass
