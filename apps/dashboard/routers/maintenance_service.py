"""Calibration cache rebuild service (issue #1332).

Provides rebuild_calibration_cache() to clear and rescan all sprint state
files, and do_rebuild() as the endpoint-callable entry point that resolves
the project slug and settings before delegating.
"""
from __future__ import annotations

import sys
from pathlib import Path

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
_SERVICES_ROOT = _DASHBOARD_ROOT.parent.parent / "services" / "sprint_manager"
for _p in (str(_DASHBOARD_ROOT), str(_SERVICES_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import os  # noqa: E402

import projects as _projects_module  # noqa: E402
import settings_repo as _settings_repo  # noqa: E402

from fastapi import HTTPException  # noqa: E402
from services.sprint_manager.settings_schema import (  # noqa: E402
    APP_CONFIG_KEY,
    build_effective_response,
)
import calibration_cache_service as _ccs  # noqa: E402

_SIZES = ("S", "M", "L", "XL")
_PROJECTS_BASE = Path.home() / "dev"
_CALIBRATION_SIZE_SETTING_KEYS = {
    "S": "estimation_s_minutes",
    "M": "estimation_m_minutes",
    "L": "estimation_l_minutes",
    "XL": "estimation_xl_minutes",
}


def _resolve_project_slug(slug: str) -> str:
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
        raise HTTPException(
            status_code=404, detail=f"Project '{slug}' not found"
        )
    return matched["repo"]


def _project_root_path(repo: str) -> Path:
    slug = repo.split("/")[-1] if "/" in repo else repo
    return _PROJECTS_BASE / slug


def _get_configured_minutes(repo: str) -> dict[str, int]:
    try:
        stored = _settings_repo.get_setting(APP_CONFIG_KEY, project=repo)
    except Exception:
        stored = {}
    effective = build_effective_response(stored)
    return {
        sz: effective[key]
        for sz, key in _CALIBRATION_SIZE_SETTING_KEYS.items()
    }


def rebuild_calibration_cache(
    project_root: Path,
    configured_minutes: dict[str, int],
    *,
    dry_run: bool = False,
) -> dict:
    """Rebuild calibration cache by rescanning all sprint state files.

    Clears processed/by_size/points before rescanning sprints/ and
    sprints/archive/ using the new size resolver (_resolve_calibration_size).
    When dry_run=True, computes counts without writing to disk.

    Returns {"total": N, "by_size": {"S": x, "M": y, "L": z, "XL": w}}.
    """
    commander = project_root / ".commander"
    sprints_dir = commander / "sprints"
    estimates_dir = commander / "estimates"

    db_path = None
    env_db = os.environ.get("DB_PATH")
    if env_db:
        db_path = Path(env_db)

    # Start completely fresh — no stale keys survive.
    cache = _ccs._calibration_empty_cache()
    processed: set[str] = set()

    if sprints_dir.is_dir():
        archive_dir = sprints_dir / "archive"
        if archive_dir.is_dir():
            for state_file in sorted(archive_dir.glob("sprint-*-state.json")):
                _ccs._calibration_absorb_state_file(
                    cache, state_file, sprints_dir, estimates_dir,
                    configured_minutes, processed,
                    db_path=db_path,
                )
        for state_file in sorted(sprints_dir.glob("sprint-*-state.json")):
            _ccs._calibration_absorb_state_file(
                cache, state_file, sprints_dir, estimates_dir,
                configured_minutes, processed,
                db_path=db_path,
            )

    cache["archive_bootstrap_done"] = True

    if not dry_run:
        _ccs._save_calibration_cache(commander, cache)

    return _count_summary(cache)


def _count_summary(cache: dict) -> dict:
    by_size = cache.get("by_size") or {}
    return {
        "total": sum(
            int(by_size.get(sz, {}).get("count", 0) or 0) for sz in _SIZES
        ),
        "by_size": {
            sz: int(by_size.get(sz, {}).get("count", 0) or 0) for sz in _SIZES
        },
    }


def do_rebuild(project_slug: str, dry_run: bool = False) -> dict:
    """Resolve project slug and run calibration rebuild.

    Raises HTTPException(404) for unknown slugs.
    """
    repo = _resolve_project_slug(project_slug)
    project_root = _project_root_path(repo)
    configured_minutes = _get_configured_minutes(repo)
    return rebuild_calibration_cache(
        project_root, configured_minutes, dry_run=dry_run
    )
