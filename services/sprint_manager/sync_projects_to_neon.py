"""sync_projects_to_neon.py — one-way sync from projects.json to Neon.

Called at dashboard startup and by POST /api/projects/sync-to-db.
projects.json remains the runtime source of truth; this module only writes
to the DB as a backup.

projects.json schema (relevant fields):
  [
    {
      "repo": "owner/name",        # unique identifier
      "name": "Display Name",
      "environments": {            # optional; each key is an env name (prd, uat, …)
        "prd": "/abs/path/to/clone"
      }
    },
    ...
  ]
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import sqlalchemy as sa

logger = logging.getLogger(__name__)

_PROJECTS_JSON = (
    Path(__file__).parent.parent.parent / "apps" / "dashboard" / "projects.json"
)


def _load_projects(projects_file: Path) -> list[dict]:
    if not projects_file.exists():
        return []
    try:
        return json.loads(projects_file.read_text())
    except Exception as exc:
        logger.warning("sync_projects: could not read %s: %s", projects_file, exc)
        return []


def sync_projects_to_neon(
    projects_file: Path | None = None,
) -> dict[str, Any]:
    """Sync projects.json → Neon.  Returns a summary dict suitable for JSON responses.

    Non-blocking: if the Neon connection is unavailable, logs a warning and
    returns a summary with the error captured in ``errors``.
    """
    from neon_db import get_engine  # local import avoids import-time DB connections

    if projects_file is None:
        projects_file = _PROJECTS_JSON

    projects = _load_projects(projects_file)

    projects_synced = 0
    projects_skipped = 0
    envs_synced = 0
    envs_skipped = 0
    errors: list[str] = []

    try:
        engine = get_engine()
    except RuntimeError as exc:
        msg = f"cannot connect to Neon: {exc}"
        logger.warning("sync_projects: %s", msg)
        return {
            "projects_synced": 0,
            "projects_skipped": 0,
            "envs_synced": 0,
            "envs_skipped": 0,
            "errors": [msg],
        }

    try:
        with engine.begin() as conn:
            for proj in projects:
                repo = (proj.get("repo") or "").strip()
                if not repo:
                    continue
                name = (proj.get("name") or repo.split("/")[-1]).strip()

                existing = conn.execute(
                    sa.text("SELECT id FROM projects WHERE repo = :repo"),
                    {"repo": repo},
                ).fetchone()

                conn.execute(
                    sa.text(
                        "INSERT INTO projects (repo, name) VALUES (:repo, :name)"
                        " ON CONFLICT (repo) DO UPDATE SET name = EXCLUDED.name"
                    ),
                    {"repo": repo, "name": name},
                )

                if existing:
                    projects_skipped += 1
                    project_id = existing[0]
                else:
                    projects_synced += 1
                    row = conn.execute(
                        sa.text("SELECT id FROM projects WHERE repo = :repo"),
                        {"repo": repo},
                    ).fetchone()
                    if not row:
                        errors.append(f"could not retrieve id for {repo}")
                        continue
                    project_id = row[0]

                for env, local_dir in (proj.get("environments") or {}).items():
                    if not env or not local_dir:
                        continue
                    env_existing = conn.execute(
                        sa.text(
                            "SELECT id FROM project_environments"
                            " WHERE project_id = :pid AND env = :env"
                        ),
                        {"pid": project_id, "env": env},
                    ).fetchone()

                    conn.execute(
                        sa.text(
                            "INSERT INTO project_environments"
                            " (project_id, env, local_directory)"
                            " VALUES (:pid, :env, :local_dir)"
                            " ON CONFLICT (project_id, env)"
                            " DO UPDATE SET local_directory = EXCLUDED.local_directory"
                        ),
                        {"pid": project_id, "env": env, "local_dir": local_dir},
                    )

                    if env_existing:
                        envs_skipped += 1
                    else:
                        envs_synced += 1

    except Exception as exc:
        msg = f"sync failed: {exc}"
        logger.warning("sync_projects: %s", msg)
        errors.append(msg)

    return {
        "projects_synced": projects_synced,
        "projects_skipped": projects_skipped,
        "envs_synced": envs_synced,
        "envs_skipped": envs_skipped,
        "errors": errors,
    }
