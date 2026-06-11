"""Service logic for the activity router (extracted from server.py, issue #794).

The activity surfaces read agent/event history straight from SQLite via the
shared ``db`` module and resolve project slugs via the shared ``projects``
module — the same module objects the monolith uses, so test patches against
``server.projects_module`` / ``db`` still apply here.

Out of this wave (pinned to server.py by pre-existing tests the AC forbids
modifying): ``/api/logs/runs`` (test_419) and ``/api/logs/sync-github``
(test_630).
"""
from __future__ import annotations

import json
import sys as _sys
from pathlib import Path
from typing import Optional

from fastapi import HTTPException

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
if str(_DASHBOARD_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_DASHBOARD_ROOT))

import db  # noqa: E402
import projects as projects_module  # noqa: E402

# Mirrored from server.py. This endpoint was the only consumer of the constant,
# so it moves with the handler (the server.py copy is removed in the same PR).
_VALID_EVENT_SOURCES = {"agent", "dashboard", "github"}


def list_agents():
    return db.get_agents()


def list_events():
    return db.get_recent_events()


def get_project_events(
    slug: str,
    source: Optional[str] = None,
    target: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 100,
):
    """Return structured events for a project, newest-first.

    Filters: source (agent|dashboard|github), target (exact), since/until (ISO date), limit.
    404 — unknown project slug.
    400 — invalid source value.
    """
    # Validate source before any DB work
    if source is not None and source not in _VALID_EVENT_SOURCES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid source {source!r}. Must be one of: {', '.join(sorted(_VALID_EVENT_SOURCES))}",
        )

    # Resolve slug → project name stored in events table
    try:
        all_projects = projects_module.load_projects()
    except Exception:
        all_projects = []

    matched = next(
        (p for p in all_projects
         if p["repo"].split("/")[-1] == slug or p["repo"] == slug),
        None,
    )
    if matched is None:
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")

    # The events table stores project as the full repo path (owner/repo)
    project_key = matched["repo"]

    query = "SELECT timestamp, source, actor, type, target, action_id, detail FROM events WHERE project = ?"
    params: list = [project_key]

    if source is not None:
        query += " AND source = ?"
        params.append(source)
    if target is not None:
        query += " AND target = ?"
        params.append(target)
    if since is not None:
        query += " AND timestamp >= ?"
        params.append(since)
    if until is not None:
        # include the full day by appending T23:59:59 when only a date is given
        until_bound = until if "T" in until else f"{until}T23:59:59"
        query += " AND timestamp <= ?"
        params.append(until_bound)

    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)

    with db.get_conn() as conn:
        rows = conn.execute(query, params).fetchall()

    result = []
    for row in rows:
        d = dict(row)
        try:
            d["detail"] = json.loads(d["detail"])
        except (TypeError, ValueError):
            pass
        result.append(d)

    # issue #764: enrich agent_finished rows with the durable duration_seconds
    # from agent_runs so the Activity tab can show a Duration column. Matched by
    # issue number + agent role; falls back to the duration already carried in
    # the event detail when no agent_runs row is found.
    _agent_finished = [
        d for d in result
        if d.get("type") == "agent_finished" and isinstance(d.get("detail"), dict)
    ]
    if _agent_finished:
        _issue_nums = {
            d["detail"].get("issue_num")
            for d in _agent_finished
            if d["detail"].get("issue_num") is not None
        }
        _runs_by_key: dict = {}
        try:
            for _num in _issue_nums:
                for _r in db.agent_runs_for_issue(int(_num)):
                    if _r.get("duration_seconds") is None:
                        continue
                    _runs_by_key[(int(_num), str(_r.get("agent", "")).lower())] = _r["duration_seconds"]
        except Exception:
            _runs_by_key = {}
        for d in _agent_finished:
            det = d["detail"]
            num = det.get("issue_num")
            role = str(det.get("role", "")).lower()
            dur = None
            if num is not None:
                dur = _runs_by_key.get((int(num), role))
            if dur is None:
                dur = det.get("duration")
            if dur is not None:
                d["duration_seconds"] = dur

    return result
