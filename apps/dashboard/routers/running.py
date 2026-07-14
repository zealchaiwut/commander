"""GET /api/running?project= snapshot endpoint (issue #1645).

Returns the running sprint status and per-ticket progress for a project in
a single request, suitable for the Running pane's first paint.  Reads
exclusively from the mirror/DB — no GitHub API calls.

Routes owned by this module:
  GET /api/running?project=<id>  — running sprint snapshot
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
if str(_DASHBOARD_ROOT) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_ROOT))

from routers.running_service import build_running_snapshot  # noqa: E402

router = APIRouter(tags=["running"])


@router.get("/api/running")
def get_running_snapshot(
    project: str = Query(..., description="Project identifier (owner/repo)"),
):
    """Return the running sprint status and per-ticket progress for a project.

    HTTP 200 — a JSON body with running sprint status and per-ticket progress
    fields (sprint_label, started_at, done/pending/failed counts, issues list,
    active_agents, levels, etc.).

    HTTP 404 — no sprint is currently running for the given project.

    Reads exclusively from in-memory state, local disk files, and the SQLite
    agent_runs DB. No GitHub API client methods are invoked.
    """
    snapshot = build_running_snapshot(project)
    if snapshot is None:
        raise HTTPException(
            status_code=404,
            detail=f"No running sprint found for project {project!r}",
        )
    return snapshot
