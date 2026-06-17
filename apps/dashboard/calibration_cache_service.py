"""Calibration cache service — durable local store for sprint calibration samples.

Extracted from server.py so sprint_manager can call _refresh_calibration_cache
on sprint finish without importing the FastAPI monolith (issue #1333).

Self-contained: only stdlib + pathlib. No FastAPI, no Neon, no external deps.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_CALIBRATION_SIZES = ("S", "M", "L", "XL")
_CALIBRATION_CACHE_VERSION = 1
_CALIBRATION_DONE_STATUSES = frozenset({"done", "uat", "merged", "passed"})
_CALIBRATION_SIZE_SETTING_KEYS = {
    "S": "estimation_s_minutes",
    "M": "estimation_m_minutes",
    "L": "estimation_l_minutes",
    "XL": "estimation_xl_minutes",
}


def _analytics_parse_ts(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.rstrip("Z")).replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _analytics_elapsed_minutes(
    start: Optional[str], end: Optional[str]
) -> Optional[float]:
    s = _analytics_parse_ts(start)
    e = _analytics_parse_ts(end)
    if s is None or e is None:
        return None
    return (e - s).total_seconds() / 60.0


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


def _resolve_calibration_size(
    issue_num: Optional[int],
    estimates_dir: Path,
    state_estimates: dict,
    issue_labels: list[dict],
) -> Optional[str]:
    """Resolve size with priority: JSON estimate file → state.estimates → size-* label."""
    if issue_num is not None and estimates_dir.is_dir():
        est_file = estimates_dir / f"issue-{issue_num}.json"
        if est_file.is_file():
            try:
                sz = json.loads(est_file.read_text(encoding="utf-8")).get("size")
                if sz in _CALIBRATION_SIZES:
                    return sz
            except (json.JSONDecodeError, OSError):
                pass

    if issue_num is not None:
        entry = state_estimates.get(issue_num) or state_estimates.get(str(issue_num))
        if isinstance(entry, dict):
            sz = entry.get("size")
            if sz in _CALIBRATION_SIZES:
                return sz

    for lbl in issue_labels:
        name = lbl.get("name", "")
        if name.startswith("size-"):
            sz = name[5:].upper()
            if sz in _CALIBRATION_SIZES:
                return sz

    return None


def _calibration_issue_sample(
    issue: dict,
    estimates_dir: Path,
    configured_minutes: dict[str, int],
    state_estimates: Optional[dict] = None,
) -> Optional[tuple[str, float, dict]]:
    """Return (size, actual_minutes, point_dict) for one completed ticket, or None."""
    if issue.get("status") not in _CALIBRATION_DONE_STATUSES:
        return None
    issue_num = issue.get("number")
    size = _resolve_calibration_size(
        issue_num,
        estimates_dir,
        state_estimates or {},
        issue.get("labels") or [],
    )
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
    processed: set[str],
) -> bool:
    """Merge new tickets from one state file into cache; return True if anything added."""
    try:
        state_data = json.loads(state_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False

    state_estimates = state_data.get("estimates") or {}
    changed = False
    for issue in state_data.get("issues", []):
        issue_num = issue.get("number")
        if issue_num is None:
            continue
        key = _calibration_state_key(state_file, sprints_dir, issue_num)
        if key in processed:
            continue
        sample = _calibration_issue_sample(issue, estimates_dir, configured_minutes,
                                           state_estimates=state_estimates)
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
    """Merge sprint state files into calibration_cache.json (durable local store).

    Live sprint-*-state.json files and archive/ copies are scanned every refresh;
    the processed list prevents double-counting. Returns (cache, new_sample_count).
    """
    commander = project_root / ".commander"
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
