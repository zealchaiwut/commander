"""Estimator calibration endpoint — average actual time per size bucket.

Routes in this module:
  GET /api/calibration
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

# ── Path setup ───────────────────────────────────────────────────────────────
_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent  # apps/dashboard/
_REPO_ROOT = _DASHBOARD_ROOT.parent.parent  # repo root
_SERVICES_ROOT = _REPO_ROOT / "services" / "sprint_manager"
for _p in (str(_DASHBOARD_ROOT), str(_SERVICES_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import projects as _projects_module  # noqa: E402
import services.sprint_manager.settings_repo as _settings_repo  # noqa: E402
from services.sprint_manager.settings_schema import (  # noqa: E402
    APP_CONFIG_KEY,
    build_effective_response,
)

_PROJECTS_BASE = Path.home() / "dev"

router = APIRouter()


# ── Shared helpers (mirrored from server.py) ─────────────────────────────────

def _resolve_project_slug(slug: str) -> str:
    """Resolve slug to owner/repo string; raise 404 if not found."""
    try:
        all_projects = _projects_module.load_projects()
    except Exception:
        all_projects = []
    matched = next(
        (p for p in all_projects
         if p["repo"].split("/")[-1] == slug or p["repo"] == slug),
        None,
    )
    if matched is None:
        raise HTTPException(status_code=404, detail=f"Project {slug!r} not found")
    return matched["repo"]


def _project_root_path(repo: str) -> Path:
    """Return the project root directory for a given repo (owner/repo)."""
    slug = repo.split("/")[-1] if "/" in repo else repo
    return _PROJECTS_BASE / slug


def _commander_dir(project_root: Path) -> Path:
    return project_root / ".commander"


# ── Calibration constants ─────────────────────────────────────────────────────

_CALIBRATION_SIZES = ("S", "M", "L", "XL")
_CALIBRATION_SIZE_SETTING_KEYS = {
    "S": "estimation_s_minutes",
    "M": "estimation_m_minutes",
    "L": "estimation_l_minutes",
    "XL": "estimation_xl_minutes",
}
_CALIBRATION_CACHE_VERSION = 1
_CALIBRATION_DONE_STATUSES = frozenset({"done", "uat", "merged", "passed"})


# ── Calibration helper functions ──────────────────────────────────────────────

def _calibration_cache_path(commander_dir: Path) -> Path:
    return commander_dir / "calibration_cache.json"


def _calibration_empty_by_size() -> dict[str, dict]:
    return {
        sz: {
            "count": 0,
            "min_minutes": None,
            "avg_minutes": None,
            "max_minutes": None,
        }
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


def _calibration_add_sample(
    cache: dict,
    size: str,
    actual_minutes: float,
    point: Optional[dict] = None,
) -> None:
    """Incrementally update per-size count/min/avg/max (no full rescan)."""
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


def _analytics_parse_ts(ts: Optional[str]) -> Optional[datetime]:
    """Parse an ISO 8601 UTC timestamp (optionally Z-suffixed) or return None."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.rstrip("Z")).replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _analytics_elapsed_minutes(start: Optional[str], end: Optional[str]) -> Optional[float]:
    """Minutes between two ISO timestamps, or None when either is missing/bad."""
    s = _analytics_parse_ts(start)
    e = _analytics_parse_ts(end)
    if s is None or e is None:
        return None
    return (e - s).total_seconds() / 60.0


def _calibration_issue_sample(
    issue: dict,
    estimates_dir: Path,
    configured_minutes: dict[str, int],
) -> Optional[tuple]:
    """Return (size, actual_minutes, point_dict) for one completed ticket."""
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

    coder_min = _analytics_elapsed_minutes(
        issue.get("coder_started_at"), issue.get("coder_finished_at"))
    tester_min = _analytics_elapsed_minutes(
        issue.get("tester_started_at"), issue.get("tester_finished_at"))
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
    """Merge new tickets from one state file into cache; return True if anything added."""
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


def _refresh_calibration_cache(
    project_root: Path,
    configured_minutes: dict[str, int],
) -> dict:
    """Merge sprint state files into calibration_cache.json (durable local store)."""
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
                cache, state_file, sprints_dir, estimates_dir,
                configured_minutes, processed,
            ):
                changed = True

    for state_file in sorted(sprints_dir.glob("sprint-*-state.json")):
        if _calibration_absorb_state_file(
            cache, state_file, sprints_dir, estimates_dir,
            configured_minutes, processed,
        ):
            changed = True

    if not cache.get("archive_bootstrap_done"):
        cache["archive_bootstrap_done"] = True
        changed = True

    if changed:
        _save_calibration_cache(commander, cache)
    return cache


def _iter_calibration_state_files(
    sprints_dir: Path,
    *,
    include_archive: bool = False,
) -> list:
    """State files for a full scan (optionally including archive/)."""
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


def _parse_iso_date(date_str: str, name: str, *, end_of_day: bool = False) -> datetime:
    """Parse a YYYY-MM-DD string into a UTC datetime, raising HTTP 400 on bad input."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(400, detail=f"Invalid {name!r} date {date_str!r} — expected YYYY-MM-DD")
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    return dt


def _compute_calibration_from_files(
    project_root: Path,
    configured_minutes: dict[str, int],
    since_dt: Optional[datetime],
    until_dt: Optional[datetime],
    sprint_filter: Optional[str],
    *,
    include_archive: bool,
) -> dict:
    """Full scan for scoped queries (since/until/sprint filters)."""
    sprints_dir = _commander_dir(project_root) / "sprints"
    estimates_dir = _commander_dir(project_root) / "estimates"

    by_size = {
        sz: {
            "configured_minutes": configured_minutes[sz],
            **(_calibration_empty_by_size()[sz]),
        }
        for sz in _CALIBRATION_SIZES
    }
    buckets: dict = {sz: [] for sz in _CALIBRATION_SIZES}
    points: list = []

    for state_file in _iter_calibration_state_files(
        sprints_dir, include_archive=include_archive,
    ):
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


def _compute_calibration(
    repo: str,
    since: Optional[str] = None,
    until: Optional[str] = None,
    sprint_filter: Optional[str] = None,
) -> dict:
    """Aggregate estimated vs actual time per size tier for the given project."""
    since_dt = _parse_iso_date(since, "since") if since else None
    until_dt = _parse_iso_date(until, "until", end_of_day=True) if until else None

    try:
        stored = _settings_repo.get_setting(APP_CONFIG_KEY, project=repo)
    except Exception:
        stored = {}
    effective = build_effective_response(stored)
    configured_minutes = {sz: effective[key] for sz, key in _CALIBRATION_SIZE_SETTING_KEYS.items()}

    project_root = _project_root_path(repo)

    if since or until or sprint_filter:
        return _compute_calibration_from_files(
            project_root,
            configured_minutes,
            since_dt,
            until_dt,
            sprint_filter,
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


# ── Route ─────────────────────────────────────────────────────────────────────

@router.get("/api/calibration")
def get_calibration(project: str):
    """Return average actual time per size bucket (legacy calibration tab).

    Delegates to the durable ``calibration_cache.json`` path used by Analytics
    so archived sprint state files remain in the dataset.
    """
    try:
        repo = _resolve_project_slug(project)
    except HTTPException:
        repo = project
    data = _compute_calibration(repo)
    canonical = {"S": 5, "M": 15, "L": 30, "XL": 60}
    result_buckets: dict = {}
    for size in _CALIBRATION_SIZES:
        row = data["by_size"][size]
        result_buckets[size] = {
            "avg_minutes": row["avg_minutes"],
            "count": row["count"],
            "canonical_minutes": row.get("configured_minutes") or canonical[size],
        }
    return {"buckets": result_buckets}


@router.get("/api/projects/{slug}/analytics/calibration")
def get_project_analytics_calibration(slug: str, request: Request):
    """GET /api/projects/{slug}/analytics/calibration — estimated vs actual per size tier (issue #1267)."""
    repo = _resolve_project_slug(slug)
    since = request.query_params.get("since")
    until = request.query_params.get("until")
    sprint_filter = request.query_params.get("sprint")
    return _compute_calibration(repo, since=since, until=until, sprint_filter=sprint_filter)
