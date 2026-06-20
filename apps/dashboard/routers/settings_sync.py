from __future__ import annotations
import sys
from pathlib import Path
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _DASHBOARD_ROOT.parent.parent
_SERVICES_ROOT = _REPO_ROOT / "services" / "sprint_manager"
for _p in (str(_DASHBOARD_ROOT), str(_SERVICES_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


_PROJECTS_BASE = Path.home() / "dev"

router = APIRouter()

def _server():
    import server
    return server


# ── Settings sync imports ─────────────────────────────────────────────────────

try:
    from services.sprint_manager.settings_sync import (
        load_local_snapshot as _ss_load_local,
        load_neon_snapshot as _ss_load_neon,
        compute_diff as _ss_compute_diff,
        is_already_in_sync as _ss_already_in_sync,
        apply_upload as _ss_apply_upload,
        apply_fetch as _ss_apply_fetch,
        get_sync_status as _ss_get_status,
        save_sync_status as _ss_save_status,
    )
    _SYNC_SETTINGS_AVAILABLE = True
except Exception:
    _SYNC_SETTINGS_AVAILABLE = False


def _get_sync_status_file() -> Path:
    return _server()._SYNC_STATUS_FILE


def _get_projects_file() -> Path:
    return _server()._PROJECTS_FILE


def _get_sprint_yaml_path():
    return _server()._SPRINT_YAML_PATH


def _get_settings_repo():
    return _server()._settings_repo


# ── Routes ────────────────────────────────────────────────────────────────────


@router.get("/api/settings/sync/status")
def get_settings_sync_status():
    """Return last-synced timestamp for settings sync.

    Returns {"last_synced": str|null} where str is ISO 8601 UTC.
    """
    ts = _ss_get_status(_get_sync_status_file()) if _SYNC_SETTINGS_AVAILABLE else None
    return {"last_synced": ts}


class _SyncDiffBody(BaseModel):
    direction: str  # "upload" or "fetch"


@router.post("/api/settings/sync/diff")
def post_settings_sync_diff(body: _SyncDiffBody):
    """Compute a settings diff without applying any writes.

    direction="upload" — compares local files → Neon
    direction="fetch"  — compares Neon → local files

    Returns {"diff": [...], "already_in_sync": bool}.
    Each diff item has: status ("added"|"removed"|"unchanged"), key, value.
    """
    if body.direction not in ("upload", "fetch"):
        raise HTTPException(status_code=400, detail="direction must be 'upload' or 'fetch'")
    if not _SYNC_SETTINGS_AVAILABLE:
        raise HTTPException(status_code=503, detail="settings sync module unavailable")

    try:
        local_snap = _ss_load_local(_get_projects_file(), sprint_yaml_path=_get_sprint_yaml_path())
        neon_snap = _ss_load_neon(_get_settings_repo())
        diff = _ss_compute_diff(local_snap, neon_snap, body.direction)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return {
        "diff": diff,
        "already_in_sync": _ss_already_in_sync(diff),
    }


class _SyncCommitBody(BaseModel):
    direction: str  # "upload" or "fetch"


@router.post("/api/settings/sync/commit")
def post_settings_sync_commit(body: _SyncCommitBody):
    """Apply a settings sync after user confirms the diff preview.

    direction="upload" — writes changed values to Neon; local files unchanged
    direction="fetch"  — writes changed values to local files; Neon unchanged

    Returns {"ok": true, "synced_at": str (ISO 8601 UTC)}.
    """
    if body.direction not in ("upload", "fetch"):
        raise HTTPException(status_code=400, detail="direction must be 'upload' or 'fetch'")
    if not _SYNC_SETTINGS_AVAILABLE:
        raise HTTPException(status_code=503, detail="settings sync module unavailable")

    try:
        local_snap = _ss_load_local(_get_projects_file(), sprint_yaml_path=_get_sprint_yaml_path())
        neon_snap = _ss_load_neon(_get_settings_repo())
        diff = _ss_compute_diff(local_snap, neon_snap, body.direction)

        if body.direction == "upload":
            _ss_apply_upload(diff, _get_settings_repo())
        else:
            _ss_apply_fetch(diff, _get_projects_file(), _get_sprint_yaml_path())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    synced_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _ss_save_status(_get_sync_status_file(), synced_at)

    return {"ok": True, "synced_at": synced_at}
