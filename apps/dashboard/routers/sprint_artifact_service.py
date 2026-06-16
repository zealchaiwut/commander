"""Sprint run-artifact helpers (lifecycle P3).

Disk files under ``.commander/sprints/`` are write-once run artifacts ingested
into the ``sprints`` DB row at end-of-run. Render paths (outcome, history)
read the DB first and only fall back to disk for pre-P3 rows.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_LABEL_RE = re.compile(r"^sprint-\d+(?:\.\d+)*$")

_MERGED_STATUSES = {"done", "shipped", "merged", "passed", "complete", "completed", "uat"}
_CLOSED_STATUSES = {"skipped", "failed", "cancelled", "closed", "rejected", "blocked"}


def _sprint_roots(sprints_dir: Path) -> list[Path]:
    """Live sprints dir plus ``archive/`` when present (issue #735)."""
    roots = [sprints_dir]
    archive = sprints_dir / "archive"
    if archive.is_dir():
        roots.append(archive)
    return roots


def resolve_state_path(sprints_dir: Path, label: str) -> Path | None:
    """Return the state file for *label*, or None.

    Per-label files (``sprint-68.6-state.json``) take precedence. Legacy base
    sprint files (``sprint-68-state.json``) are used only when their embedded
    ``sprint_label`` matches or is absent. Archived copies under
    ``sprints/archive/`` are searched after the live directory.
    """
    if not sprints_dir.is_dir() or not _LABEL_RE.match(label):
        return None
    for root in _sprint_roots(sprints_dir):
        label_path = root / f"{label}-state.json"
        if label_path.is_file():
            return label_path
    m = re.match(r"^sprint-(\d+)", label)
    if not m:
        return None
    for root in _sprint_roots(sprints_dir):
        legacy = root / f"sprint-{m.group(1)}-state.json"
        if not legacy.is_file():
            continue
        try:
            data = json.loads(legacy.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return legacy
        recorded = data.get("sprint_label")
        if recorded and recorded != label:
            continue
        return legacy
    return None


def load_state_file(sprints_dir: Path, label: str) -> dict | None:
    path = resolve_state_path(sprints_dir, label)
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def _map_issue_state(raw: str | None) -> str:
    s = (raw or "").strip().lower()
    if s in _MERGED_STATUSES:
        return "merged"
    if s in _CLOSED_STATUSES:
        return "closed"
    return "open"


def _issue_time_spent(iss: dict) -> int | None:
    start = iss.get("coder_started_at") or iss.get("tester_started_at")
    end = iss.get("tester_finished_at") or iss.get("coder_finished_at")
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


def normalize_issue_row(iss: dict) -> dict:
    ticket_id = iss.get("number", iss.get("ticket_id", iss.get("issue_number")))
    pr = iss.get("pr_number")
    if pr is None and isinstance(iss.get("pr"), dict):
        pr = iss["pr"].get("number")
    agent_status = (iss.get("agent_status") or "").strip().lower() or None
    failure_reason = iss.get("failure_reason")
    out = {
        "ticket_id": ticket_id,
        "number": ticket_id,
        "title": iss.get("title", ""),
        "state": _map_issue_state(iss.get("status") or iss.get("state")),
        "time_spent": iss.get("time_spent", _issue_time_spent(iss)),
        "pr_number": pr,
    }
    if agent_status:
        out["agent_status"] = agent_status
    if failure_reason:
        out["failure_reason"] = str(failure_reason)
    return out


def _parse_issue_num_from_url(url: str | None) -> int | None:
    if not url:
        return None
    m = re.search(r"/issues/(\d+)", str(url))
    return int(m.group(1)) if m else None


def _parse_pr_number(state: dict) -> int | None:
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


def _compute_estimate_accuracy(state: dict) -> float | None:
    est_min = state.get("estimator_total_minutes")
    actual_secs = state.get("wall_clock_secs")
    if not est_min or not actual_secs:
        return None
    est_secs = float(est_min) * 60.0
    try:
        return round(float(actual_secs) / est_secs, 3) if est_secs else None
    except (ZeroDivisionError, TypeError, ValueError):
        return None


def build_post_sprint(state: dict) -> dict | None:
    """Post-sprint documenter + reviewer block (mirrors history service)."""
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


def find_summary_path(sprints_dir: Path, label: str) -> str | None:
    if not sprints_dir.is_dir():
        return None
    cands = sorted(
        sprints_dir.glob(f"{label}-summary-*.md"),
        key=lambda p: p.name,
        reverse=True,
    )
    if cands:
        return str(cands[0])
    m = re.match(r"^sprint-(\d+)", label)
    if m:
        legacy = sorted(
            sprints_dir.glob(f"sprint-{m.group(1)}-summary-*.md"),
            key=lambda p: p.name,
            reverse=True,
        )
        if legacy:
            return str(legacy[0])
    return None


def _compute_summary_counts(issues_raw: list[dict]) -> dict:
    """Compute denormalized summary counts from raw state-file issues.

    settled_done = total - not_yet_settled, where "not yet settled" means the
    issue was never dispatched to an agent (no agent action, status still in
    pending/in-progress/sit).  This matches _settled_done_from_columns applied
    to the same run data:
        settled = total - backlog(pending) - in_progress - sit

    An issue with agent_status set (completed/failed) or failure_reason set is
    always settled — the agent ran on it — regardless of the status field.

    uat_count    = issues with status 'uat'.
    failure_count = issues where agent_status=='failed' or failure_reason is set.
    """
    _NOT_SETTLED_STATUSES = frozenset({"pending", "in-progress", "sit"})

    total = len(issues_raw)
    not_settled = 0
    uat_count = 0
    failure_count = 0
    for iss in issues_raw:
        status = (iss.get("status") or "").lower()
        agent = (iss.get("agent_status") or "").lower()
        fr = iss.get("failure_reason")
        has_agent_action = agent in ("completed", "done", "failed") or bool(fr)
        if not has_agent_action and status in _NOT_SETTLED_STATUSES or (
            not has_agent_action and status == ""
        ):
            not_settled += 1
        if status == "uat":
            uat_count += 1
        if agent == "failed" or fr:
            failure_count += 1
    return {
        "summary_settled_done": max(0, total - not_settled),
        "summary_uat_count": uat_count,
        "summary_failure_count": failure_count,
    }


def artifact_fields_from_state(
    state: dict,
    *,
    summary_path: str | None = None,
    sprints_dir: Path | None = None,
) -> dict:
    """Map a sprint state dict to DB artifact column values."""
    issues_raw = state.get("issues") or []
    issues = [normalize_issue_row(i) for i in issues_raw]
    tin = state.get("total_tokens_in") or 0
    tout = state.get("total_tokens_out") or 0
    wc = state.get("wall_clock_secs")
    surl = state.get("summary_issue_url")
    spath = summary_path
    if spath is None and sprints_dir is not None:
        spath = find_summary_path(sprints_dir, state.get("sprint_label") or "")
    counts = _compute_summary_counts(issues_raw)
    return {
        "issues_json": json.dumps(issues),
        "tokens": int(tin) + int(tout),
        "wall_clock_secs": round(wc) if isinstance(wc, (int, float)) else None,
        "reconciliation_json": json.dumps(state["reconciliation"]) if state.get("reconciliation") else None,
        "summary_issue_url": surl,
        "summary_path": spath,
        "pr_number": _parse_pr_number(state),
        "post_sprint_json": json.dumps(ps) if (ps := build_post_sprint(state)) else None,
        "estimate_accuracy": _compute_estimate_accuracy(state),
        **counts,
    }


def enrichment_from_db_row(row: dict) -> dict:
    """Build history/outcome enrichment dict from an ingested sprints row."""
    issues: list[dict] = []
    try:
        issues = json.loads(row.get("issues_json") or "[]")
    except (ValueError, TypeError):
        issues = []
    issues_raw: list[dict] = []
    reconciliation = None
    if row.get("reconciliation_json"):
        try:
            reconciliation = json.loads(row["reconciliation_json"])
        except (ValueError, TypeError):
            pass
    post_sprint = None
    if row.get("post_sprint_json"):
        try:
            post_sprint = json.loads(row["post_sprint_json"])
        except (ValueError, TypeError):
            pass
    failed_tickets = [
        {"ticket_id": i.get("ticket_id"), "failure_reason": i.get("failure_reason") or "Agent failed"}
        for i in issues
        if i.get("failure_reason") or (i.get("agent_status") or "").lower() == "failed"
    ]
    surl = row.get("summary_issue_url")
    return {
        "issues": issues,
        "issues_raw": issues_raw,
        "tokens": row.get("tokens"),
        "duration": row.get("wall_clock_secs"),
        "estimate_accuracy": row.get("estimate_accuracy"),
        "summary_path": row.get("summary_path"),
        "reconciliation": reconciliation,
        "pr_number": row.get("pr_number"),
        "summary_issue_url": surl,
        "summary_issue_num": _parse_issue_num_from_url(surl),
        "end_reason": row.get("end_reason"),
        "plan_status": None,
        "failed_tickets": failed_tickets,
        "failure_reason": failed_tickets[-1]["failure_reason"] if failed_tickets else None,
        "post_sprint": post_sprint,
        # Materialized summary counts (preferred over re-derivation when present)
        "summary_settled_done": row.get("summary_settled_done"),
        "summary_uat_count": row.get("summary_uat_count"),
        "summary_failure_count": row.get("summary_failure_count"),
    }
