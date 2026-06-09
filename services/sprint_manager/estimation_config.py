"""Resolve estimation configuration from the settings KV store.

Provides get_estimation_cfg(project=None) which fetches the 'estimation' key
from the settings table (global merged with optional project override) and falls
back to DEFAULT_ESTIMATION_CFG when the settings layer is unavailable.

The default values intentionally match the former hard-coded constants so that
the no-override path produces identical output to pre-#640 behaviour.
"""
from __future__ import annotations

from typing import Any, Optional

DEFAULT_ESTIMATION_CFG: dict[str, Any] = {
    "size_minutes": {"S": 5, "M": 15, "L": 30, "XL": 60},
    "buffer_pct": 20,
    "thin_ac_buffer_pct": 30,
}


def _get_setting_safe(key: str, project: Optional[str] = None) -> dict:
    """Thin wrapper around settings_repo.get_setting with error suppression."""
    try:
        from services.sprint_manager.settings_repo import get_setting  # noqa: PLC0415
        return get_setting(key, project=project) or {}
    except Exception:
        return {}


def get_estimation_cfg(project: Optional[str] = None) -> dict[str, Any]:
    """Return the effective estimation config for a project.

    Resolution order:
      1. Fetch global 'estimation' row from settings table.
      2. If *project* given, fetch project row and shallow-merge on top.
      3. Merge result on top of DEFAULT_ESTIMATION_CFG so any missing keys
         fall back to defaults.
      4. On any error (DB unavailable, table missing), return defaults.
    """
    stored = _get_setting_safe("estimation", project=project)
    if not stored:
        return dict(DEFAULT_ESTIMATION_CFG)

    result = dict(DEFAULT_ESTIMATION_CFG)
    result.update(stored)

    # Ensure size_minutes sub-dict is fully populated (partial override is OK)
    base_sizes = dict(DEFAULT_ESTIMATION_CFG["size_minutes"])
    if isinstance(stored.get("size_minutes"), dict):
        base_sizes.update(stored["size_minutes"])
    result["size_minutes"] = base_sizes

    return result
