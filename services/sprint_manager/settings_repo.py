from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import text

from services.sprint_manager.neon_db import get_session as _neon_get_session

# Tests can replace this with a sessionmaker bound to a test engine.
_session_factory = None


def _open_session():
    if _session_factory is not None:
        return _session_factory()
    return _neon_get_session()


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
    with _open_session() as session:
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
    with _open_session() as session:
        row = session.execute(
            text(
                "SELECT value FROM settings"
                " WHERE scope = :scope AND key = :key"
                " AND (project = :project OR (project IS NULL AND :project IS NULL))"
            ),
            {"scope": scope, "key": key, "project": project},
        ).fetchone()
        return _load(row[0]) if row else {}


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

    with _open_session() as session:
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
