"""Home / sprint-nav active-milestone endpoint (issue #880).

Backs the compact milestone progress indicator on the home project cards and
in the project header (sprint nav). Business logic lives in the sibling
``home_milestone_service`` module; this handler stays thin.

Mounted via ``include_router`` so no route lands in the server.py monolith
(COMMANDER_GATE_MONOLITH, issue #761).
"""
from __future__ import annotations

from fastapi import APIRouter

from . import home_milestone_service as service

router = APIRouter(prefix="/api/home", tags=["home-milestone"])


@router.get("/milestone")
def get_home_milestone(repo: str) -> dict:
    """Active milestone name + progress for *repo*.

    Returns ``{}`` when the project has no active milestone so the frontend can
    render the card / header normally with no indicator (AC3).
    """
    return service.active_milestone_progress(repo) or {}
