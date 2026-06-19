"""Service logic for the projects router (extracted from server.py, issue #1249).

Delegates to ``projects`` and ``db`` modules via the same module objects
server.py uses, so test patches against ``srv.projects_module`` continue to
apply here. Backup triggering and gh-error formatting are reached via a
deferred server import to avoid the circular-import that would arise if
server.py were imported at module level while it is still mounting routers.
"""
from __future__ import annotations

import shutil
import subprocess
import sys as _sys
from pathlib import Path
from typing import Optional

from fastapi import HTTPException

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
if str(_DASHBOARD_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_DASHBOARD_ROOT))

import db  # noqa: E402
import projects as _projects_module  # noqa: E402


def _server():
    """Deferred import of the monolith — safe at request time."""
    import server  # noqa: PLC0415
    return server


def list_projects() -> dict:
    try:
        agents = db.get_agents()
        return _projects_module.get_all_projects(agents)
    except subprocess.CalledProcessError as e:
        raise _server()._gh_error(e)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))


def create_project(repo_url: str, icon: Optional[str], color: Optional[str]) -> dict:
    srv = _server()
    try:
        new_proj = _projects_module.add_project(
            repo=repo_url,
            icon=icon or "ti-folder",
            color=color or "gray",
        )
        if srv._BACKUP_AVAILABLE:
            try:
                srv._backup_module.schedule_backup()
            except Exception:
                pass
        return new_proj
    except FileExistsError as e:
        raise HTTPException(409, detail=str(e))
    except ValueError as e:
        raise HTTPException(422, detail=str(e))
    except subprocess.CalledProcessError as e:
        raise srv._gh_error(e)


def delete_project(
    owner: str,
    repo_name: str,
    delete_local_folders: bool,
    delete_github_repo: bool,
) -> dict:
    srv = _server()
    repo = f"{owner}/{repo_name}"

    if not any(p["repo"] == repo for p in _projects_module.load_projects()):
        raise HTTPException(404, detail="Project not found")

    removed: list[str] = []
    removed.extend(_projects_module.remove_project(repo))

    if delete_local_folders:
        projects_dir = Path.home() / "dev"
        project_root = projects_dir / repo_name
        nested = (project_root / "main").exists() and (project_root / "main" / ".git").exists()
        if nested:
            if project_root.exists():
                shutil.rmtree(project_root)
                removed.append(str(project_root))
        else:
            uat_dir = project_root / "uat"
            if uat_dir.exists():
                shutil.rmtree(uat_dir)
                removed.append(str(uat_dir))
            for suffix in ("", "-coder", "-tester"):
                d = projects_dir / f"{repo_name}{suffix}"
                if d.exists():
                    shutil.rmtree(d)
                    removed.append(str(d))

    if delete_github_repo:
        result = subprocess.run(
            ["gh", "repo", "delete", repo, "--yes"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise HTTPException(502, detail=f"Failed to delete GitHub repository: {err}")
        removed.append(f"GitHub repo {repo}")

    if srv._BACKUP_AVAILABLE:
        try:
            srv._backup_module.schedule_backup()
        except Exception:
            pass

    return {"ok": True, "removed": removed}
