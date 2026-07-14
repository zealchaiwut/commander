"""Scheduled overnight sprint queue endpoints (issue #863).

Thin HTTP surface over ``services.sprint_manager.sprint_scheduler`` (pure queue
logic) and the sibling ``scheduler_service`` (dashboard-coupled glue: sprint
provider, runner, lock, alert, clock).

This router declares full ``/api/scheduler/*`` paths and carries no prefix, so
it rides the already-mounted ``sprints_router`` via ``include_router`` in
``routers/sprints.py`` — no route lands in ``server.py``
(COMMANDER_GATE_MONOLITH, issue #761).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.sprint_manager import sprint_scheduler as _sched
from . import scheduler_service
from .board_cache import invalidate_board

router = APIRouter(tags=["scheduler"])


class ScheduleConfigBody(BaseModel):
    project: str
    scheduled_run_time: str = ""


class RunOnScheduleBody(BaseModel):
    project: str
    sprint_label: str
    enabled: bool


class TickBody(BaseModel):
    project: str


@router.get("/api/scheduler/config")
def get_schedule_config(project: str):
    """Return the project's scheduled run time (``""`` when disabled)."""
    return {"project": project, "scheduled_run_time": _sched.get_scheduled_time(project)}


@router.put("/api/scheduler/config")
def set_schedule_config(body: ScheduleConfigBody):
    """Set the project's scheduled run time. Empty disables auto-scheduling (AC1)."""
    try:
        stored = _sched.set_scheduled_time(body.project, body.scheduled_run_time)
    except _sched.ScheduleError as e:
        raise HTTPException(400, detail=str(e))
    invalidate_board(body.project)
    return {"project": body.project, "scheduled_run_time": stored}


@router.get("/api/scheduler/sprints")
def get_run_on_schedule_map(project: str):
    """Return the ``{label: true}`` Run-on-schedule map (default empty — AC3)."""
    return {"project": project, "run_on_schedule": _sched.get_run_on_schedule_map(project)}


@router.put("/api/scheduler/sprints")
def set_run_on_schedule(body: RunOnScheduleBody):
    """Toggle Run-on-schedule for a sprint (defaults off, AC3)."""
    value = _sched.set_run_on_schedule(body.project, body.sprint_label, body.enabled)
    invalidate_board(body.project)
    return {
        "project": body.project,
        "sprint_label": body.sprint_label,
        "enabled": value,
    }


@router.post("/api/scheduler/tick", status_code=202)
def scheduler_tick(body: TickBody):
    """Fire the scheduled queue if it is due right now.

    Intended to be POSTed once a minute by the external runner (launchd / cron),
    matching Commander's launchd-is-the-authoritative-runner model. Returns
    immediately; when due, the sequential queue runs in a background thread so a
    multi-hour overnight batch never blocks the request.
    """
    return scheduler_service.tick(body.project)
