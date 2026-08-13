"""Milestone selector endpoint (issue #879).

Backs the milestone selector in the BA bulk-create and single new-ticket
dialogs: list a project's available GitHub milestones and mark the Active one
(the default selection).

Mounted via ``include_router`` so no route lands in the server.py monolith
(COMMANDER_GATE_MONOLITH, issue #761).
"""
from __future__ import annotations

from fastapi import APIRouter

from . import milestones_service

router = APIRouter(tags=["milestones"])


@router.get("/api/milestones")
def selector_list_milestones(repo: str):
    """Available milestones for the selector + the Active milestone default."""
    return milestones_service.list_milestones(repo)
