"""Sprint state, live-stream, and log route handlers (extracted from server.py, issue #1255).

Routes owned by this module:
  GET  /api/sprints/{sprint_label}/state          — plan.json payload (issue #507)
  GET  /api/sprints/{sprint_label}/state-full     — full state.json + outcome (issue #435)
  GET  /api/sprints/{sprint_label}/state          — timing data from state.json (issue #212)
  GET  /api/sprints/{sprint_label}/issue/{issue_num}/log — issue log tail
  GET  /api/logs/runs                             — paginated sprint run history (issue #419)
  POST /api/logs/sync-github                      — GitHub events sync
  GET  /api/sprints/{sprint_label}/live           — live snapshot JSON (issue #224)
  GET  /api/sprints/{sprint_label}/live/stream    — SSE log-line stream (issue #224)

Helpers that are only consumed by the routes above were moved here as private
functions.  Shared server.py helpers (_SPRINT_LABEL_RE, _project_root_path,
_commander_dir, etc.) are accessed via the deferred ``_server()`` import so the
circular-import guard stays intact.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
if str(_DASHBOARD_ROOT) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_ROOT))

import db  # noqa: E402
import github_client  # noqa: E402
import github_events_sync  # noqa: E402
import live_metrics as _live_metrics  # noqa: E402
import projects as projects_module  # noqa: E402
from sizing import (  # noqa: E402
    letter_from_minutes as _letter_from_minutes,
    minutes_from_letter as _minutes_from_letter,
)
from .hermes_models import SprintLiveResponse  # noqa: E402

router = APIRouter(tags=["sprint_live"])


def _server():
    """Deferred import of the monolith — safe at request time, avoids circular import."""
    import server  # noqa: PLC0415
    return server


# ── Private helpers (moved from server.py; only used by routes in this module) ─

def _parse_log_lines_for_live(lines: list[str], limit: int = 50) -> list[dict]:
    """Parse log lines into structured log entries for the live panel.

    Classifies each line into one of: dispatch, success, warn, fail, event.
    Returns the last `limit` entries (oldest-first).
    """
    entries: list[dict] = []
    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            continue

        if (
            stripped.startswith("→")
            or stripped.startswith("---")
            or "start_feature.py" in stripped
            or "Dispatching" in stripped
        ):
            line_type = "dispatch"
        elif (
            stripped.startswith("✓")
            or "promoted" in stripped.lower()
            or "merged" in stripped.lower()
            or "completed" in stripped.lower()
            or "done" in stripped.lower()
        ):
            line_type = "success"
        elif (
            "warning" in stripped.lower()
            or stripped.lower().startswith("warn")
            or "[retry]" in stripped.lower()
        ):
            line_type = "warn"
        elif (
            "error" in stripped.lower()
            or "fail" in stripped.lower()
            or "skipped" in stripped.lower()
            or stripped.lower().startswith("err")
        ):
            line_type = "fail"
        else:
            line_type = "event"

        entries.append({"timestamp": "—", "type": line_type, "message": stripped})

    return entries[-limit:]


def _find_latest_sprint_log(log_dir: Path, sprint_label: str) -> Optional[Path]:
    """Return the most recently modified sprint-run-<label>-*.log file, or None."""
    if not log_dir.exists():
        return None
    candidates = sorted(
        log_dir.glob(f"sprint-run-{sprint_label}-*.log"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _live_issue_size_from_labels(labels: list[dict]) -> Optional[str]:
    for lbl in labels:
        name = (lbl.get("name") or "") if isinstance(lbl, dict) else str(lbl)
        m = re.match(r"^size-([SMLX]+)$", name)
        if m:
            return m.group(1)
    return None


def _live_issue_sizes_from_github(repo: str, sprint_label: str) -> dict[int, str]:
    """Best-effort size map from cached open issues (GitHub size-* labels)."""
    out: dict[int, str] = {}
    try:
        for iss in github_client.cached_open_issues_with_body(repo_name=repo) or []:
            label_names = {lbl.get("name") for lbl in iss.get("labels", [])}
            if sprint_label not in label_names:
                continue
            sz = _live_issue_size_from_labels(iss.get("labels", []))
            if sz:
                out[int(iss["number"])] = sz
    except Exception:
        pass
    return out


def _live_issue_size_and_minutes(
    commander: Path,
    num: int,
    estimates: dict,
    github_sizes: dict[int, str],
) -> tuple[Optional[str], Optional[int]]:
    est_entry = estimates.get(str(num)) or estimates.get(num)
    raw_size: Optional[str] = est_entry.get("size") if est_entry else None
    raw_minutes: Optional[int] = est_entry.get("minutes") if est_entry else None
    if not raw_size:
        est_path = commander / "estimates" / f"issue-{num}.json"
        if est_path.exists():
            try:
                file_est = json.loads(est_path.read_text(encoding="utf-8"))
                raw_size = file_est.get("size") or raw_size
                raw_minutes = file_est.get("minutes") or raw_minutes
            except Exception:
                pass
    if not raw_size:
        raw_size = github_sizes.get(num)
    if raw_size and not raw_minutes:
        raw_minutes = _minutes_from_letter(raw_size)
    elif raw_minutes and not raw_size:
        raw_size = _letter_from_minutes(raw_minutes)
    return raw_size, raw_minutes


def _live_coder_attempt_count(issue_number: int, sprint_label: str) -> int:
    try:
        rows = db.agent_runs_for_issue(issue_number, sprint_label)
        return sum(1 for r in rows if (r.get("agent") or "").lower() == "coder")
    except Exception:
        return 0


_PLAN_TERMINAL_LIVE_STATES = frozenset({
    "needs_rework", "completed", "ready_to_merge", "partial_finished",
})


def _live_freeze_terminal_fields(payload: dict, plan: dict) -> dict:
    """Strip in-flight agents and freeze elapsed time for ended sprints."""
    ended_at_str = plan.get("ended_at")
    started_at_str = payload.get("started_at") or plan.get("started_at")
    if started_at_str and ended_at_str:
        try:
            start_dt = datetime.fromisoformat(str(started_at_str).rstrip("Z"))
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=timezone.utc)
            end_dt = datetime.fromisoformat(str(ended_at_str).rstrip("Z"))
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            payload["time_spent_sec"] = max(0, int((end_dt - start_dt).total_seconds()))
        except Exception:
            pass
    payload["active_agents"] = []
    payload["active_agent"] = None
    payload["current_ticket"] = None
    payload["est_remaining_minutes"] = 0
    end_reason = (plan.get("end_reason") or "").lower()
    payload["run_state"] = (
        "cancelled" if "stopped by user" in end_reason else "finished"
    )
    if ended_at_str:
        payload["ended_at"] = ended_at_str
    for iss in payload.get("issues") or []:
        if iss.get("agent_status") == "running":
            iss["agent_status"] = None
            iss["agent"] = None
        if iss.get("status") == "in-progress":
            iss["status"] = "pending"
    return payload


# ── Route handlers ────────────────────────────────────────────────────────────

@router.get("/api/sprints/{sprint_label}/state")
def get_sprint_state_plan(sprint_label: str, project: str):
    """Return the full plan.json payload for a sprint (issue #507).

    GET endpoints must not write plan.json (issue #1096): the sprint manager
    is the sole writer.  Returns 404 when plan.json does not exist.
    """
    srv = _server()
    if not srv._SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")
    project_root = srv._project_root_path(project)
    plan = srv._read_plan_json(project_root, sprint_label)
    if plan is None:
        raise HTTPException(404, detail=f"plan.json not found for {sprint_label!r}")
    return plan


@router.get("/api/logs/runs")
def get_logs_runs(
    project: Optional[str] = None,
    sprint_label: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    """Return paginated sprint run history read from sprint state JSON files."""
    start_dt: Optional[datetime] = None
    end_dt: Optional[datetime] = None

    if start_date:
        try:
            start_dt = datetime.fromisoformat(start_date)
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(
                400,
                detail=f"Invalid start_date {start_date!r}. Use ISO 8601 format, e.g. 2024-06-01.",
            )

    if end_date:
        try:
            parsed_end = datetime.fromisoformat(end_date)
            if parsed_end.tzinfo is None:
                parsed_end = parsed_end.replace(tzinfo=timezone.utc)
            if "T" not in end_date:
                parsed_end = parsed_end.replace(hour=23, minute=59, second=59, microsecond=999999)
            end_dt = parsed_end
        except ValueError:
            raise HTTPException(
                400,
                detail=f"Invalid end_date {end_date!r}. Use ISO 8601 format, e.g. 2024-06-30.",
            )

    items: list[dict] = []
    srv = _server()

    try:
        all_projects = projects_module.load_projects()
    except Exception:
        all_projects = []

    for proj in all_projects:
        repo = proj.get("repo", "")
        project_root = srv._project_root_path(repo)
        sprints_dir = srv._commander_dir(project_root) / "sprints"

        if not sprints_dir.exists():
            continue

        for state_path in sprints_dir.glob("sprint-*-state.json"):
            try:
                state_data = json.loads(state_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            state_project = state_data.get("project") or repo
            state_sprint_label = state_data.get("sprint_label", "")
            start_ts_str = state_data.get("start_timestamp")
            wall_clock = float(state_data.get("wall_clock_secs") or 0.0)
            issues = state_data.get("issues") or []

            if not start_ts_str:
                continue

            try:
                start_time_dt = datetime.fromisoformat(start_ts_str.rstrip("Z"))
                if start_time_dt.tzinfo is None:
                    start_time_dt = start_time_dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue

            end_time_dt = start_time_dt + timedelta(seconds=wall_clock)

            compact_ts = start_time_dt.strftime("%Y%m%dT%H%M%S")
            run_id = f"sprint-{state_sprint_label}-{compact_ts}"

            has_failed = any(
                i.get("agent_status") == "failed"
                or i.get("failure_reason")
                or i.get("status") == "skipped"
                for i in issues
            )
            all_done = bool(issues) and all(i.get("status") == "done" for i in issues)
            if all_done and not has_failed:
                outcome = "success"
            elif has_failed:
                outcome = "partial"
            else:
                outcome = "unknown"

            if project and state_project != project:
                continue
            if sprint_label and state_sprint_label != sprint_label:
                continue
            if start_dt and start_time_dt < start_dt:
                continue
            if end_dt and start_time_dt > end_dt:
                continue

            items.append({
                "run_id": run_id,
                "project": state_project,
                "sprint_label": state_sprint_label,
                "start_time": start_time_dt.isoformat(),
                "end_time": end_time_dt.isoformat(),
                "ticket_count": len(issues),
                "outcome": outcome,
            })

    items.sort(key=lambda x: x["start_time"], reverse=True)

    total = len(items)
    offset = (page - 1) * page_size
    paged = items[offset: offset + page_size]

    return {"items": paged, "page": page, "page_size": page_size, "total": total}


@router.post("/api/logs/sync-github")
def post_logs_sync_github(project: Optional[str] = None):
    """Poll GitHub Events API for the project repo and upsert into the events table."""
    if not project:
        return {"synced": 0, "skipped": 0, "rate_limited": False, "error": "project required"}
    try:
        result = github_events_sync.sync_github_events(project=project, repo=project)
    except Exception as exc:
        return {"synced": 0, "skipped": 0, "rate_limited": False, "error": str(exc)}
    return result


@router.get("/api/sprints/{sprint_label}/state-full")
def get_sprint_state_full(sprint_label: str, project: str):
    """Return full sprint state including per-ticket issues for the comparison view (issue #435)."""
    srv = _server()
    if not srv._SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    project_root = srv._project_root_path(project)
    sprints_dir = srv._commander_dir(project_root) / "sprints"

    if not sprints_dir.exists():
        raise HTTPException(404, detail="No sprints directory found")

    for state_path in sprints_dir.glob("sprint-*-state.json"):
        try:
            state_data = json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if state_data.get("sprint_label") != sprint_label:
            continue

        issues = state_data.get("issues") or []
        dispatch_count = sum(1 for i in issues if i.get("coder_started_at") is not None)
        has_failed = any(
            i.get("agent_status") == "failed"
            or i.get("failure_reason")
            or i.get("status") == "skipped"
            for i in issues
        )
        all_done = bool(issues) and all(i.get("status") == "done" for i in issues)
        if all_done and not has_failed:
            outcome = "success"
        elif has_failed:
            outcome = "partial"
        else:
            outcome = "unknown"

        return {**state_data, "outcome": outcome, "dispatch_count": dispatch_count}

    raise HTTPException(404, detail=f"Sprint state not found for {sprint_label!r}")


@router.get("/api/sprints/{sprint_label}/issue/{issue_num}/log")
def get_issue_log(sprint_label: str, project: str, issue_num: int, tail_lines: int = 200):
    """Return the last N lines of sprint-issue-<N>.log for the given sprint."""
    srv = _server()
    if not srv._SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")
    from log_source import read_log  # noqa: PLC0415 — local import keeps startup fast
    project_root = srv._project_root_path(project)
    return read_log("issue", project_root, label=sprint_label, issue_num=issue_num, tail_lines=tail_lines)


@router.get("/api/sprints/{sprint_label}/live", response_model=SprintLiveResponse)
def get_sprint_live_snapshot(sprint_label: str, project: str):
    """Return a JSON snapshot of the live running sprint.

    Response shape:
    {
      "time_spent_sec": <int>,
      "started_at": "<ISO8601>",
      "current_ticket": {"number": N, "title": "..."} | null,
      "active_agent": {"name": "coder"|"tester", "model": "...", "pid": N} | null,
      "active_agents": [{"name": "coder"|"tester", "ticket": {"number": N, "title": "..."}, "pid": N}, ...],
      "pipeline_mode": <bool>,
      "max_coder_slots": <int>, "max_tester_slots": <int>,   # lane capacity (issue #1415)
      "active_coder_slots": <int>, "active_tester_slots": <int>,  # lane occupancy (issue #1440)
      "levels": [{"level": N, "total": N, "merged": N, "state": "complete"|"active"|"waiting"}, ...],
      "recent_log_lines": [{"timestamp": "HH:MM:SS", "type": "...", "message": "..."}, ...],
      "issues": [
        {
          "number": <int>,
          "title": <str>,
          "status": "pending"|"in-progress"|"done"|"skipped",
          "agent_status": "running"|"failed"|null,
          "agent": "coder"|"tester"|null,
          "elapsed_secs": <int>|null,
          "size": "S"|"M"|"L"|"XL"|null
        }, ...
      ]
    }
    recent_log_lines contains the last 50 lines.
    issues is sourced from the locked launch snapshot (issue #306).
    """
    srv = _server()
    if not srv._SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    project_root = srv._project_root_path(project)
    commander = srv._commander_dir(project_root)
    plan = srv._read_plan_json(project_root, sprint_label) or {}
    plan_terminal = (
        not srv._sprint_pid_alive(project_root, sprint_label)
        and (
            plan.get("ended_at")
            or (plan.get("state") or "").lower() in _PLAN_TERMINAL_LIVE_STATES
        )
    )

    status_key = (project, sprint_label)
    status_data = srv._sprint_statuses.get(status_key, {})

    if not status_data:
        _fb_path = commander / "sprints" / f"{sprint_label}-state.json"
        if _fb_path.exists():
            try:
                status_data = json.loads(_fb_path.read_text(encoding="utf-8"))
            except Exception:
                pass

    started_at_str: Optional[str] = status_data.get("start_timestamp")
    started_at_dt: Optional[datetime] = None
    if started_at_str:
        try:
            started_at_dt = datetime.fromisoformat(started_at_str.rstrip("Z"))
            if started_at_dt.tzinfo is None:
                started_at_dt = started_at_dt.replace(tzinfo=timezone.utc)
        except Exception:
            started_at_dt = None

    now_utc = datetime.now(timezone.utc)
    time_spent_sec: int = 0
    if started_at_dt:
        time_spent_sec = max(0, int((now_utc - started_at_dt).total_seconds()))

    current_ticket: Optional[dict] = None
    issues = status_data.get("issues", [])
    _ACTIVE_AGENT = ("coder_dispatched", "coder_running", "tester_dispatched", "tester_running")
    active_iss = [i for i in issues if i.get("agent_status") in _ACTIVE_AGENT]
    in_progress = [i for i in issues if i.get("status") == "in-progress"]
    if active_iss:
        iss = active_iss[-1]
        current_ticket = {"number": iss.get("number"), "title": iss.get("title", "")}
    elif in_progress:
        iss = in_progress[-1]
        current_ticket = {"number": iss.get("number"), "title": iss.get("title", "")}
    else:
        pending = [i for i in issues if i.get("status") not in ("done", "skipped")]
        if pending:
            iss = pending[0]
            current_ticket = {"number": iss.get("number"), "title": iss.get("title", "")}

    done_count = sum(1 for i in issues if i.get("status") == "done")
    failed_count = sum(1 for i in issues if i.get("agent_status") == "failed")
    skipped_count = sum(
        1 for i in issues
        if i.get("status") == "skipped" and i.get("agent_status") != "failed"
    )
    total_count = len(issues)
    complete_count = done_count + failed_count + skipped_count
    pending_count = total_count - complete_count

    estimates: dict = status_data.get("estimates", {})
    github_sizes = _live_issue_sizes_from_github(project, sprint_label)
    fix_round_max = int(os.environ.get("COMMANDER_MAX_FIX_ROUNDS", "3"))
    est_remaining_minutes: Optional[int] = None

    if estimates and total_count > 0:
        rem_minutes = 0
        has_any_estimate = False
        for iss in issues:
            num = iss.get("number")
            terminal = iss.get("status") in ("done", "skipped")
            est_entry = estimates.get(str(num)) or estimates.get(num)
            if est_entry:
                has_any_estimate = True
                if not terminal:
                    stored_mins = est_entry.get("minutes")
                    if stored_mins and isinstance(stored_mins, (int, float)) and stored_mins > 0:
                        rem_minutes += int(stored_mins)
                    else:
                        rem_minutes += _minutes_from_letter(est_entry.get("size", ""))
        if has_any_estimate:
            est_remaining_minutes = rem_minutes

    if est_remaining_minutes is None and complete_count > 0 and pending_count > 0:
        wall_secs = status_data.get("wall_clock_secs", 0.0)
        avg_secs = wall_secs / complete_count if complete_count > 0 else 0
        est_remaining_minutes = max(0, round(avg_secs * pending_count / 60))

    if est_remaining_minutes is None and total_count > 0:
        est_remaining_minutes = 0

    _agent_run_rows = _live_metrics._fetch_sprint_agent_run_rows(sprint_label)
    _runs_by_issue = _live_metrics.runs_by_issue(_agent_run_rows)

    _IN_FLIGHT_AGENT_STATUSES = frozenset({
        "coder_dispatched", "coder_running", "coder_done",
        "tester_dispatched", "tester_running", "tester_done",
    })

    def _parse_ts_utc(s: Optional[str]) -> Optional[datetime]:
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s.rstrip("Z"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None

    issues_out: list[dict] = []
    for iss in issues:
        num = iss.get("number")
        raw_agent_status = iss.get("agent_status")

        if raw_agent_status in _IN_FLIGHT_AGENT_STATUSES:
            derived_status = "in-progress"
        else:
            derived_status = iss.get("status", "pending")

        if raw_agent_status in ("coder_running", "tester_running"):
            public_agent_status: Optional[str] = "running"
        elif raw_agent_status == "failed":
            public_agent_status = "failed"
        else:
            public_agent_status = None

        if raw_agent_status in ("coder_dispatched", "coder_running"):
            active_role: Optional[str] = "coder"
        elif raw_agent_status in ("tester_dispatched", "tester_running"):
            active_role = "tester"
        else:
            active_role = None

        issue_elapsed = _live_metrics.issue_elapsed_secs(
            iss, now_utc, _runs_by_issue,
        )

        raw_size, raw_minutes = _live_issue_size_and_minutes(
            commander, num, estimates, github_sizes,
        )

        tac = int(iss.get("tester_attempt_count") or 0)
        pipeline_stage = _live_metrics.pipeline_stage_from_status(
            raw_agent_status or "", derived_status, tac,
        )
        coder_attempt = (
            sum(
                1 for r in (_runs_by_issue.get(int(num)) or [])
                if (r.get("agent") or "").lower() == "coder"
            )
            if num is not None else 0
        )
        if coder_attempt < 1 and raw_agent_status in ("coder_dispatched", "coder_running"):
            coder_attempt = 1

        issues_out.append({
            "number":         num,
            "title":          iss.get("title", ""),
            "status":         derived_status,
            "agent_status":   public_agent_status,
            "agent":          active_role,
            "elapsed_secs":   issue_elapsed,
            "size":           raw_size,
            "minutes":        raw_minutes,
            "dispatch_level": iss.get("dispatch_level", 0),
            "coder_model":          iss.get("coder_model"),
            "coder_backend":        iss.get("coder_backend"),
            "coder_provider":       iss.get("coder_provider"),
            "tester_attempt_count": tac,
            "coder_attempt":        coder_attempt,
            "pipeline_stage":       pipeline_stage,
            "category":       iss.get("category"),
            "failure_reason": iss.get("failure_reason"),
        })

    active_agent: Optional[dict] = None
    state_path = commander / "sprints" / f"{sprint_label}-state.json"
    if state_path.exists():
        try:
            state_data = json.loads(state_path.read_text(encoding="utf-8"))
            for iss in state_data.get("issues", []):
                if iss.get("coder_started_at") and not iss.get("tester_finished_at"):
                    agent_name = "tester" if iss.get("tester_started_at") else "coder"
                    agent_model = iss.get("coder_model") if agent_name == "coder" else None
                    active_agent = {"name": agent_name, "model": agent_model, "pid": None, "ticket": None}
                    break
        except Exception:
            pass

    pid_file = commander / "sprints" / f"{sprint_label}-pid"
    if pid_file.exists():
        try:
            pid_val = int(pid_file.read_text(encoding="utf-8").strip())
            if active_agent:
                active_agent["pid"] = pid_val
            else:
                active_agent = {"name": "coder", "model": None, "pid": pid_val, "ticket": None}
        except Exception:
            pass

    pipeline_mode = bool(status_data.get("pipeline_mode", False))

    coder_entries: list[dict] = []
    tester_entry = None
    active_tester_slots = 0
    for iss in issues:
        ticket = {"number": iss.get("number"), "title": iss.get("title", "")}
        cs, cf = iss.get("coder_started_at"), iss.get("coder_finished_at")
        ts, tf = iss.get("tester_started_at"), iss.get("tester_finished_at")
        if ts and not tf:
            tester_entry = {"name": "tester", "ticket": ticket, "pid": iss.get("tester_pid")}
            active_tester_slots += 1
        elif cs and not cf:
            coder_entries.append({"name": "coder", "ticket": ticket, "pid": iss.get("coder_pid")})
    active_agents: list[dict] = coder_entries + ([tester_entry] if tester_entry else [])
    if not active_agents and active_agent:
        active_agents = [active_agent]

    # Active lane occupancy (issue #1440) — how many coder/tester slots are
    # currently busy. The frontend multi-lane view gates on max_coder_slots > 1
    # AND more than one active coder, so these counts must travel with the
    # capacity (max_*_slots) values in the same snapshot.
    active_coder_slots = len(coder_entries)

    levels_out = _live_metrics.compute_levels(issues)

    log_dir = commander / "logs"
    recent_log_lines: list[dict] = []
    log_path = _find_latest_sprint_log(log_dir, sprint_label)
    if log_path:
        try:
            raw_lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            recent_log_lines = _parse_log_lines_for_live(raw_lines, limit=50)
        except OSError:
            pass

    payload = {
        "time_spent_sec": time_spent_sec,
        "started_at": started_at_str,
        "current_ticket": current_ticket,
        "active_agent": active_agent,
        "active_agents": active_agents,
        "pipeline_mode": pipeline_mode,
        "levels": levels_out,
        "recent_log_lines": recent_log_lines,
        "done_count":           done_count,
        "failed_count":         failed_count,
        "skipped_count":        skipped_count,
        "pending_count":        pending_count,
        "total_count":          total_count,
        "complete_count":       complete_count,
        "est_remaining_minutes": est_remaining_minutes,
        "issues":               issues_out,
        "llm_provider":         status_data.get("llm_provider"),
        **_live_metrics.lane_capacity(status_data),
        "active_coder_slots":   active_coder_slots,
        "active_tester_slots":  active_tester_slots,
        "fix_round_max":        fix_round_max,
        **_live_metrics.running_metrics(sprint_label, project),
    }
    if plan_terminal:
        payload = _live_freeze_terminal_fields(payload, plan)
    return payload


_SNAPSHOT_EVERY_N = 10  # emit snapshot every N half-second ticks (~5 s)


def _find_issue_log(log_dir: Path, issue_num: int) -> Optional[Path]:
    """Return sprint-issue-<N>.log path if it exists in log_dir."""
    p = log_dir / f"sprint-issue-{issue_num}.log"
    return p if p.exists() else None


@router.get("/api/sprints/{sprint_label}/live/stream")
async def get_sprint_live_stream(sprint_label: str, project: str, request: Request):
    """SSE endpoint that streams incremental log-line events as they occur.

    Events emitted (issue #1777):
    - event: snapshot   data: <full live snapshot JSON>  (on connect + every ~5 s)
    - event: log_line   data: {"timestamp": "...", "type": "...", "message": "..."}
                             optional "issue_num": N for per-worker routing (AC5)
    - event: complete   data: {"reason": "stopped"}   (when sprint ends)

    Data source: tails the most recent sprint-run-<label>-*.log file for
    orchestrator log_line events, plus sprint-issue-N.log for per-issue events.
    """
    srv = _server()
    if not srv._SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    project_root = srv._project_root_path(project)
    commander = srv._commander_dir(project_root)
    log_dir = commander / "logs"

    async def _stream():
        log_path: Optional[Path] = None
        for _ in range(20):
            log_path = _find_latest_sprint_log(log_dir, sprint_label)
            if log_path:
                break
            await asyncio.sleep(0.1)

        if not log_path:
            yield f"event: complete\ndata: {json.dumps({'reason': 'no_log_file'})}\n\n"
            return

        try:
            file_size = log_path.stat().st_size
        except OSError:
            file_size = 0

        # Start tailing from the current end (only new lines going forward).
        current_offset = file_size

        # Emit initial snapshot so the board can bootstrap without a separate REST call.
        snap = None
        try:
            snap = get_sprint_live_snapshot(sprint_label, project)
            yield f"event: snapshot\ndata: {json.dumps(snap)}\n\n"
        except Exception:
            pass

        # Track per-issue log files: {issue_num: current_offset}
        issue_log_offsets: dict[int, int] = {}

        # Seed per-issue log tracking from the initial snapshot — reuse snap so
        # get_sprint_live_snapshot is not called a second time on connect.
        try:
            for iss in ((snap or {}).get("issues") or []):
                num = iss.get("number")
                if num is not None:
                    p = _find_issue_log(log_dir, num)
                    if p is not None:
                        try:
                            issue_log_offsets[num] = p.stat().st_size
                        except OSError:
                            issue_log_offsets[num] = 0
        except Exception:
            pass

        snapshot_tick = 0

        while True:
            if await request.is_disconnected():
                return

            is_running = srv._is_sprint_running(project_root, sprint_label)

            # ── Tail orchestrator/dispatch log ────────────────────────────────
            try:
                file_size = log_path.stat().st_size
            except OSError:
                file_size = current_offset

            if file_size > current_offset:
                try:
                    with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
                        fh.seek(current_offset)
                        new_text = fh.read(file_size - current_offset)
                    current_offset = file_size

                    new_lines = new_text.splitlines()
                    parsed = _parse_log_lines_for_live(new_lines, limit=len(new_lines))
                    for entry in parsed:
                        yield f"event: log_line\ndata: {json.dumps(entry)}\n\n"
                except OSError:
                    pass

            # ── Tail per-issue log files (issue #1777 AC5) ───────────────────
            for num, i_offset in list(issue_log_offsets.items()):
                i_path = log_dir / f"sprint-issue-{num}.log"
                try:
                    i_size = i_path.stat().st_size
                except OSError:
                    continue
                if i_size > i_offset:
                    try:
                        with open(i_path, "r", encoding="utf-8", errors="replace") as fh:
                            fh.seek(i_offset)
                            new_text = fh.read(i_size - i_offset)
                        issue_log_offsets[num] = i_size
                        new_lines = new_text.splitlines()
                        parsed = _parse_log_lines_for_live(new_lines, limit=len(new_lines))
                        for entry in parsed:
                            entry["issue_num"] = num
                            yield f"event: log_line\ndata: {json.dumps(entry)}\n\n"
                    except OSError:
                        pass

            # ── Periodic snapshot for board metric refresh (AC3, AC8) ────────
            snapshot_tick += 1
            if snapshot_tick >= _SNAPSHOT_EVERY_N:
                snapshot_tick = 0
                try:
                    snap = get_sprint_live_snapshot(sprint_label, project)
                    yield f"event: snapshot\ndata: {json.dumps(snap)}\n\n"
                    # Register any newly-started issue logs
                    for iss in (snap.get("issues") or []):
                        num = iss.get("number")
                        if num is not None and num not in issue_log_offsets:
                            p = _find_issue_log(log_dir, num)
                            if p is not None:
                                try:
                                    issue_log_offsets[num] = p.stat().st_size
                                except OSError:
                                    issue_log_offsets[num] = 0
                except Exception:
                    pass

            if not is_running:
                yield f"event: complete\ndata: {json.dumps({'reason': 'stopped'})}\n\n"
                return

            await asyncio.sleep(0.5)

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/sprints/{sprint_label}/state-timing")
def get_sprint_state_timing(sprint_label: str, project: str):
    """Return timing data from sprint-N-state.json for duration display (issue #212).

    Returns:
      - wall_clock_secs: total sprint wall-clock time
      - issues: list of {number, duration_secs, failed} for each issue that has
                timing data (coder_started_at present); duration_secs is computed
                from coder_started_at to tester_finished_at (or status_changed_at
                as fallback).  failed=true when issue status is 'skipped' or
                agent_status is 'failed'.
    """
    srv = _server()
    if not srv._SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    project_root = srv._project_root_path(project)
    commander = srv._commander_dir(project_root)

    m = re.search(r"(\d+)", sprint_label)
    n = m.group(1) if m else sprint_label

    state_path = commander / "sprints" / f"sprint-{n}-state.json"

    if not state_path.exists():
        raise HTTPException(404, detail=f"State not found for {sprint_label!r}")

    try:
        state_data = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise HTTPException(500, detail=f"Could not read state file: {e}")

    def _parse_iso(s: Optional[str]) -> Optional[float]:
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s.rstrip("Z"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except Exception:
            return None

    issue_durations = []
    for iss in state_data.get("issues", []):
        start_ts = _parse_iso(iss.get("coder_started_at"))
        if start_ts is None:
            continue
        end_ts = _parse_iso(iss.get("tester_finished_at")) or _parse_iso(iss.get("status_changed_at"))
        if end_ts is None:
            continue
        duration_secs = max(0.0, end_ts - start_ts)
        failed = (
            iss.get("status") == "skipped"
            or iss.get("agent_status") == "failed"
            or iss.get("failure_reason") is not None
        )
        issue_durations.append({
            "number":       iss["number"],
            "duration_secs": round(duration_secs),
            "failed":       failed,
        })

    return {
        "sprint_label":   sprint_label,
        "wall_clock_secs": state_data.get("wall_clock_secs", 0.0),
        "issues":         issue_durations,
    }
