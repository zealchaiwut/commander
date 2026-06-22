"""Sprint collision audit endpoint (issue #1461).

Routes owned by this module:
  GET /api/debug/sprint-collisions  — return the sprint-collisions.json manifest
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi import APIRouter

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _DASHBOARD_ROOT.parent.parent

for _p in (str(_REPO_ROOT), str(_DASHBOARD_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

router = APIRouter(tags=["sprint_collisions"])


def _manifest_path() -> Path:
    """Return the path to sprint-collisions.json."""
    try:
        from services.sprint_manager.commander_paths import discover_commander_dir
        return discover_commander_dir() / "runtime" / "sprint-collisions.json"
    except Exception:
        return _REPO_ROOT / ".commander" / "runtime" / "sprint-collisions.json"


@router.get("/api/debug/sprint-collisions")
def get_sprint_collisions():
    """Return the sprint-collisions.json manifest written by audit_sprint_collisions.py.

    Returns an empty list when the manifest file has not been generated yet.
    """
    path = _manifest_path()
    if not path.is_file():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
