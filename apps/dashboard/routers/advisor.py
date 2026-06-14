"""HTTP surface for the daily advisor agent (issue #881).

New endpoints live here, never in ``server.py`` (COMMANDER_GATE_MONOLITH,
issue #761). Service logic lives in the sibling ``advisor_service`` module.

Routes:
  GET  /api/projects/{project}/advisor/suggestions — current draft suggestions
  POST /api/projects/{project}/advisor/run         — trigger on-demand run
  POST /api/advisor/tick                           — daily schedule tick
"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from . import advisor_service

router = APIRouter(tags=["advisor"])


class TickBody(BaseModel):
    project: str


@router.get("/api/projects/{project}/advisor/suggestions")
def get_advisor_suggestions(project: str):
    """Return the current draft suggestions for *project* (AC8).

    Returns an empty list when the advisor has never run for this project.
    """
    return advisor_service.get_suggestions(project)


@router.post("/api/projects/{project}/advisor/run", status_code=200)
def run_advisor_on_demand(project: str):
    """Trigger an immediate advisor run for *project* (AC6).

    Runs synchronously so the caller receives the suggestions in the response.
    """
    try:
        suggestions = advisor_service.run_advisor(project, on_demand=True)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"suggestions": suggestions, "on_demand": True}


@router.get("/api/projects/{project}/advisor/look-ahead")
def get_advisor_look_ahead(project: str):
    """Return the current look-ahead entries for *project* (issue #883).

    Returns an empty list when the advisor has not yet produced a look-ahead.
    Never creates any GitHub objects.
    """
    return {"look_ahead": advisor_service.get_look_ahead(project)}


@router.post("/api/advisor/tick", status_code=202)
def advisor_tick(body: TickBody):
    """Fire the daily advisor for *project* if it is due right now (AC1, AC10).

    Intended to be POSTed once a minute by the external runner (launchd / cron).
    Returns immediately; when due, the run fires in a background thread.
    """
    return advisor_service.tick(body.project)
