"""Service logic for the sprints router (extracted from server.py, issue #795).

The movable sprint surfaces from the monolith. The heavy lifting (GitHub
client, project-root resolution, sprint-state helpers) still lives in and is
mutated by ``server.py``, so the service reads it back through a deferred
import at request time — keeping a single source of truth and avoiding the
import-time circular dependency (``server.py`` imports this package while
mounting routers).

Out of this wave (pinned to server.py by pre-existing tests the AC forbids
modifying): run_sprint_managed, rerun_sprint, finish_sprint, set_sprint_status,
get_sprint_management_issues, get_sprint_estimate_vs_actual,
get_sprint_estimate_summary, get_sprint_branch_status.
"""
from __future__ import annotations

import subprocess
import sys as _sys
from pathlib import Path

from fastapi import HTTPException

# server.py is a top-level module on the dashboard path; make sure it resolves.
_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
if str(_DASHBOARD_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_DASHBOARD_ROOT))


def _server():
    """Deferred import of the monolith — safe at request time."""
    import server  # noqa: PLC0415 — intentional late import (see module docstring)
    return server


def get_sprints():
    srv = _server()
    try:
        sprints = srv.github_client.list_sprints()
        default = srv.github_client.latest_active_sprint()
        return {"sprints": sprints, "default": default}
    except subprocess.CalledProcessError as e:
        raise srv._gh_error(e)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))


def get_sprint_goal(project: str, sprint: str):
    """Return the persisted sprint goal for a project/sprint."""
    srv = _server()
    project_root = srv._project_root_path(project)
    goal_path = srv._sprint_goal_path(project_root, sprint)
    if goal_path.exists():
        return {"goal": goal_path.read_text(encoding="utf-8").strip()}
    return {"goal": ""}


def save_sprint_goal(project: str, sprint_label: str, goal: str):
    """Persist sprint goal to .commander/sprints/<label>-goal.txt."""
    srv = _server()
    if not srv._SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")
    project_root = srv._project_root_path(project)
    goal_path = srv._sprint_goal_path(project_root, sprint_label)
    goal_path.parent.mkdir(parents=True, exist_ok=True)
    goal_path.write_text(goal, encoding="utf-8")
    return {"ok": True}


def get_sprint_order(project: str):
    """Return the persisted sprint display order for a project slug."""
    srv = _server()
    project_root = srv._project_root_path(project)
    try:
        sprints = srv.github_client.list_sprints(repo_name=None)
    except Exception:
        sprints = []
    order = srv._load_sprint_order(project_root, sprints)
    return {"order": order}


def save_sprint_order(project: str, order: list[str]):
    """Persist sprint display order for a project slug."""
    import json

    srv = _server()
    project_root = srv._project_root_path(project)
    order_path = srv._sprint_order_path(project_root)
    order_path.parent.mkdir(parents=True, exist_ok=True)
    order_path.write_text(json.dumps(order), encoding="utf-8")
    return {"ok": True}


def get_all_running_sprints():
    """Return ALL currently running sprints across all projects.

    Reads plan.json state=running as the authoritative source (issue #507).
    PID files are retained only for process-killing.

    Returns: {"running": [{"project": ..., "sprint_label": ...}, ...]}
    Empty list means no sprints are running.
    """
    srv = _server()
    all_running = srv._all_sprints_running()
    return {"running": all_running}


def get_dispatch_log(sprint_label: str, project: str, tail_lines: int = 200):
    """Return the last N lines of the most recent sprint-run-<label>-*.log."""
    srv = _server()
    if not srv._SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")
    from log_source import read_log  # local import keeps startup fast

    project_root = srv._project_root_path(project)
    return read_log("dispatch", project_root, label=sprint_label, tail_lines=tail_lines)
