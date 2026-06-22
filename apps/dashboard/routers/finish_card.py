"""Sprint finish-card route handler extracted from server.py (issue #1267).

GET /api/sprints/{sprint_label}/finish-card

Returns data for the floating finish-report card above a sprint pane.
Always returns HTTP 200; clients must check the ``state`` field.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
_SERVICES_ROOT = _DASHBOARD_ROOT.parent.parent / "services" / "sprint_manager"
for _p in (str(_DASHBOARD_ROOT), str(_SERVICES_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import db  # noqa: E402

router = APIRouter(tags=["finish_card"])


def _server():
    """Deferred import of the monolith — safe at request time, avoids circular import."""
    import server  # noqa: PLC0415
    return server


@router.get("/api/sprints/{sprint_label}/finish-card")
def get_sprint_finish_card(sprint_label: str, project: str):
    """Return data for the floating finish-report card above a sprint pane.

    Always returns HTTP 200. Clients must check the ``state`` field in the
    response body — do NOT rely on HTTP 404 to detect a missing sprint.
    (Before issue #671 this endpoint returned 404 when no state file existed;
    it now returns HTTP 200 with state="no_data" instead.)

    For running sprints: state="running", in_flight_count, pending_count,
    done_count, wall_clock_secs, started_at.

    For finished sprints: state in (completed|has_rework|cancelled),
    done_count, failed_count, skipped_count, rework_count, wall_clock_secs,
    ended_at, summary_issue_url, summary_issue_num.

    When no sprint state file exists on disk: state="no_data", sprint_label,
    sprint_number only (HTTP 200, not 404).
    """
    srv = _server()

    if not srv._SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    fc_m = re.search(r"(\d+)", sprint_label)
    fc_n = fc_m.group(1) if fc_m else sprint_label
    sprint_number = int(fc_n) if fc_n.isdigit() else None

    project_root = srv._project_root_path(project)
    commander = srv._commander_dir(project_root)

    if srv._is_sprint_running(project_root, sprint_label):
        status_key = (project, sprint_label)
        status_data = srv._sprint_statuses.get(status_key, {})
        live_issues = status_data.get("issues", [])
        in_flight = sum(1 for i in live_issues if i.get("status") == "in-progress")
        pending = sum(1 for i in live_issues if i.get("status") == "pending")
        done = sum(1 for i in live_issues if i.get("status") == "done")
        started_at_str: Optional[str] = status_data.get("start_timestamp")
        wall_clock_secs = 0.0
        if started_at_str:
            try:
                started_dt = datetime.fromisoformat(started_at_str.rstrip("Z"))
                if started_dt.tzinfo is None:
                    started_dt = started_dt.replace(tzinfo=timezone.utc)
                wall_clock_secs = (datetime.now(timezone.utc) - started_dt).total_seconds()
            except Exception:
                pass
        return {
            "sprint_label":    sprint_label,
            "sprint_number":   sprint_number,
            "state":           "running",
            "in_flight_count": in_flight,
            "pending_count":   pending,
            "done_count":      done,
            "wall_clock_secs": wall_clock_secs,
            "started_at":      started_at_str,
        }

    if not srv._sprint_has_own_run_outcome(project_root, sprint_label, project):
        return {
            "sprint_label":  sprint_label,
            "sprint_number": sprint_number,
            "state":         "no_data",
        }

    fc_json_path = srv._sprint_json_path(project_root, sprint_label)
    fc_sprint_json = srv._sprint_json_read(fc_json_path)
    fc_is_cancelled: bool = fc_sprint_json.get("status") in ("cancelled", "needs_rework")

    state_path = commander / "sprints" / f"sprint-{fc_n}-state.json"
    if not state_path.exists():
        if fc_is_cancelled:
            return {
                "sprint_label":      sprint_label,
                "sprint_number":     sprint_number,
                "state":             "cancelled",
                "done_count":        0,
                "failed_count":      0,
                "skipped_count":     0,
                "rework_count":      0,
                "wall_clock_secs":   0.0,
                "ended_at":          None,
                "summary_issue_url": None,
                "summary_issue_num": None,
            }
        return {
            "sprint_label":  sprint_label,
            "sprint_number": sprint_number,
            "state":         "no_data",
        }

    try:
        fc_state_data = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise HTTPException(500, detail=str(e))

    # Rec 2d — collapse the disk-vs-DB dual path: populate the DB row from disk on
    # read so DB-backed readers (outcome, history) converge regardless of which
    # endpoint the UI hits first. UPDATE-only (never mints a draft row);
    # best-effort — an ingest hiccup must never break the finish card.
    _fc_db_row = db.get_sprint(sprint_label, project=project or None)
    if _fc_db_row and not _fc_db_row.get("run_ingested_at"):
        try:
            db.ingest_sprint_run_artifact(sprint_label, fc_state_data, project=project)
        except Exception:
            pass

    def _fc_parse_iso(s: Optional[str]) -> Optional[float]:
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s.rstrip("Z"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except Exception:
            return None

    sprints_dir = commander / "sprints"
    fc_sprint_status: Optional[str] = None
    for sf in sorted(sprints_dir.glob(f"sprint-{fc_n}-summary-*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            meta = srv._parse_summary_file(sf)
            raw = (meta.get("status") or "").lower()
            if raw in ("complete", "completed"):
                fc_sprint_status = "completed"
            elif raw in ("stopped", "failed", "cancelled"):
                fc_sprint_status = "stopped"
                if raw == "cancelled":
                    fc_is_cancelled = True
        except Exception:
            pass
        break

    fc_issues_raw = fc_state_data.get("issues", [])
    if fc_sprint_status is None and fc_issues_raw:
        has_pending = any(i.get("status") == "pending" for i in fc_issues_raw)
        has_failed = any(i.get("agent_status") == "failed" or i.get("failure_reason") for i in fc_issues_raw)
        if not has_pending:
            fc_sprint_status = "stopped" if has_failed else "completed"

    done_count    = sum(1 for i in fc_issues_raw if i.get("status") == "done")
    failed_count  = sum(1 for i in fc_issues_raw if i.get("agent_status") == "failed" or i.get("failure_reason"))
    skipped_count = sum(
        1 for i in fc_issues_raw
        if i.get("status") == "skipped" and not (i.get("agent_status") == "failed" or i.get("failure_reason"))
    )

    fc_ended_ts: Optional[float] = None
    for iss in fc_issues_raw:
        end_ts = _fc_parse_iso(iss.get("tester_finished_at")) or _fc_parse_iso(iss.get("status_changed_at"))
        if end_ts and (fc_ended_ts is None or end_ts > fc_ended_ts):
            fc_ended_ts = end_ts
    ended_at = (
        datetime.fromtimestamp(fc_ended_ts, tz=timezone.utc).strftime("%H:%M")
        if fc_ended_ts else None
    )

    if fc_is_cancelled:
        card_state = "cancelled"
        rework_count = 0
    elif srv._has_rework_tickets(sprint_label, project):
        card_state = "has_rework"
        rework_count = srv._count_rework_tickets(sprint_label, project)
    else:
        card_state = "completed"
        rework_count = 0

    summary_issue_url: Optional[str] = fc_state_data.get("summary_issue_url")
    summary_issue_num: Optional[int] = None
    if summary_issue_url:
        m_num = re.search(r"/issues/(\d+)", summary_issue_url)
        if m_num:
            summary_issue_num = int(m_num.group(1))

    return {
        "sprint_label":      sprint_label,
        "sprint_number":     sprint_number,
        "state":             card_state,
        "lifecycle":         db.canonical_lifecycle(card_state),
        "end_reason":        fc_sprint_json.get("end_reason"),
        "done_count":        done_count,
        "failed_count":      failed_count,
        "skipped_count":     skipped_count,
        "rework_count":      rework_count,
        "wall_clock_secs":   fc_state_data.get("wall_clock_secs", 0.0),
        "ended_at":          ended_at,
        "summary_issue_url": summary_issue_url,
        "summary_issue_num": summary_issue_num,
    }
