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
    out = {
        "ticket_id": ticket_id,
        "state": _map_issue_state(iss.get("status") or iss.get("state")),
        "time_spent": iss.get("time_spent", _issue_time_spent(iss)),
        "pr_number": pr,
    }
    title = iss.get("title")
    if title:
        out["title"] = str(title)
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


def _issues_from_agent_runs(label: str) -> list[dict]:
    """Synthesize issue rows from agent_runs, deriving each ticket's disposition
    from its run outcomes.

    Used both as a fallback when state.json has no tickets AND to UNION in
    tickets that ran under this sprint but were dropped from the latest state
    file (e.g. merged in an earlier run of the same sprint) so the History ledger
    matches the Board rather than hiding successes.
    """
    try:
        rows = _db().agent_runs_for_sprint(label)
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
    out["failed_tickets"] = _failed_tickets_from_raw(issues_raw)
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


def _lifecycle_display_state(lifecycle: str, end_reason: str | None, issues: list[dict]) -> str:
    """Correct needs_rework rows that are successful natural ends (issue #1137)."""
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


# ── record builders ───────────────────────────────────────────────────────────

def _record_from_history(rec: dict) -> dict:
    """Build the response row from a sprint_history snapshot (authoritative)."""
    issues = [_normalize_issue(i) if "ticket_id" not in i else i for i in rec.get("issues", [])]
    failed_tickets = [
        {"ticket_id": i.get("ticket_id"), "failure_reason": i.get("failure_reason") or "Agent failed"}
        for i in issues
        if i.get("failure_reason") or (i.get("agent_status") or "").lower() == "failed"
    ]
    return {
        "label": rec.get("label"),
        "project": rec.get("project", ""),
        "lifecycle_state": _normalize_state(rec.get("lifecycle_state")),
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
    }


def _record_from_lifecycle(row: dict, sprints_dirs: Path | list[Path]) -> dict:
    """Build the response row from a `sprints` lifecycle row + DB/disk enrichment."""
    label = row.get("label")
    if row.get("run_ingested_at"):
        from . import sprint_artifact_service  # noqa: PLC0415
        enrich = sprint_artifact_service.enrichment_from_db_row(row)
    else:
        enrich = _enrich_from_state(label, sprints_dirs)
    duration = _seconds_between(row.get("started_at"), row.get("ended_at"))
    if duration is None:
        duration = enrich["duration"]
    from . import sprint_state  # noqa: PLC0415
    lifecycle_state = sprint_state.current(label) or _normalize_state(row.get("state"))
    end_reason = row.get("end_reason") or enrich.get("end_reason")
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
    return {
        "label": label,
        "project": plan.get("project", ""),
        "lifecycle_state": _normalize_state(state_raw) if state_raw else "unknown",
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


# Child states that close the partial_finished chain (sprint-lifecycle.md): a parent
# stays partial_finished until every descendant reaches Merge Sprint (completed).
# ready_to_merge means "run done, awaiting sign-off" — not chain-complete.
_CHILD_SETTLED_STATES = frozenset({"completed", "deleted"})


def _finalize_records(records: list[dict], sprints_dirs: Path | list[Path]) -> None:
    """Attach rerun-child flags and derive lifecycle adjustments in-place.

    The pre-redesign failed-state heuristics (_infer_failed_lifecycle) are
    retired: needs_rework is now written at the source (sprint manager, cancel
    endpoint, orphan-PID sweeps) and legacy rows render through the display
    mapping. The one promotion kept is a fact, not a guess: a row whose run
    recorded failed tickets is needs_rework regardless of its stored state.

    `partial_finished` is derived here, never stored (sprint-lifecycle.md): a
    terminal sprint whose re-run children are not all completed shows
    partial_finished; when the last child completes the parent flips to
    completed automatically.
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
        if not rec.get("post_sprint"):
            # Pre-P3 rows: post_sprint may only exist on disk.
            state = _read_state_file(sprints_dirs, label)
            if state:
                rec["post_sprint"] = _build_post_sprint(state)

        if not rec.get("issues"):
            run_issues = _issues_from_agent_runs(label)
            if run_issues:
                rec["issues"] = run_issues

        base = _label_base(label)
        siblings = sorted(children_by_base.get(base, []), key=_label_sub_index)
        sub = _label_sub_index(label)
        rec["has_rerun_child"] = any(_label_sub_index(c) > sub for c in siblings)
        if sub == 0:
            rec["has_rerun_child"] = bool(siblings)

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

    _fill_missing_links(records, sprints_dirs)


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
    """Backfill PR / summary targets from disk, events, GitHub cache, and parents."""
    by_label = {r.get("label"): r for r in records if r.get("label")}
    github_by_project: dict[str, dict[str, dict]] = {}

    for rec in records:
        label = rec.get("label")
        if not label:
            continue

        disk = _enrich_from_state(label, sprints_dirs)
        if rec.get("pr_number") is None and disk.get("pr_number") is not None:
            rec["pr_number"] = disk["pr_number"]
        if not rec.get("summary_issue_url") and disk.get("summary_issue_url"):
            rec["summary_issue_url"] = disk["summary_issue_url"]
        if rec.get("summary_issue_num") is None and disk.get("summary_issue_num") is not None:
            rec["summary_issue_num"] = disk["summary_issue_num"]
        if not rec.get("summary_path") and disk.get("summary_path"):
            rec["summary_path"] = disk["summary_path"]

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
    return {l for l in labels if _LABEL_RE.match(l)}


def _resolve_sprint_project(
    label: str,
    declared: str,
    sprints_dirs: Path | list[Path],
    db_module,
) -> str:
    """Infer owner/repo when the sprints row was ingested without project (child reruns)."""
    if (declared or "").strip():
        return declared.strip()
    plan = _read_plan_file(sprints_dirs, label) or {}
    proj = (plan.get("project") or "").strip()
    if proj:
        return proj
    state = _read_state_file(sprints_dirs, label) or {}
    proj = (state.get("project") or "").strip()
    if proj:
        return proj
    parent = (plan.get("parent") or "").strip()
    if parent:
        prow = db_module.get_sprint(parent)
        if prow and (prow.get("project") or "").strip():
            return prow["project"].strip()
    row = db_module.get_sprint(label)
    if row:
        parent = (row.get("parent_label") or "").strip()
        if parent:
            prow = db_module.get_sprint(parent)
            if prow and (prow.get("project") or "").strip():
                return prow["project"].strip()
    return ""


# ── public API ────────────────────────────────────────────────────────────────

def get_sprint_history(offset: int = 0, limit: int = 20, sprints_dir: Path | None = None,
                       project: str | None = None) -> dict:
    """Return paginated, enriched sprint-history rows. No GitHub calls (AC5).

    ``project`` (owner/repo) scopes the ledger to one project — without it the
    board showed every project's sprints plus project-less junk rows.
    """
    search_dirs = _as_sprints_dirs(sprints_dir, project)
    offset = max(0, int(offset))
    limit = max(0, int(limit))
    db = _db()

    records: list[dict] = []
    seen_labels: set[str] = set()

    # 1) sprint_history snapshots (deleted etc.) — authoritative, take first.
    for rec in db.list_sprint_history():
        label = rec.get("label")
        if label in seen_labels:
            continue
        seen_labels.add(label)
        records.append(_record_from_history(rec))

    # 2) sprints lifecycle rows not already represented by a snapshot.
    for row in db.list_sprints_lifecycle():
        label = row.get("label")
        if label in seen_labels:
            continue
        seen_labels.add(label)
        records.append(_record_from_lifecycle(row, search_dirs))

    # 3) file-only sprints with no DB row at all.
    for label in _discover_file_labels(search_dirs):
        if label in seen_labels:
            continue
        seen_labels.add(label)
        records.append(_record_from_files(label, search_dirs))

    if project:
        for rec in records:
            if not (rec.get("project") or "").strip():
                rec["project"] = _resolve_sprint_project(
                    rec.get("label") or "",
                    rec.get("project") or "",
                    search_dirs,
                    db,
                )
        records = [r for r in records if r.get("project") == project]

    _finalize_records(records, search_dirs)

    records.sort(key=lambda r: r.get("_sort_key") or "", reverse=True)
    total = len(records)
    window = records[offset:offset + limit] if limit else records[offset:]
    for r in window:
        r.pop("_sort_key", None)

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
