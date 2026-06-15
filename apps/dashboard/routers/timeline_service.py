"""Service logic for the sprint timeline endpoint (issue #1146).

Builds the structured timeline data for the running-sprint pane. Data sources:
  - GitHub labels: authoritative issue status (done | running | queued)
  - DB agent_runs: authoritative segment times (start, end, duration, agent)
  - Settings KV: calibrated estimate base values + reviewer_enabled flag

No render-time disk reads — all segment data comes from the agent_runs table.
"""
from __future__ import annotations

import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Path setup ────────────────────────────────────────────────────────────────
_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
_SERVICES_ROOT = _DASHBOARD_ROOT.parent.parent / "services" / "sprint_manager"
for _p in (str(_DASHBOARD_ROOT), str(_SERVICES_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import db as _db  # noqa: E402
import settings_repo as _settings_repo  # noqa: E402
from settings_schema import APP_CONFIG_KEY  # noqa: E402

# Minimum analytics samples required to override settings value with calibrated median.
_MIN_CALIBRATION_SAMPLES = 3

# Fraction of total estimate attributed to coder vs tester in pipeline overlap model.
_CODER_FRAC = 0.6
_TESTER_FRAC = 0.4

# Agents that belong on individual issue segments (excludes wrap-up agents).
_ISSUE_AGENTS = {"coder", "tester"}

# Size label → settings key
_SIZE_TO_KEY = {
    "S": "estimation_s_minutes",
    "M": "estimation_m_minutes",
    "L": "estimation_l_minutes",
    "XL": "estimation_xl_minutes",
}


# ── Injectable helpers (monkeypatched in tests) ───────────────────────────────

def _server_now() -> datetime:
    return datetime.now(timezone.utc)


def _get_sprint_issues(sprint_label: str, project: str) -> list[dict]:
    """Return issues belonging to this sprint label. GitHub is the source of truth."""
    srv = _server()
    try:
        issues = srv.github_client.list_open_issues_with_body(repo_name=project, limit=300)
        return [i for i in issues if _primary_sprint_label(i) == sprint_label]
    except Exception:
        return []


def _get_agent_runs(sprint_label: str) -> list[dict]:
    """Return agent_runs rows for a sprint from DB (metrics truth)."""
    try:
        return _db.agent_runs_for_sprint(sprint_label)
    except Exception:
        return []


def _get_settings(project: str) -> dict:
    """Return effective settings for project (includes global defaults)."""
    try:
        stored = _settings_repo.get_setting(APP_CONFIG_KEY, project=project)
        from settings_schema import KNOWN_FIELDS
        defaults = {k: v["default"] for k, v in KNOWN_FIELDS.items() if not v.get("secret")}
        return {**defaults, **stored}
    except Exception:
        from settings_schema import KNOWN_FIELDS
        return {k: v["default"] for k, v in KNOWN_FIELDS.items() if not v.get("secret")}


def _get_sprint_row(sprint_label: str) -> Optional[dict]:
    """Return the sprints lifecycle row for a label."""
    try:
        return _db.get_sprint(sprint_label)
    except Exception:
        return None


def _get_calibration_records() -> list:
    """Return SQLite-sourced calibration records [{size, actual_minutes}]."""
    try:
        from calibration import sqlite_calibration_records
        return sqlite_calibration_records()
    except Exception:
        return []


# ── Private helpers ───────────────────────────────────────────────────────────

def _server():
    import server  # noqa: PLC0415 — intentional late import
    return server


def _primary_sprint_label(issue: dict) -> Optional[str]:
    """Return the first sprint-N label on an issue, or None."""
    for lbl in issue.get("labels", []):
        name = lbl.get("name", "")
        if name.startswith("sprint-"):
            return name
    return None


def _classify_status(issue: dict) -> str:
    """Map GitHub labels to timeline status: done | running | queued."""
    state = issue.get("state", "open")
    label_names = {lbl["name"] for lbl in issue.get("labels", [])}
    if state == "closed" or "UAT-approved" in label_names or "UAT" in label_names:
        return "done"
    if "in-progress" in label_names or "SIT" in label_names:
        return "running"
    return "queued"


def _issue_size(issue: dict) -> Optional[str]:
    """Return size letter (S/M/L/XL) from labels, or None if not labelled."""
    for lbl in issue.get("labels", []):
        name = lbl.get("name", "")
        if name.startswith("size-"):
            size = name[5:].upper()
            if size in _SIZE_TO_KEY:
                return size
    return None


def _calibrated_estimate(size: Optional[str], settings: dict, cal_buckets: dict) -> float:
    """Return calibrated estimate minutes for an issue of given size.

    Priority:
      1. Analytics actuals (median) when >= _MIN_CALIBRATION_SAMPLES for this size
      2. Settings value (estimation_<s>_minutes)
      3. 0 when size is unknown
    """
    if size is None:
        return 0.0
    key = _SIZE_TO_KEY.get(size)
    if key is None:
        return 0.0
    settings_minutes = float(settings.get(key, 0) or 0)
    samples = cal_buckets.get(size, [])
    if len(samples) >= _MIN_CALIBRATION_SAMPLES:
        return float(round(statistics.median(samples)))
    return settings_minutes


def _build_cal_buckets(cal_records: list) -> dict:
    """Group calibration records by size → list of actual_minutes."""
    buckets: dict[str, list] = {}
    for rec in cal_records:
        size = rec.get("size", "")
        mins = rec.get("actual_minutes")
        if size in _SIZE_TO_KEY and isinstance(mins, (int, float)) and mins > 0:
            buckets.setdefault(size, []).append(float(mins))
    return buckets


def _build_segments(runs: list[dict], now: datetime) -> list[dict]:
    """Convert agent_runs rows to sorted segment dicts (coder/tester only)."""
    segs = []
    for run in runs:
        agent = run.get("agent", "")
        if agent not in _ISSUE_AGENTS:
            continue
        started = run.get("started_at")
        if not started:
            continue
        finished = run.get("finished_at")
        dur = run.get("duration_seconds")

        # Compute duration if missing but end is known
        if dur is None and started and finished:
            s = _dt_from_iso(str(started))
            e = _dt_from_iso(str(finished))
            if s and e:
                dur = int((e - s).total_seconds())

        # For open (running) segments, compute elapsed up to now
        if finished is None and dur is None and started:
            s = _dt_from_iso(str(started))
            if s:
                dur = int((now - s).total_seconds())

        segs.append({
            "agent": agent,
            "start": str(started),
            "end": str(finished) if finished else None,
            "duration": dur,
        })

    # Sort by start time (chronological)
    segs.sort(key=lambda seg: seg.get("start") or "")
    return segs


def _elapsed_for_issue(runs: list[dict], now: datetime) -> float:
    """Total elapsed minutes for an issue (sum of all agent_runs, including open)."""
    total_s = 0.0
    for run in runs:
        agent = run.get("agent", "")
        if agent not in _ISSUE_AGENTS:
            continue
        dur = run.get("duration_seconds")
        if dur is not None:
            total_s += float(dur)
        elif run.get("started_at") and not run.get("finished_at"):
            s = _dt_from_iso(str(run["started_at"]))
            if s:
                total_s += (now - s).total_seconds()
    return total_s / 60.0


def _iso_from_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _dt_from_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        # Treat naive timestamps (no tzinfo) as UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def get_timeline(sprint_label: str, project: str) -> dict:
    """Return structured timeline data for a sprint.

    Response shape:
      {
        sprint_started_at: str | null,
        server_now: str,
        issues: [
          {
            number: int,
            title: str,
            status: "done" | "running" | "queued",
            size: str | null,
            calibrated_estimate: float,     # minutes
            estimated_remaining: float,     # minutes (running only)
            segments: [
              {agent, start, end, duration}  # coder/tester only
            ]
          }
        ],
        wrap_up_estimate: {
          documenter: float,                # minutes
          reviewer: float,                  # minutes (only when reviewer_enabled)
          total: float
        },
        projected_finish: str              # ISO-8601 timestamp
      }
    """
    now = _server_now()
    settings = _get_settings(project)
    cal_records = _get_calibration_records()
    cal_buckets = _build_cal_buckets(cal_records)
    pipeline_mode = bool(settings.get("pipeline_mode", False))
    reviewer_enabled = bool(settings.get("reviewer_enabled", False))

    # Sprint lifecycle row for sprint_started_at
    sprint_row = _get_sprint_row(sprint_label)
    sprint_started_at = sprint_row.get("started_at") if sprint_row else None

    # GitHub issues (status truth)
    github_issues = _get_sprint_issues(sprint_label, project)

    # DB agent_runs (metrics truth), grouped by issue number
    all_runs = _get_agent_runs(sprint_label)
    runs_by_issue: dict[int, list[dict]] = {}
    for run in all_runs:
        num = run.get("issue_number")
        if num is not None:
            runs_by_issue.setdefault(int(num), []).append(run)

    # Build per-issue data
    issue_list = []
    running_remaining_min: Optional[float] = None
    queued_estimates: list[float] = []

    for gh_iss in github_issues:
        number = gh_iss.get("number")
        status = _classify_status(gh_iss)
        size = _issue_size(gh_iss)
        est = _calibrated_estimate(size, settings, cal_buckets)

        issue_runs = runs_by_issue.get(int(number) if number is not None else -1, [])
        segments = _build_segments(issue_runs, now)

        entry: dict = {
            "number": number,
            "title": gh_iss.get("title", ""),
            "url": gh_iss.get("url", ""),
            "status": status,
            "size": size,
            "calibrated_estimate": est,
            "estimated_remaining": None,
            "segments": segments,
        }

        if status == "running":
            elapsed = _elapsed_for_issue(issue_runs, now)
            remaining = max(0.0, est - elapsed)
            entry["estimated_remaining"] = remaining
            if running_remaining_min is None:
                running_remaining_min = remaining
            else:
                running_remaining_min = max(running_remaining_min, remaining)
        elif status == "queued":
            queued_estimates.append(est)

        issue_list.append(entry)

    # Wrap-up estimate (sprint level)
    doc_min = float(settings.get("estimation_m_minutes", 15) or 15)
    rev_min = float(settings.get("estimation_m_minutes", 15) or 15)
    wrap_up: dict = {"documenter": doc_min}
    if reviewer_enabled:
        wrap_up["reviewer"] = rev_min
    wrap_up["total"] = doc_min + (rev_min if reviewer_enabled else 0.0)

    # Projected finish
    remaining_running = running_remaining_min or 0.0

    if pipeline_mode:
        queue_time = _pipeline_queue_time(queued_estimates)
    else:
        queue_time = sum(queued_estimates)

    total_remaining_min = remaining_running + queue_time + wrap_up["total"]
    from datetime import timedelta
    projected_dt = now + timedelta(minutes=total_remaining_min)
    projected_finish = _iso_from_dt(projected_dt)

    return {
        "sprint_started_at": sprint_started_at,
        "server_now": _iso_from_dt(now),
        "issues": issue_list,
        "wrap_up_estimate": wrap_up,
        "projected_finish": projected_finish,
    }


def _pipeline_queue_time(queued_estimates: list[float]) -> float:
    """Compute pipeline-mode queue time for N queued issues.

    In pipeline mode, while issue N's tester runs, issue N+1's coder runs.
    Critical path = sum(coder fractions for all issues) + last_tester_fraction.
    This yields a shorter total than serial (sum of all estimates).
    """
    if not queued_estimates:
        return 0.0
    coder_total = sum(e * _CODER_FRAC for e in queued_estimates)
    last_tester = queued_estimates[-1] * _TESTER_FRAC
    return coder_total + last_tester
