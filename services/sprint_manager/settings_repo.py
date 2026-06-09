from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import text

from services.sprint_manager.neon_db import get_session as _neon_get_session

# Tests can replace this with a sessionmaker bound to a test engine.
_session_factory = None


def _open_session():
    if _session_factory is not None:
        return _session_factory()
    return _neon_get_session()


def _try_open_session():
    """Return an open session, or None when no backing DB is configured.

    The dashboard runs fully off GitHub + local JSON when Neon is disabled
    (DATABASE_URL unset / COMMANDER_DISABLE_NEON=1). In that mode get_engine()
    raises, so settings reads/writes must degrade to a local JSON store instead
    of 500ing — otherwise the Settings screen can't even load defaults.
    """
    try:
        return _open_session()
    except Exception:
        return None


# ── Local JSON fallback store (used when Neon is unavailable) ─────────────────
# One file for the whole dashboard, keyed by scope|project|key — mirrors the
# single shared `settings` table Neon would otherwise provide.
_fallback_lock = threading.Lock()


def _fallback_store_path() -> Path:
    # settings_repo.py lives at <repo>/services/sprint_manager/settings_repo.py
    root = Path(__file__).resolve().parent.parent.parent
    d = root / ".commander"
    d.mkdir(parents=True, exist_ok=True)
    return d / "settings_store.json"


def _fkey(scope: str, project: Optional[str], key: str) -> str:
    return f"{scope}|{project or ''}|{key}"


def _fallback_read_all() -> dict:
    p = _fallback_store_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _fallback_write_all(data: dict) -> None:
    try:
        _fallback_store_path().write_text(json.dumps(data, indent=2))
    except Exception:
        pass


def _load(raw: Any) -> Any:
    """Deserialize value — handles both str (SQLite) and already-decoded dict (Postgres JSONB)."""
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


def get_setting(key: str, project: Optional[str] = None) -> Any:
    """Return the setting value for *key*, optionally merged with a project override.

    Resolution order:
      1. Fetch global row (scope='global', project IS NULL).
      2. If *project* given, fetch project row (scope='project', project=project).
      3. Return shallow-merge of project over global (project fields win).
    Returns {} if no global row exists.
    """
    session_cm = _try_open_session()
    if session_cm is None:
        store = _fallback_read_all()
        global_val: dict = store.get(_fkey("global", None, key), {})
        if project is None:
            return global_val
        proj_val = store.get(_fkey("project", project, key))
        if proj_val is None:
            return global_val
        return {**global_val, **proj_val}
    with session_cm as session:
        global_row = session.execute(
            text(
                "SELECT value FROM settings"
                " WHERE scope = 'global' AND project IS NULL AND key = :key"
            ),
            {"key": key},
        ).fetchone()
        global_val: dict = _load(global_row[0]) if global_row else {}

        if project is None:
            return global_val

        project_row = session.execute(
            text(
                "SELECT value FROM settings"
                " WHERE scope = 'project' AND project = :proj AND key = :key"
            ),
            {"proj": project, "key": key},
        ).fetchone()

        if project_row is None:
            return global_val

        return {**global_val, **_load(project_row[0])}


def get_setting_scoped(scope: str, key: str, project: Optional[str] = None) -> Any:
    """Return the raw stored value for a specific scope/project without merging.

    Returns {} if no matching row exists.
    """
    session_cm = _try_open_session()
    if session_cm is None:
        return _fallback_read_all().get(_fkey(scope, project, key), {})
    with session_cm as session:
        row = session.execute(
            text(
                "SELECT value FROM settings"
                " WHERE scope = :scope AND key = :key"
                " AND (project = :project OR (project IS NULL AND :project IS NULL))"
            ),
            {"scope": scope, "key": key, "project": project},
        ).fetchone()
        return _load(row[0]) if row else {}


def delete_setting(
    scope: str,
    key: str,
    project: Optional[str] = None,
) -> None:
    """Delete a settings row (no-op if it doesn't exist)."""
    session_cm = _try_open_session()
    if session_cm is None:
        with _fallback_lock:
            store = _fallback_read_all()
            if store.pop(_fkey(scope, project, key), None) is not None:
                _fallback_write_all(store)
        return
    with session_cm as session:
        session.execute(
            text(
                "DELETE FROM settings"
                " WHERE scope = :scope AND key = :key"
                " AND (project = :project OR (project IS NULL AND :project IS NULL))"
            ),
            {"scope": scope, "key": key, "project": project},
        )
        session.commit()


def set_setting(
    scope: str,
    key: str,
    value: Any,
    project: Optional[str] = None,
) -> None:
    """Upsert a settings row.

    Args:
        scope:   'global' or 'project'
        key:     Setting key, e.g. 'estimation'
        value:   Python dict to store as JSON
        project: Required when scope='project'; ignored for scope='global'
    """
    serialized = json.dumps(value)
    now = datetime.now(timezone.utc).isoformat()

    session_cm = _try_open_session()
    if session_cm is None:
        with _fallback_lock:
            store = _fallback_read_all()
            store[_fkey(scope, project, key)] = value
            _fallback_write_all(store)
        return
    with session_cm as session:
        existing = session.execute(
            text(
                "SELECT id FROM settings"
                " WHERE scope = :scope AND key = :key"
                " AND (project = :project OR (project IS NULL AND :project IS NULL))"
            ),
            {"scope": scope, "key": key, "project": project},
        ).fetchone()

        if existing:
            session.execute(
                text(
                    "UPDATE settings SET value = :val, updated_at = :now WHERE id = :id"
                ),
                {"val": serialized, "now": now, "id": existing[0]},
            )
        else:
            session.execute(
                text(
                    "INSERT INTO settings (scope, project, key, value)"
                    " VALUES (:scope, :project, :key, :val)"
                ),
                {"scope": scope, "project": project, "key": key, "val": serialized},
            )
        session.commit()
