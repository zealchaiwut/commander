"""Service logic for GET /api/running?project= snapshot endpoint (issue #1645).

Reads exclusively from the in-memory _sprint_statuses dict, local disk
state.json/plan.json files, and the SQLite agent_runs DB. No GitHub API
client methods are invoked during a request.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
if str(_DASHBOARD_ROOT) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_ROOT))

import live_metrics as _live_metrics  # noqa: E402
from sizing import (  # noqa: E402
    letter_from_minutes as _letter_from_minutes,
    minutes_from_letter as _minutes_from_letter,
)


def _server():
    """Deferred import of the monolith — safe at request time, avoids circular import."""
    import server  # noqa: PLC0415
    return server


_IN_FLIGHT_AGENT_STATUSES = frozenset({
    "coder_dispatched", "coder_running", "coder_done",
    "tester_dispatched", "tester_running", "tester_done",
})

_PLAN_TERMINAL_STATES = frozenset({
    "needs_rework", "completed", "ready_to_merge", "partial_finished",
})


def _local_issue_size_and_minutes(
    commander: Path,
    num: int,
    estimates: dict,
) -> tuple[Optional[str], Optional[int]]:
    """Return (size, minutes) from local estimate dict or per-issue estimate file.

    Deliberately skips the GitHub cache so no GitHub API call is made.
    """
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
    if raw_size and not raw_minutes:
        raw_minutes = _minutes_from_letter(raw_size)
    elif raw_minutes and not raw_size:
        raw_size = _letter_from_minutes(raw_minutes)
    return raw_size, raw_minutes


def _snapshot_from_dispatch(project: str, run: dict) -> dict:
    """Build a Running-pane snapshot from an API dispatch JSON record (#2355)."""
    from services.sprint_manager.dispatch_runner import dispatch_live_fields

    tickets = list(run.get("tickets") or [])
    outcomes = list(run.get("outcomes") or [])
    done_issues = {
        o.get("issue") for o in outcomes
        if o.get("step") == "tester" and o.get("ok")
    }
    failed_issue = run.get("failed_issue")
    current_issue = run.get("current_issue")
    current_step = run.get("current_step")

    issues_out: list[dict] = []
    done_count = 0
    failed_count = 0
    for num in tickets:
        if num in done_issues:
            status = "done"
            done_count += 1
            agent_status = None
            agent = None
        elif failed_issue is not None and num == failed_issue:
            status = "skipped"
            failed_count += 1
            agent_status = "failed"
            agent = None
        elif current_issue is not None and num == current_issue:
            status = "in-progress"
            agent_status = "running"
            agent = current_step if current_step in ("coder", "tester") else None
        else:
            status = "pending"
            agent_status = None
            agent = None
        issues_out.append({
            "number": num,
            "title": "",
            "status": status,
            "agent_status": agent_status,
            "agent": agent,
            "elapsed_secs": None,
            "size": None,
            "minutes": None,
            "dispatch_level": 0,
            "coder_model": None,
            "coder_backend": None,
            "coder_provider": None,
            "tester_attempt_count": 0,
            "coder_attempt": 0,
            "pipeline_stage": current_step if num == current_issue else None,
            "category": None,
            "failure_reason": None,
        })

    total_count = len(tickets)
    complete_count = done_count + failed_count
    pending_count = total_count - complete_count
    current_ticket = (
        {"number": current_issue, "title": ""} if current_issue is not None else None
    )
    active_agents: list[dict] = []
    if current_issue is not None and current_step in ("coder", "tester"):
        active_agents = [{
            "name": current_step,
            "ticket": current_ticket,
            "pid": None,
        }]

    dispatch = dispatch_live_fields(run)
    return {
        "sprint_label": run.get("sprint_label"),
        "project": project,
        "source": "dispatch",
        "dispatch": dispatch,
        "run_id": run.get("run_id"),
        "time_spent_sec": 0,
        "started_at": run.get("started_at") or None,
        "current_ticket": current_ticket,
        "active_agents": active_agents,
        "pipeline_mode": False,
        "levels": [],
        "done_count": done_count,
        "failed_count": failed_count,
        "skipped_count": 0,
        "pending_count": pending_count,
        "total_count": total_count,
        "complete_count": complete_count,
        "est_remaining_minutes": 0,
        "issues": issues_out,
        "llm_provider": None,
        "active_coder_slots": 1 if current_step == "coder" else 0,
        "active_tester_slots": 1 if current_step == "tester" else 0,
        "max_coder_slots": 1,
        "max_tester_slots": 1,
        "run_state": "running",
    }


def _active_dispatch_for_project(project: str, project_root: Path) -> Optional[dict]:
    """Newest active dispatch-*.json for this project (local files only)."""
    from services.sprint_manager.dispatch_runner import list_dispatch_runs

    # runtime_dir(project_root) → <project>/.commander/runtime/dispatch-*.json
    runs = list_dispatch_runs(project_root, repo=project)
    if not runs:
        return None
    # Prefer running over queued; then newest started_at
    runs.sort(
        key=lambda r: (
            0 if r.get("status") == "running" else 1,
            r.get("started_at") or "",
        )
    )
    return runs[0]


def build_running_snapshot(project: str) -> Optional[dict]:
    """Build the running sprint snapshot for the Running pane's first paint.

    Returns a dict with running sprint status and per-ticket progress when a
    sprint is running for the given project, or None if no running sprint exists.

    Reads exclusively from:
      - server._any_sprint_running (reads plan.json + PID files — local disk)
      - server._sprint_statuses (in-memory dict)
      - <commander>/sprints/<label>-state.json (local disk fallback)
      - ``.commander/runtime/dispatch-*.json`` for API dispatch (#2355)
      - SQLite agent_runs via live_metrics helpers

    No GitHub API client methods are invoked.
    """
    srv = _server()
    project_root = srv._project_root_path(project)

    running = srv._any_sprint_running(project=project)
    if not running:
        dispatch_run = _active_dispatch_for_project(project, project_root)
        if dispatch_run is None:
            return None
        return _snapshot_from_dispatch(project, dispatch_run)

    sprint_label: str = running["sprint_label"]
    commander = srv._commander_dir(project_root)

    # Check plan.json terminal state (plan.json is a local disk file)
    plan = srv._read_plan_json(project_root, sprint_label) or {}
    plan_terminal = (
        not srv._sprint_pid_alive(project_root, sprint_label)
        and (
            plan.get("ended_at")
            or (plan.get("state") or "").lower() in _PLAN_TERMINAL_STATES
        )
    )

    # Read status data — in-memory first, then disk fallback
    status_key = (project, sprint_label)
    status_data: dict = srv._sprint_statuses.get(status_key, {})

    if not status_data:
        state_path = commander / "sprints" / f"{sprint_label}-state.json"
        if state_path.exists():
            try:
                status_data = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception:
                pass

    issues: list[dict] = status_data.get("issues", [])
    started_at_str: Optional[str] = status_data.get("start_timestamp")
    now_utc = datetime.now(timezone.utc)

    # Time elapsed
    time_spent_sec = 0
    if started_at_str:
        try:
            started_at_dt = datetime.fromisoformat(started_at_str.rstrip("Z"))
            if started_at_dt.tzinfo is None:
                started_at_dt = started_at_dt.replace(tzinfo=timezone.utc)
            time_spent_sec = max(0, int((now_utc - started_at_dt).total_seconds()))
        except Exception:
            pass

    # Per-ticket progress (SQLite-backed, no GitHub API)
    _agent_run_rows = _live_metrics._fetch_sprint_agent_run_rows(sprint_label, project)
    _runs_by_issue = _live_metrics.runs_by_issue(_agent_run_rows)

    estimates: dict = status_data.get("estimates", {})

    done_count = 0
    failed_count = 0
    skipped_count = 0
    issues_out: list[dict] = []

    for iss in issues:
        num = iss.get("number")
        raw_agent_status = iss.get("agent_status")

        if raw_agent_status in _IN_FLIGHT_AGENT_STATUSES:
            derived_status = "in-progress"
        else:
            derived_status = iss.get("status", "pending")

        if derived_status == "done":
            done_count += 1
        elif raw_agent_status == "failed":
            failed_count += 1
        elif derived_status == "skipped" and raw_agent_status != "failed":
            skipped_count += 1

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

        issue_elapsed = _live_metrics.issue_elapsed_secs(iss, now_utc, _runs_by_issue)
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

        raw_size, raw_minutes = _local_issue_size_and_minutes(commander, num, estimates)

        issues_out.append({
            "number":               num,
            "title":                iss.get("title", ""),
            "status":               derived_status,
            "agent_status":         public_agent_status,
            "agent":                active_role,
            "elapsed_secs":         issue_elapsed,
            "size":                 raw_size,
            "minutes":              raw_minutes,
            "dispatch_level":       iss.get("dispatch_level", 0),
            "coder_model":          iss.get("coder_model"),
            "coder_backend":        iss.get("coder_backend"),
            "coder_provider":       iss.get("coder_provider"),
            "tester_attempt_count": tac,
            "coder_attempt":        coder_attempt,
            "pipeline_stage":       pipeline_stage,
            "category":             iss.get("category"),
            "failure_reason":       iss.get("failure_reason"),
        })

    total_count = len(issues)
    complete_count = done_count + failed_count + skipped_count
    pending_count = total_count - complete_count

    # Estimated remaining (from local estimate data only)
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
        wall_secs = float(status_data.get("wall_clock_secs", 0.0))
        avg_secs = wall_secs / complete_count if complete_count > 0 else 0
        est_remaining_minutes = max(0, round(avg_secs * pending_count / 60))

    if est_remaining_minutes is None and total_count > 0:
        est_remaining_minutes = 0

    # Current ticket
    _ACTIVE_AGENT = ("coder_dispatched", "coder_running", "tester_dispatched", "tester_running")
    active_iss = [i for i in issues if i.get("agent_status") in _ACTIVE_AGENT]
    in_progress_iss = [i for i in issues if i.get("status") == "in-progress"]
    current_ticket: Optional[dict] = None
    if active_iss:
        iss = active_iss[-1]
        current_ticket = {"number": iss.get("number"), "title": iss.get("title", "")}
    elif in_progress_iss:
        iss = in_progress_iss[-1]
        current_ticket = {"number": iss.get("number"), "title": iss.get("title", "")}
    else:
        pending = [i for i in issues if i.get("status") not in ("done", "skipped")]
        if pending:
            iss = pending[0]
            current_ticket = {"number": iss.get("number"), "title": iss.get("title", "")}

    levels_out = _live_metrics.compute_levels(issues)

    # Active agents from state.json coder/tester timestamps (local disk)
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
    active_coder_slots = len(coder_entries)

    payload: dict = {
        "sprint_label":          sprint_label,
        "project":               project,
        "source":                "plan",
        "time_spent_sec":        time_spent_sec,
        "started_at":            started_at_str,
        "current_ticket":        current_ticket,
        "active_agents":         active_agents,
        "pipeline_mode":         bool(status_data.get("pipeline_mode", False)),
        "levels":                levels_out,
        "done_count":            done_count,
        "failed_count":          failed_count,
        "skipped_count":         skipped_count,
        "pending_count":         pending_count,
        "total_count":           total_count,
        "complete_count":        complete_count,
        "est_remaining_minutes": est_remaining_minutes,
        "issues":                issues_out,
        "llm_provider":          status_data.get("llm_provider"),
        "active_coder_slots":    active_coder_slots,
        "active_tester_slots":   active_tester_slots,
        **_live_metrics.lane_capacity(status_data),
        **_live_metrics.running_metrics(sprint_label, project),
    }

    # Attach API-dispatch progress when present for this label (#2355).
    from services.sprint_manager.dispatch_runner import (
        dispatch_live_fields,
        list_dispatch_runs,
    )
    dispatch_runs = list_dispatch_runs(
        project_root, sprint_label=sprint_label, repo=project,
    )
    if dispatch_runs:
        payload["dispatch"] = dispatch_live_fields(dispatch_runs[0])
        payload["run_id"] = dispatch_runs[0].get("run_id")

    if plan_terminal:
        # Freeze in-flight fields for a sprint that has ended
        ended_at_str = plan.get("ended_at")
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
