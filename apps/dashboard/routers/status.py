"""Generalized status endpoint (GET /api/status) for monitoring clients.

A domain-grouped, LOCAL-ONLY snapshot of commander's state — polled every ~10s
by a Stream Deck and, later, a monitoring board. Makes ZERO GitHub API calls;
all assembly lives in ``status_service``. New endpoints belong here in
``routers/``, never in ``server.py`` (COMMANDER_GATE_MONOLITH).

Thin sub-resources return the matching slice of the same snapshot:
  GET /api/status/sprints  -> the sprints array
  GET /api/status/health   -> the health object
  GET /api/status/queue    -> the queue object
"""
from __future__ import annotations

from fastapi import APIRouter

from . import status_service

router = APIRouter(tags=["status"])


@router.get("/api/status")
def get_status() -> dict:
    """Full domain-grouped status snapshot. Local sources only; never raises."""
    return status_service.build_status()


@router.get("/api/status/sprints")
def get_status_sprints() -> list[dict]:
    """Per-project sprint state slice (running / failed / completed / idle)."""
    return status_service.sprints()


@router.get("/api/status/health")
def get_status_health() -> dict:
    """Health slice: watchdog/heartbeat + (future) github rate-limit telemetry."""
    return status_service.health()


@router.get("/api/status/queue")
def get_status_queue() -> dict:
    """Queue slice: uat_pending / backlog (mirror) + errors_today (events)."""
    return status_service.queue()
