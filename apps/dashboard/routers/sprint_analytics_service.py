"""Service layer for sprint analytics and metrics routes (issue #1252).

Extracted from server.py: estimate-summary, estimate, outcome,
estimate-vs-actual, estimates/batch, calibration, metrics/sprints.
All file I/O and business logic lives here; route handlers stay thin.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Path bootstrap ────────────────────────────────────────────────────────────
_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
_SERVICES_ROOT = _DASHBOARD_ROOT.parent.parent / "services" / "sprint_manager"
for _p in (str(_DASHBOARD_ROOT), str(_SERVICES_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import db as _db                                # noqa: E402
import github_client as _github_client          # noqa: E402
import projects as _projects_module             # noqa: E402
from fastapi import HTTPException               # noqa: E402

try:
    import services.sprint_manager.settings_repo as _settings_repo  # noqa: E402
    from services.sprint_manager.settings_schema import (
        APP_CONFIG_KEY as _APP_CONFIG_KEY,
        build_effective_response as _build_effective_response,
    )
    _SETTINGS_AVAILABLE = True
except Exception:
    _settings_repo = None  # type: ignore[assignment]
    _APP_CONFIG_KEY = "app_config"
    _build_effective_response = None  # type: ignore[assignment]
    _SETTINGS_AVAILABLE = False

try:
    from sizing import SIZE_TO_MINUTES as _SIZE_TO_MINUTES  # noqa: E402
except Exception:
    _SIZE_TO_MINUTES: dict = {"S": 5, "M": 15, "L": 30, "XL": 60}

_log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_PROJECTS_BASE = Path.home() / "dev"

_SPRINT_LABEL_RE = re.compile(r"^sprint-\d+(\.\d+)?$")

_CALIBRATION_SIZES = ("S", "M", "L", "XL")
_CALIBRATION_SIZE_SETTING_KEYS = {
    "S": "estimation_s_minutes",
    "M": "estimation_m_minutes",
    "L": "estimation_l_minutes",
    "XL": "estimation_xl_minutes",
}
_CALIBRATION_CACHE_VERSION = 1
_CALIBRATION_DONE_STATUSES = frozenset({"done", "uat", "merged", "passed"})

_NOT_RUNNING_PLAN_STATES: frozenset[str] = frozenset({
    "completed", "ready_to_merge", "needs_rework", "cancelled",
    "draft", "planned", "planning",
})

_OUTCOME_TERMINAL_STATES = frozenset({"completed", "needs_rework", "ready_to_merge", "deleted"})
_CHILD_SETTLED_STATES = frozenset({"completed", "deleted"})

# ── Path helpers ──────────────────────────────────────────────────────────────


def _project_root_path(repo: str) -> Path:
    slug = repo.split("/")[-1] if "/" in repo else repo
    return _PROJECTS_BASE / slug


def _commander_dir(project_root: Path) -> Path:
    return project_root / ".commander"


def _sprint_plan_path(project_root: Path, sprint_label: str) -> Path:
    return _commander_dir(project_root) / "sprints" / f"{sprint_label}-plan.json"


def _sprint_json_path(project_root: Path, sprint_label: str) -> Path:
    return _commander_dir(project_root) / "sprints" / f"{sprint_label}.json"


def _sprint_json_read(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _read_plan_json(project_root: Path, sprint_label: str) -> Optional[dict]:
    path = _sprint_plan_path(project_root, sprint_label)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return {"tickets": raw}
        if isinstance(raw, dict):
            return raw
    except (OSError, json.JSONDecodeError):
        pass
    return None


def _resolve_project_slug(slug: str) -> str:
    try:
        all_projects = _projects_module.load_projects()
    except Exception:
        all_projects = []
    matched = next(
        (p for p in all_projects if p["repo"].split("/")[-1] == slug or p["repo"] == slug),
        None,
    )
    if matched is None:
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
    return matched["repo"]


def _size_to_minutes(size: str) -> int:
    return _SIZE_TO_MINUTES.get(size, 0)


# ── Sprint running / PID helpers ──────────────────────────────────────────────


def _sprint_pid_alive(project_root: Path, sprint_label: str) -> bool:
    sprints_dir = _commander_dir(project_root) / "sprints"
    pid_file = sprints_dir / f"{sprint_label}-pid"
    pending_file = sprints_dir / f"{sprint_label}-pid.pending"
    for candidate in (pid_file, pending_file):
        if not candidate.exists():
            continue
        try:
            raw = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if raw in ("", "0"):
            return True
        try:
            pid = int(raw)
        except ValueError:
            try:
                candidate.unlink()
            except OSError:
                pass
            continue
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            try:
                candidate.unlink()
            except OSError:
                pass
        except PermissionError:
            return True
        except OSError:
            pass
    return False


def _is_sprint_running(project_root: Path, sprint_label: str) -> bool:
    try:
        _db_row = _db.get_sprint(sprint_label)
    except Exception:
        _db_row = None
    if _db_row is not None:
        _row_proj = str(_db_row.get("project") or "")
        _row_slug = _row_proj.split("/")[-1] if "/" in _row_proj else _row_proj
        if _row_slug != project_root.name:
            _db_row = None
    if _db_row is not None:
        if _db_row.get("state") != "running":
            return False
        if _sprint_pid_alive(project_root, sprint_label):
            return True
        _log.warning(
            "Sprint %s: DB state=running but no alive PID — reporting not running",
            sprint_label,
        )
        return False

    plan = _read_plan_json(project_root, sprint_label)
    if plan is not None:
        plan_state = plan.get("state")
        if plan_state in _NOT_RUNNING_PLAN_STATES:
            return False
        if plan_state == "running":
            return _sprint_pid_alive(project_root, sprint_label)

    return _sprint_pid_alive(project_root, sprint_label)


# ── Outcome helpers ───────────────────────────────────────────────────────────


def _state_data_is_dry_run_only(state_data: dict) -> bool:
    issues = state_data.get("issues") or []
    if not issues:
        return False
    if any(i.get("coder_started_at") or i.get("tester_started_at") for i in issues):
        return False
    return any((i.get("skip_reason") or "").lower() == "dry-run" for i in issues)


def _sprint_has_own_run_outcome(project_root: Path, sprint_label: str) -> bool:
    plan = _read_plan_json(project_root, sprint_label)
    if plan and plan.get("state") in ("planning", "draft", "planned"):
        return False

    row = _db.get_sprint(sprint_label)
    if row and row.get("run_ingested_at"):
        return True

    from routers import sprint_artifact_service  # noqa: PLC0415
    sprints_dir = _commander_dir(project_root) / "sprints"
    resolved = sprint_artifact_service.resolve_state_path(sprints_dir, sprint_label)
    if resolved is None:
        return False
    try:
        state_data = json.loads(resolved.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return True
    return not _state_data_is_dry_run_only(state_data)


def _parse_summary_file(path: Path) -> dict:
    name = path.stem
    m = re.match(r"sprint-(\d+)-summary-(\d{4}-\d{2}-\d{2})", name)
    sprint_num = int(m.group(1)) if m else None
    date = m.group(2) if m else ""
    content = path.read_text(encoding="utf-8")
    status_m = re.search(r"^## Sprint \S+ — (\S+)", content, re.MULTILINE)
    status = status_m.group(1) if status_m else "unknown"
    shipped_count = 0
    in_shipped = False
    for line in content.splitlines():
        if line.startswith("## Pending UAT Review") or line.startswith("## What Shipped"):
            in_shipped = True
            continue
        if in_shipped and line.startswith("## "):
            break
        if in_shipped and line.startswith("|") and not line.startswith("| Issue") and "|---|" not in line:
            cell = line.split("|")[1].strip()
            if cell and cell != "—":
                shipped_count += 1
    skipped_count = 0
    in_skipped = False
    for line in content.splitlines():
        if line.startswith("## What Didn't Ship"):
            in_skipped = True
            continue
        if in_skipped and line.startswith("## "):
            break
        if in_skipped and line.startswith("|") and not line.startswith("| Issue") and "|---|" not in line:
            cell = line.split("|")[1].strip()
            if cell and cell != "—":
                skipped_count += 1
    total_tokens = 0
    tok_m = re.search(r"\|\s*Total tokens\s*\|\s*(\d+)\s*\|", content)
    if tok_m:
        total_tokens = int(tok_m.group(1))
    return {
        "sprint_num": sprint_num,
        "date": date,
        "status": status,
        "shipped_count": shipped_count,
        "skipped_count": skipped_count,
        "total_tokens": total_tokens,
    }


def _has_rework_tickets(sprint_label: str, project: str) -> bool:
    NON_WORK = {"sprint-summary", "docs", "documentation"}
    REWORK = {"needs-rework", "need-rework", "tester-rejected"}
    DONE = {"UAT", "UAT-approved", "released"}
    try:
        issues = _get_sprint_issues(project, sprint_label)
    except Exception:
        return False
    for iss in issues:
        labels = {lbl["name"] for lbl in iss.get("labels", [])}
        if labels & NON_WORK:
            continue
        if labels & REWORK:
            return True
        if not (labels & DONE):
            return True
    return False


def _derive_outcome_lifecycle(
    sprint_label: str,
    project_root: Path,
    project: str,
    plan_state: str,
    pane_state: str,
    failed_count: int,
) -> str:
    row = _db.get_sprint(sprint_label)
    if row is None:
        return _db.canonical_lifecycle(pane_state)
    parent_state = _db.canonical_lifecycle(row["state"])
    if parent_state not in _OUTCOME_TERMINAL_STATES:
        return parent_state
    children = _db.get_sprint_children(sprint_label)
    if not children:
        return parent_state
    unsettled = [
        c for c in children
        if _db.canonical_lifecycle(c["state"]) not in _CHILD_SETTLED_STATES
    ]
    if unsettled:
        return "partial_finished"
    return parent_state


def _outcome_from_ingested_row(row: dict, sprint_label: str, project: str) -> dict:
    from routers import sprint_artifact_service  # noqa: PLC0415

    enrich = sprint_artifact_service.enrichment_from_db_row(row)
    stored_state = row.get("state") or ""
    lifecycle = _db.canonical_lifecycle(stored_state)
    end_reason = row.get("end_reason")
    if lifecycle == "needs_rework" and (end_reason or "") == "natural":
        try:
            _raw = json.loads(row.get("issues_json") or "[]")
            if _raw and all(
                (i.get("state") or "").lower() == "merged"
                or (i.get("agent_status") or "").lower() in ("completed", "done")
                for i in _raw
            ):
                lifecycle = "ready_to_merge"
        except (json.JSONDecodeError, TypeError):
            pass
    is_cancelled = lifecycle == "needs_rework" and (end_reason or "").startswith("stopped")

    try:
        issues_raw = json.loads(row.get("issues_json") or "[]")
    except (json.JSONDecodeError, TypeError):
        issues_raw = []

    result_issues = []
    for iss in issues_raw:
        tid = iss.get("ticket_id") or iss.get("number")
        agent = (iss.get("agent_status") or "").lower()
        fr = iss.get("failure_reason")
        st = (iss.get("state") or "").lower()
        if st == "merged" or agent in ("completed", "done"):
            outcome = "done"
        elif agent == "failed" or fr:
            outcome = "failed"
        else:
            outcome = "skipped"
        result_issues.append({
            "number": tid,
            "title": iss.get("title", ""),
            "outcome": outcome,
            "elapsed_secs": iss.get("time_spent"),
            "failure_reason": fr,
        })

    try:
        from routers import sprint_history_service  # noqa: PLC0415
        _seen = {str(i["number"]) for i in result_issues if i.get("number") is not None}
        for _extra in sprint_history_service._issues_from_agent_runs(sprint_label):
            _tid = _extra.get("ticket_id")
            if _tid is None:
                _tid = _extra.get("number")
            if _tid is None or int(_tid) <= 0:
                continue
            _eid = str(_tid)
            if _eid in _seen:
                continue
            _st = (_extra.get("state") or "").lower()
            _oc = "done" if _st == "merged" else ("failed" if _st == "closed" else "skipped")
            result_issues.append({
                "number": int(_tid),
                "title": _extra.get("title", ""),
                "outcome": _oc,
                "elapsed_secs": None,
                "failure_reason": None,
            })
            _seen.add(_eid)
    except Exception:
        pass

    if is_cancelled:
        pane_state = "cancelled"
        sprint_status = "stopped"
    elif _has_rework_tickets(sprint_label, project):
        pane_state = "has_rework"
        sprint_status = "stopped"
    else:
        pane_state = "completed"
        sprint_status = "completed"

    done_count = sum(1 for i in result_issues if i["outcome"] == "done")
    failed_count = sum(1 for i in result_issues if i["outcome"] == "failed")
    skipped_count = sum(1 for i in result_issues if i["outcome"] == "skipped")

    surl = enrich.get("summary_issue_url")
    summary_issue_num = enrich.get("summary_issue_num")
    pr_number = row.get("pr_number") if row.get("pr_number") is not None else enrich.get("pr_number")
    pr_url = None
    if pr_number:
        try:
            pr_repo = _github_client.get_repo_for_operation(project)
            pr_url = f"https://github.com/{pr_repo}/pull/{int(pr_number)}"
        except Exception:
            pr_url = None

    return {
        "sprint_label": sprint_label,
        "state": pane_state,
        "lifecycle": lifecycle,
        "end_reason": end_reason,
        "sprint_status": sprint_status,
        "counts": {
            "done": done_count,
            "failed": failed_count,
            "skipped": skipped_count,
        },
        "wall_clock_secs": enrich.get("duration") or row.get("wall_clock_secs") or 0,
        "ended_at": None,
        "issues": result_issues,
        "log_line_count": 0,
        "summary_issue_url": surl,
        "summary_issue_num": summary_issue_num,
        "pr_number": pr_number,
        "pr_url": pr_url,
    }


# ── GitHub helpers ────────────────────────────────────────────────────────────


def _primary_sprint_label(iss: dict) -> Optional[str]:
    for lbl in iss.get("labels", []):
        if _SPRINT_LABEL_RE.match(lbl["name"]):
            return lbl["name"]
    return None


def _get_sprint_issues(project: str, sprint_label: str) -> list[dict]:
    issues = _github_client.list_open_issues_with_body(repo_name=project, limit=200)
    return [iss for iss in issues if _primary_sprint_label(iss) == sprint_label]


# ── Calibration helpers ───────────────────────────────────────────────────────


def _analytics_parse_ts(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.rstrip("Z")).replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _analytics_elapsed_minutes(start: Optional[str], end: Optional[str]) -> Optional[float]:
    s = _analytics_parse_ts(start)
    e = _analytics_parse_ts(end)
    if s is None or e is None:
        return None
    return (e - s).total_seconds() / 60.0


def _parse_iso_date(date_str: str, name: str, *, end_of_day: bool = False) -> datetime:
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(
            400, detail=f"Invalid {name!r} date {date_str!r} — expected YYYY-MM-DD"
        )
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    return dt


def _calibration_empty_by_size() -> dict[str, dict]:
    return {
        sz: {"count": 0, "min_minutes": None, "avg_minutes": None, "max_minutes": None}
        for sz in _CALIBRATION_SIZES
    }


def _calibration_empty_cache() -> dict:
    return {
        "version": _CALIBRATION_CACHE_VERSION,
        "archive_bootstrap_done": False,
        "by_size": _calibration_empty_by_size(),
        "processed": [],
        "points": [],
    }


def _calibration_cache_path(commander_dir: Path) -> Path:
    return commander_dir / "calibration_cache.json"


def _load_calibration_cache(commander_dir: Path) -> dict:
    path = _calibration_cache_path(commander_dir)
    if not path.is_file():
        return _calibration_empty_cache()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _calibration_empty_cache()
    if data.get("version") != _CALIBRATION_CACHE_VERSION:
        return _calibration_empty_cache()
    by_size = data.get("by_size") or {}
    for sz in _CALIBRATION_SIZES:
        if sz not in by_size:
            by_size[sz] = _calibration_empty_by_size()[sz]
    data["by_size"] = by_size
    if not isinstance(data.get("processed"), list):
        data["processed"] = []
    if not isinstance(data.get("points"), list):
        data["points"] = []
    return data


def _save_calibration_cache(commander_dir: Path, cache: dict) -> None:
    path = _calibration_cache_path(commander_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def _calibration_add_sample(cache: dict, size: str, actual_minutes: float, point: Optional[dict] = None) -> None:
    bucket = cache["by_size"][size]
    val = round(actual_minutes, 2)
    count = int(bucket["count"] or 0)
    if count == 0:
        bucket["count"] = 1
        bucket["min_minutes"] = val
        bucket["avg_minutes"] = val
        bucket["max_minutes"] = val
    else:
        old_avg = float(bucket["avg_minutes"])
        new_count = count + 1
        bucket["avg_minutes"] = round((old_avg * count + val) / new_count, 2)
        bucket["count"] = new_count
        bucket["min_minutes"] = round(min(float(bucket["min_minutes"]), val), 2)
        bucket["max_minutes"] = round(max(float(bucket["max_minutes"]), val), 2)
    if point is not None:
        cache["points"].append(point)


def _calibration_issue_sample(
    issue: dict,
    estimates_dir: Path,
    configured_minutes: dict[str, int],
) -> Optional[tuple]:
    if issue.get("status") not in _CALIBRATION_DONE_STATUSES:
        return None
    issue_num = issue.get("number")
    size = None
    if issue_num is not None and estimates_dir.is_dir():
        est_file = estimates_dir / f"issue-{issue_num}.json"
        if est_file.is_file():
            try:
                est = json.loads(est_file.read_text(encoding="utf-8"))
                size = est.get("size")
            except (json.JSONDecodeError, OSError):
                size = None
    if size not in _CALIBRATION_SIZES:
        return None
    coder_min = _analytics_elapsed_minutes(issue.get("coder_started_at"), issue.get("coder_finished_at"))
    tester_min = _analytics_elapsed_minutes(issue.get("tester_started_at"), issue.get("tester_finished_at"))
    if coder_min is None and tester_min is None:
        return None
    actual_minutes = (coder_min or 0.0) + (tester_min or 0.0)
    point = {
        "issue_number": issue_num,
        "estimated_size": size,
        "estimated_minutes": configured_minutes[size],
        "actual_minutes": round(actual_minutes, 2),
    }
    return size, actual_minutes, point


def _calibration_state_key(state_file: Path, sprints_dir: Path, issue_num: int) -> str:
    rel = state_file.relative_to(sprints_dir)
    return f"{rel.as_posix()}/{issue_num}"


def _calibration_absorb_state_file(
    cache: dict,
    state_file: Path,
    sprints_dir: Path,
    estimates_dir: Path,
    configured_minutes: dict[str, int],
    processed: set,
) -> bool:
    try:
        state_data = json.loads(state_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    changed = False
    for issue in state_data.get("issues", []):
        issue_num = issue.get("number")
        if issue_num is None:
            continue
        key = _calibration_state_key(state_file, sprints_dir, issue_num)
        if key in processed:
            continue
        sample = _calibration_issue_sample(issue, estimates_dir, configured_minutes)
        if sample is None:
            continue
        size, actual_minutes, point = sample
        _calibration_add_sample(cache, size, actual_minutes, point)
        cache["processed"].append(key)
        processed.add(key)
        changed = True
    return changed


def _refresh_calibration_cache(project_root: Path, configured_minutes: dict[str, int]) -> dict:
    commander = _commander_dir(project_root)
    sprints_dir = commander / "sprints"
    estimates_dir = commander / "estimates"
    cache = _load_calibration_cache(commander)
    processed = set(cache.get("processed") or [])
    changed = False
    if not sprints_dir.is_dir():
        return cache
    archive_dir = sprints_dir / "archive"
    if archive_dir.is_dir():
        for state_file in sorted(archive_dir.glob("sprint-*-state.json")):
            if _calibration_absorb_state_file(
                cache, state_file, sprints_dir, estimates_dir, configured_minutes, processed
            ):
                changed = True
    for state_file in sorted(sprints_dir.glob("sprint-*-state.json")):
        if _calibration_absorb_state_file(cache, state_file, sprints_dir, estimates_dir, configured_minutes, processed):
            changed = True
    if not cache.get("archive_bootstrap_done"):
        cache["archive_bootstrap_done"] = True
        changed = True
    if changed:
        _save_calibration_cache(commander, cache)
    return cache


def _iter_calibration_state_files(sprints_dir: Path, *, include_archive: bool = False) -> list:
    if not sprints_dir.is_dir():
        return []
    roots = [sprints_dir]
    if include_archive:
        archive = sprints_dir / "archive"
        if archive.is_dir():
            roots.append(archive)
    files: list = []
    for root in roots:
        files.extend(sorted(root.glob("sprint-*-state.json")))
    return files


def _compute_calibration_from_files(
    project_root: Path,
    configured_minutes: dict[str, int],
    since_dt: Optional[datetime],
    until_dt: Optional[datetime],
    sprint_filter: Optional[str],
    *,
    include_archive: bool,
) -> dict:
    sprints_dir = _commander_dir(project_root) / "sprints"
    estimates_dir = _commander_dir(project_root) / "estimates"
    by_size = {
        sz: {"configured_minutes": configured_minutes[sz], **_calibration_empty_by_size()[sz]}
        for sz in _CALIBRATION_SIZES
    }
    buckets: dict[str, list] = {sz: [] for sz in _CALIBRATION_SIZES}
    points: list = []
    for state_file in _iter_calibration_state_files(sprints_dir, include_archive=include_archive):
        try:
            state_data = json.loads(state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if sprint_filter and state_data.get("sprint_label", "") != sprint_filter:
            continue
        start_dt = _analytics_parse_ts(state_data.get("start_timestamp"))
        if start_dt:
            if since_dt and start_dt < since_dt:
                continue
            if until_dt and start_dt > until_dt:
                continue
        for issue in state_data.get("issues", []):
            sample = _calibration_issue_sample(issue, estimates_dir, configured_minutes)
            if sample is None:
                continue
            size, actual_minutes, point = sample
            buckets[size].append(actual_minutes)
            points.append(point)
    for sz in _CALIBRATION_SIZES:
        vals = buckets[sz]
        if vals:
            by_size[sz]["count"] = len(vals)
            by_size[sz]["min_minutes"] = round(min(vals), 2)
            by_size[sz]["avg_minutes"] = round(sum(vals) / len(vals), 2)
            by_size[sz]["max_minutes"] = round(max(vals), 2)
    return {"by_size": by_size, "points": points}


def _get_configured_minutes(repo: str) -> dict[str, int]:
    defaults = {"S": 5, "M": 15, "L": 30, "XL": 60}
    if not _SETTINGS_AVAILABLE or _settings_repo is None or _build_effective_response is None:
        return defaults
    try:
        stored = _settings_repo.get_setting(_APP_CONFIG_KEY, project=repo)
    except Exception:
        stored = {}
    try:
        effective = _build_effective_response(stored)
        return {sz: effective[key] for sz, key in _CALIBRATION_SIZE_SETTING_KEYS.items()}
    except Exception:
        return defaults


def compute_calibration(
    repo: str,
    since: Optional[str] = None,
    until: Optional[str] = None,
    sprint_filter: Optional[str] = None,
) -> dict:
    since_dt = _parse_iso_date(since, "since") if since else None
    until_dt = _parse_iso_date(until, "until", end_of_day=True) if until else None
    configured_minutes = _get_configured_minutes(repo)
    project_root = _project_root_path(repo)
    if since or until or sprint_filter:
        return _compute_calibration_from_files(
            project_root, configured_minutes, since_dt, until_dt, sprint_filter,
            include_archive=True,
        )
    cache = _refresh_calibration_cache(project_root, configured_minutes)
    by_size = {
        sz: {
            "configured_minutes": configured_minutes[sz],
            "count": cache["by_size"][sz]["count"],
            "min_minutes": cache["by_size"][sz]["min_minutes"],
            "avg_minutes": cache["by_size"][sz]["avg_minutes"],
            "max_minutes": cache["by_size"][sz]["max_minutes"],
        }
        for sz in _CALIBRATION_SIZES
    }
    return {"by_size": by_size, "points": list(cache.get("points") or [])}


# ── Service functions (called by route handlers) ──────────────────────────────


def get_estimate_summary(sprint_label: str, project: str) -> dict:
    """Compute rolled-up estimate summary for a sprint."""
    import subprocess  # noqa: PLC0415

    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")
    try:
        sprint_issues = _get_sprint_issues(project, sprint_label)
    except subprocess.CalledProcessError as e:
        detail = e.stderr.strip() if e.stderr else str(e)
        raise HTTPException(status_code=502, detail=detail)

    _SIZE_LABELS = ["S", "M", "L", "XL"]
    size_counts: dict[str, int] = {}
    unsized_numbers: list[int] = []
    total_minutes = 0
    for iss in sprint_issues:
        label_names = {lbl["name"] for lbl in iss.get("labels", [])}
        found_size = None
        for size in _SIZE_LABELS:
            if f"size-{size}" in label_names:
                found_size = size
                break
        if found_size:
            size_counts[found_size] = size_counts.get(found_size, 0) + 1
            total_minutes += _size_to_minutes(found_size)
        else:
            unsized_numbers.append(iss["number"])

    m = re.search(r"(\d+)", sprint_label)
    sprint_num = int(m.group(1)) if m else None
    sprint_name = f"Sprint {sprint_num}" if sprint_num is not None else sprint_label
    return {
        "sprint_name": sprint_name,
        "sprint_label": sprint_label,
        "total_tickets": len(sprint_issues),
        "size_counts": size_counts,
        "total_minutes": total_minutes,
        "unsized_numbers": unsized_numbers,
    }


def get_sprint_estimate(sprint_label: str, project: str) -> dict:
    """Return sprint estimate JSON file content."""
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")
    project_root = _project_root_path(project)
    commander = _commander_dir(project_root)
    m = re.search(r"(\d+)", sprint_label)
    n = m.group(1) if m else sprint_label
    estimate_path = commander / "sprints" / f"sprint-{n}-estimate.json"
    if not estimate_path.exists():
        raise HTTPException(404, detail=f"Estimate not found for {sprint_label!r}. Run the estimator first.")
    try:
        return json.loads(estimate_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise HTTPException(500, detail=f"Could not read estimate file: {e}")


def get_estimates_batch(project: str, issues: str) -> dict:
    """Return summed estimated_hours and per-issue size/confidence."""
    _SIZE_TOKENS: dict[str, int] = {"S": 5_000, "M": 15_000, "L": 30_000, "XL": 60_000}
    _COST_PER_TOKEN: float = (0.80 * 0.6 + 4.00 * 0.4) / 1_000_000

    issue_nums = [int(p) for p in issues.split(",") if p.strip().isdigit()]
    if not issue_nums:
        return {
            "total_hours": 0.0, "complete": True, "issues": {},
            "total_tokens": None, "total_cost_usd": None,
            "estimated_count": 0, "partial": False,
        }
    try:
        project_root = _project_root_path(project)
        estimates_dir = _commander_dir(project_root) / "estimates"
        if not estimates_dir.is_dir():
            return {
                "total_hours": None, "complete": False,
                "issues": {str(n): None for n in issue_nums},
                "total_tokens": None, "total_cost_usd": None,
                "estimated_count": 0, "partial": False,
            }
    except Exception:
        return {
            "total_hours": None, "complete": False,
            "issues": {str(n): None for n in issue_nums},
            "total_tokens": None, "total_cost_usd": None,
            "estimated_count": 0, "partial": False,
        }

    total = 0.0
    total_tokens = 0
    complete = True
    estimated_count = 0
    per_issue: dict = {}
    for num in issue_nums:
        path = estimates_dir / f"issue-{num}.json"
        if not path.exists():
            complete = False
            per_issue[str(num)] = None
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            h = data.get("estimated_hours")
            size = data.get("size")
            confidence = data.get("confidence")
            if h is None:
                complete = False
            else:
                total += float(h)
            tokens = _SIZE_TOKENS.get(size or "", 0)
            total_tokens += tokens
            estimated_count += 1
            per_issue[str(num)] = {
                "size": size,
                "confidence": confidence,
                "files_likely_affected": data.get("files_likely_affected", []),
                "risk_flags": data.get("risk_flags", []),
                "summary": data.get("summary", ""),
            }
        except (json.JSONDecodeError, OSError, ValueError):
            complete = False
            per_issue[str(num)] = None

    has_any = estimated_count > 0
    return {
        "total_hours": total if complete else None,
        "complete": complete,
        "issues": per_issue,
        "total_tokens": total_tokens if has_any else None,
        "total_cost_usd": total_tokens * _COST_PER_TOKEN if has_any else None,
        "estimated_count": estimated_count,
        "partial": has_any and estimated_count < len(issue_nums),
    }


def get_sprint_outcome(sprint_label: str, project: str) -> dict:
    """Return frozen outcome data for a completed or stopped sprint."""
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    project_root = _project_root_path(project)
    commander = _commander_dir(project_root)

    if _is_sprint_running(project_root, sprint_label):
        return {"sprint_label": sprint_label, "state": "running", "lifecycle": "running"}

    if not _sprint_has_own_run_outcome(project_root, sprint_label):
        raise HTTPException(404, detail=f"Outcome not found for {sprint_label!r} (not run yet)")

    ingested = _db.get_sprint(sprint_label)
    if ingested and ingested.get("run_ingested_at"):
        return _outcome_from_ingested_row(ingested, sprint_label, project)

    m = re.search(r"(\d+)", sprint_label)
    n = m.group(1) if m else sprint_label

    json_path = _sprint_json_path(project_root, sprint_label)
    sprint_json = _sprint_json_read(json_path)
    is_cancelled: bool = sprint_json.get("status") in ("cancelled", "needs_rework")

    state_path = commander / "sprints" / f"sprint-{n}-state.json"
    from routers import sprint_artifact_service  # noqa: PLC0415
    resolved = sprint_artifact_service.resolve_state_path(commander / "sprints", sprint_label)
    if resolved is not None:
        state_path = resolved
    if not state_path.exists():
        if is_cancelled:
            return {
                "sprint_label": sprint_label, "state": "cancelled",
                "lifecycle": "needs_rework",
                "end_reason": sprint_json.get("end_reason"),
            }
        raise HTTPException(404, detail=f"Outcome not found for {sprint_label!r}")

    try:
        state_data = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise HTTPException(500, detail=f"Could not read state file: {e}")

    if _state_data_is_dry_run_only(state_data):
        raise HTTPException(404, detail=f"Outcome not found for {sprint_label!r} (dry-run only)")

    plan = _read_plan_json(project_root, sprint_label)
    plan_state = (plan.get("state") or "").lower() if plan else ""
    if plan_state in ("draft", "planned", "planning"):
        raise HTTPException(404, detail=f"Outcome not found for {sprint_label!r} (not run yet)")

    if ingested and not ingested.get("run_ingested_at"):
        try:
            _db.ingest_sprint_run_artifact(sprint_label, state_data, project=project)
        except Exception:
            pass

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

    def _fmt_iso(ts: Optional[float]) -> Optional[str]:
        if ts is None:
            return None
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M")

    sprint_status: Optional[str] = None
    for sf in sorted(
        list((commander / "sprints").glob(f"{sprint_label}-summary-*.md"))
        + list((commander / "sprints").glob(f"sprint-{n}-summary-*.md")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):
        try:
            meta = _parse_summary_file(sf)
            raw = (meta.get("status") or "").lower()
            if raw in ("complete", "completed"):
                sprint_status = "completed"
            elif raw in ("stopped", "failed", "cancelled"):
                sprint_status = "stopped"
                if raw == "cancelled":
                    is_cancelled = True
        except Exception:
            pass
        break

    issues_raw = state_data.get("issues", [])
    if sprint_status is None and issues_raw:
        has_pending = any(i.get("status") == "pending" for i in issues_raw)
        has_failed = any(
            i.get("agent_status") == "failed" or i.get("failure_reason")
            for i in issues_raw
        )
        if not has_pending:
            sprint_status = "stopped" if has_failed else "completed"

    if sprint_status is None:
        raise HTTPException(404, detail=f"Cannot determine outcome for {sprint_label!r}")

    if plan_state == "needs_rework":
        pane_state = "has_rework"
    elif is_cancelled:
        pane_state = "cancelled"
    elif _has_rework_tickets(sprint_label, project):
        pane_state = "has_rework"
    else:
        pane_state = "completed"

    result_issues = []
    ended_ts: Optional[float] = None
    for iss in issues_raw:
        start_ts = _parse_iso(iss.get("coder_started_at"))
        end_ts = (
            _parse_iso(iss.get("tester_finished_at"))
            or _parse_iso(iss.get("status_changed_at"))
        )
        elapsed_secs = None
        if start_ts is not None and end_ts is not None:
            elapsed_secs = max(0.0, end_ts - start_ts)
        if end_ts and (ended_ts is None or end_ts > ended_ts):
            ended_ts = end_ts
        iss_status = iss.get("status", "pending")
        iss_agent = iss.get("agent_status")
        failure_reason = iss.get("failure_reason")
        if iss_status == "done":
            outcome = "done"
        elif iss_agent == "failed" or failure_reason:
            outcome = "failed"
        elif iss_status == "skipped":
            outcome = "skipped"
        else:
            outcome = "skipped"
        result_issues.append({
            "number": iss.get("number"),
            "title": iss.get("title", ""),
            "outcome": outcome,
            "elapsed_secs": round(elapsed_secs) if elapsed_secs is not None else None,
            "failure_reason": failure_reason,
        })

    failed_nums = [i["number"] for i in result_issues if i["outcome"] == "failed" and i["number"]]
    if failed_nums:
        import subprocess  # noqa: PLC0415
        try:
            repo = _github_client.get_repo_for_operation(project)
            r = subprocess.run(
                ["gh", "issue", "list", "--repo", repo,
                 "--state", "all", "--label", "UAT",
                 "--json", "number", "--limit", "200"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                uat_nums = {i["number"] for i in (json.loads(r.stdout) or [])}
                for ri in result_issues:
                    if ri["outcome"] == "failed" and ri["number"] in uat_nums:
                        ri["outcome"] = "done"
        except Exception:
            pass

    on_label_nums: Optional[set] = None
    try:
        on_label_nums = {iss["number"] for iss in _get_sprint_issues(project, sprint_label)}
    except Exception:
        pass
    if on_label_nums is not None:
        for ri in result_issues:
            if ri["outcome"] == "failed" and ri["number"] not in on_label_nums:
                ri["outcome"] = "rerun"

    done_count = sum(1 for i in result_issues if i["outcome"] == "done")
    failed_count = sum(1 for i in result_issues if i["outcome"] == "failed")
    skipped_count = sum(1 for i in result_issues if i["outcome"] == "skipped")

    if sprint_status == "stopped" and failed_count == 0:
        sprint_status = "completed"

    log_line_count = 0
    log_dir = commander / "logs"
    if log_dir.exists():
        candidates = sorted(
            log_dir.glob(f"sprint-run-{sprint_label}-*.log"),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        if candidates:
            try:
                log_line_count = len(candidates[0].read_text(encoding="utf-8", errors="replace").splitlines())
            except OSError:
                pass

    summary_issue_url: Optional[str] = state_data.get("summary_issue_url")
    summary_issue_num: Optional[int] = None
    if summary_issue_url:
        m_sn = re.search(r"/issues/(\d+)", summary_issue_url)
        if m_sn:
            summary_issue_num = int(m_sn.group(1))

    return {
        "sprint_label": sprint_label,
        "state": pane_state,
        "lifecycle": _derive_outcome_lifecycle(
            sprint_label, project_root, project, plan_state, pane_state, failed_count,
        ),
        "end_reason": (plan.get("end_reason") if plan else None) or sprint_json.get("end_reason"),
        "sprint_status": sprint_status,
        "counts": {
            "done": done_count,
            "failed": failed_count,
            "skipped": skipped_count,
        },
        "wall_clock_secs": state_data.get("wall_clock_secs", 0.0),
        "ended_at": _fmt_iso(ended_ts),
        "issues": result_issues,
        "log_line_count": log_line_count,
        "summary_issue_url": summary_issue_url,
        "summary_issue_num": summary_issue_num,
    }


def get_estimate_vs_actual(sprint_label: str, project: str) -> dict:
    """Return per-ticket estimate-vs-actual comparison for a finished sprint."""
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    project_root = _project_root_path(project)
    commander = _commander_dir(project_root)

    if _is_sprint_running(project_root, sprint_label):
        raise HTTPException(404, detail=f"Sprint {sprint_label!r} is still in progress")

    plan = _read_plan_json(project_root, sprint_label)
    plan_state = (plan or {}).get("state", "")
    if plan_state in ("planning", "draft", "planned", "running"):
        raise HTTPException(404, detail=f"Sprint {sprint_label!r} is not finished")
    if plan_state == "cancelled":
        raise HTTPException(404, detail=f"Sprint {sprint_label!r} was cancelled")

    m = re.search(r"(\d+)", sprint_label)
    n = m.group(1) if m else sprint_label
    state_path = commander / "sprints" / f"sprint-{n}-state.json"

    if not state_path.exists():
        raise HTTPException(404, detail=f"Sprint {sprint_label!r} not found")

    try:
        state_data = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise HTTPException(500, detail=f"Could not read state file: {e}")

    issues_raw = state_data.get("issues", [])
    if plan_state != "completed" and issues_raw:
        has_pending = any(i.get("status") == "pending" for i in issues_raw)
        if has_pending:
            raise HTTPException(404, detail=f"Sprint {sprint_label!r} is not finished")
    elif plan_state != "completed" and not issues_raw:
        raise HTTPException(404, detail=f"Sprint {sprint_label!r} not found")

    def _parse_ts(s: Optional[str]) -> Optional[float]:
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s.rstrip("Z"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except Exception:
            return None

    estimates_dir = commander / "estimates"
    tickets = []
    for iss in issues_raw:
        issue_num = iss.get("number")
        title = iss.get("title", "")
        status = iss.get("status", "pending")
        estimated_size: Optional[str] = None
        estimated_minutes: Optional[int] = None
        if issue_num is not None:
            est_path = estimates_dir / f"issue-{issue_num}.json"
            if est_path.exists():
                try:
                    est_data = json.loads(est_path.read_text(encoding="utf-8"))
                    estimated_size = est_data.get("size") or None
                    if estimated_size:
                        estimated_minutes = _size_to_minutes(estimated_size) or None
                except (json.JSONDecodeError, OSError):
                    pass
        start_ts = _parse_ts(iss.get("coder_started_at"))
        end_ts = (
            _parse_ts(iss.get("tester_finished_at"))
            or _parse_ts(iss.get("status_changed_at"))
        )
        actual_elapsed_seconds: Optional[float] = None
        actual_elapsed_minutes: Optional[float] = None
        if start_ts is not None and end_ts is not None:
            actual_elapsed_seconds = max(0.0, end_ts - start_ts)
            actual_elapsed_minutes = round(actual_elapsed_seconds / 60, 1)
        delta_minutes: Optional[float] = None
        if estimated_minutes is not None and actual_elapsed_minutes is not None:
            delta_minutes = round(actual_elapsed_minutes - estimated_minutes, 1)
        tickets.append({
            "ticket_id": issue_num,
            "title": title,
            "estimated_size": estimated_size,
            "estimated_minutes": estimated_minutes,
            "actual_elapsed_seconds": round(actual_elapsed_seconds) if actual_elapsed_seconds is not None else None,
            "actual_elapsed_minutes": actual_elapsed_minutes,
            "delta_minutes": delta_minutes,
            "status": status,
        })

    return {"sprint_label": sprint_label, "tickets": tickets}


def get_calibration(project: str) -> dict:
    """Return average actual time per size bucket (legacy calibration endpoint)."""
    try:
        repo = _resolve_project_slug(project)
    except HTTPException:
        repo = project
    data = compute_calibration(repo)
    canonical = {"S": 5, "M": 15, "L": 30, "XL": 60}
    result_buckets: dict[str, dict] = {}
    for size in _CALIBRATION_SIZES:
        row = data["by_size"][size]
        result_buckets[size] = {
            "avg_minutes": row["avg_minutes"],
            "count": row["count"],
            "canonical_minutes": row.get("configured_minutes") or canonical[size],
        }
    return {"buckets": result_buckets}


def get_sprint_metrics(from_date, to_date) -> list:
    """Return per-sprint aggregate metrics across all registered projects."""
    from_dt = datetime(from_date.year, from_date.month, from_date.day, tzinfo=timezone.utc)
    to_dt = datetime(to_date.year, to_date.month, to_date.day, 23, 59, 59, tzinfo=timezone.utc)

    try:
        all_projects = _projects_module.load_projects()
    except Exception:
        all_projects = []

    results = []
    seen_paths: set = set()

    for proj in all_projects:
        repo = proj.get("repo", "")
        if not repo:
            continue
        project_root = _project_root_path(repo)
        sprints_dir = _commander_dir(project_root) / "sprints"
        if not sprints_dir.exists():
            continue
        for state_file in sorted(sprints_dir.glob("sprint-*-state.json")):
            if state_file in seen_paths:
                continue
            seen_paths.add(state_file)
            try:
                state_data = json.loads(state_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            start_ts_str = state_data.get("start_timestamp")
            if not start_ts_str:
                continue
            try:
                start_dt = datetime.fromisoformat(start_ts_str.rstrip("Z")).replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if not (from_dt <= start_dt <= to_dt):
                continue
            sprint_label_val = state_data.get("sprint_label", state_file.stem.replace("-state", ""))
            project_val = state_data.get("project") or repo
            wall_clock_secs = float(state_data.get("wall_clock_secs") or 0.0)
            issues = state_data.get("issues", [])
            done_count = sum(1 for i in issues if i.get("status") == "done")
            failed_count = sum(1 for i in issues if i.get("status") == "failed")
            skipped_count = sum(1 for i in issues if i.get("status") == "skipped")
            coder_count = sum(1 for i in issues if i.get("coder_started_at"))
            tester_count = sum(1 for i in issues if i.get("tester_started_at"))
            reviewer_count = 1 if state_data.get("reviewer_status") not in (None, "") else 0
            documenter_count = 1 if state_data.get("documenter_status") not in (None, "") else 0
            tokens_in = int(state_data.get("total_tokens_in") or 0)
            tokens_out = int(state_data.get("total_tokens_out") or 0)
            total_tokens = tokens_in + tokens_out
            token_estimate = total_tokens if total_tokens > 0 else None
            results.append({
                "sprint_label": sprint_label_val,
                "project": project_val,
                "duration_minutes": round(wall_clock_secs / 60, 2),
                "ticket_count": len(issues),
                "ticket_outcomes_breakdown": {
                    "done": done_count,
                    "failed": failed_count,
                    "skipped": skipped_count,
                    "needs_rework": 0,
                },
                "agent_dispatch_counts": {
                    "coder": coder_count,
                    "tester": tester_count,
                    "reviewer": reviewer_count,
                    "documenter": documenter_count,
                },
                "total_token_estimate": token_estimate,
            })

    return results
