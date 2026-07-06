from __future__ import annotations

import json
import os
import sqlite3
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


# ── Durable sqlite backend (issue #8) ────────────────────────────────────────
# When Neon is disabled, prefer the local sqlite DB (DB_PATH, e.g. commander.db)
# over the per-clone settings_store.json so estimation/pipeline/etc. settings
# survive deploys instead of resetting to schema defaults. The JSON file stays
# as a last-resort fallback (and as the seed for a one-time back-fill).
_kv_init_done: set[str] = set()


def _sqlite_db_path() -> Optional[str]:
    p = (os.environ.get("DB_PATH") or "").strip()
    return p or None


def _kv_conn() -> Optional[sqlite3.Connection]:
    """Open the settings sqlite DB (ensuring table + one-time back-fill), or None."""
    path = _sqlite_db_path()
    if not path:
        return None
    try:
        conn = sqlite3.connect(path, timeout=5)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS settings_kv ("
            " scope TEXT NOT NULL, project TEXT NOT NULL DEFAULT '',"
            " key TEXT NOT NULL, value TEXT NOT NULL, updated_at TEXT,"
            " PRIMARY KEY (scope, project, key))"
        )
        if path not in _kv_init_done:
            _kv_backfill_from_json(conn)
            _kv_init_done.add(path)
        return conn
    except Exception:
        return None


def _kv_backfill_from_json(conn: sqlite3.Connection) -> None:
    """Seed the sqlite table from settings_store.json once, so values customized
    before this change (e.g. estimation minutes) carry over."""
    try:
        if conn.execute("SELECT COUNT(*) FROM settings_kv").fetchone()[0]:
            return  # already populated — don't clobber
        p = _fallback_store_path()
        store = json.loads(p.read_text()) if p.exists() else {}
    except Exception:
        return
    if not store:
        return
    now = datetime.now(timezone.utc).isoformat()
    for fkey, value in store.items():
        parts = fkey.split("|", 2)
        if len(parts) != 3:
            continue
        scope, project, key = parts
        try:
            conn.execute(
                "INSERT OR REPLACE INTO settings_kv (scope, project, key, value, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (scope, project, key, json.dumps(value), now),
            )
        except Exception:
            continue
    try:
        conn.commit()
    except Exception:
        pass


def _fallback_read_all() -> dict:
    conn = _kv_conn()
    if conn is not None:
        try:
            rows = conn.execute(
                "SELECT scope, project, key, value FROM settings_kv"
            ).fetchall()
            out: dict = {}
            for scope, project, key, value in rows:
                try:
                    out[_fkey(scope, project or None, key)] = json.loads(value)
                except Exception:
                    continue
            return out
        except Exception:
            pass
        finally:
            conn.close()
    # Last resort: per-clone JSON file.
    p = _fallback_store_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _fallback_write_all(data: dict) -> None:
    conn = _kv_conn()
    if conn is not None:
        try:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute("DELETE FROM settings_kv")
            for fkey, value in data.items():
                parts = fkey.split("|", 2)
                if len(parts) != 3:
                    continue
                scope, project, key = parts
                conn.execute(
                    "INSERT INTO settings_kv (scope, project, key, value, updated_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (scope, project, key, json.dumps(value), now),
                )
            conn.commit()
            return
        except Exception:
            pass
        finally:
            conn.close()
    try:
        _fallback_store_path().write_text(json.dumps(data, indent=2))
    except Exception:
        pass


def _load(raw: Any) -> Any:
    """Deserialize value — handles both str (SQLite) and already-decoded dict (Postgres JSONB)."""
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


def _ensure_session_schema(session) -> None:
    """Create the settings table if it does not yet exist in the session's DB.

    Used when a new in-memory engine (e.g., in tests) is wired via
    _session_factory but its schema was never initialised.  We intentionally
    omit the non-constant DEFAULT on updated_at so the CREATE succeeds on
    SQLite ≥ 3.37 which rejects datetime('now') as a column default.
    """
    try:
        session.execute(text(
            "CREATE TABLE IF NOT EXISTS settings ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " scope TEXT NOT NULL,"
            " project TEXT,"
            " key TEXT NOT NULL,"
            " value TEXT NOT NULL,"
            " updated_at TEXT,"
            " UNIQUE(scope, project, key)"
            ")"
        ))
        session.commit()
    except Exception:
        pass


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
    try:
        with session_cm as session:
            _ensure_session_schema(session)
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
    except Exception:
        store = _fallback_read_all()
        global_val = store.get(_fkey("global", None, key), {})
        if project is None:
            return global_val
        proj_val = store.get(_fkey("project", project, key))
        if proj_val is None:
            return global_val
        return {**global_val, **proj_val}


def get_setting_scoped(scope: str, key: str, project: Optional[str] = None) -> Any:
    """Return the raw stored value for a specific scope/project without merging.

    Returns {} if no matching row exists.
    """
    session_cm = _try_open_session()
    if session_cm is None:
        return _fallback_read_all().get(_fkey(scope, project, key), {})
    try:
        with session_cm as session:
            _ensure_session_schema(session)
            row = session.execute(
                text(
                    "SELECT value FROM settings"
                    " WHERE scope = :scope AND key = :key"
                    " AND (project = :project OR (project IS NULL AND :project IS NULL))"
                ),
                {"scope": scope, "key": key, "project": project},
            ).fetchone()
            return _load(row[0]) if row else {}
    except Exception:
        return _fallback_read_all().get(_fkey(scope, project, key), {})


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
    try:
        with session_cm as session:
            _ensure_session_schema(session)
            session.execute(
                text(
                    "DELETE FROM settings"
                    " WHERE scope = :scope AND key = :key"
                    " AND (project = :project OR (project IS NULL AND :project IS NULL))"
                ),
                {"scope": scope, "key": key, "project": project},
            )
            session.commit()
    except Exception:
        with _fallback_lock:
            store = _fallback_read_all()
            if store.pop(_fkey(scope, project, key), None) is not None:
                _fallback_write_all(store)


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
    try:
        with session_cm as session:
            _ensure_session_schema(session)
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
    except Exception:
        with _fallback_lock:
            store = _fallback_read_all()
            store[_fkey(scope, project, key)] = value
            _fallback_write_all(store)
