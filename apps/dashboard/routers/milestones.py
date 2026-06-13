"""Milestone selector endpoints (issue #879).

Backs the milestone selector in the BA bulk-create and single new-ticket
dialogs: list a project's available GitHub milestones and mark the Active one
(the default selection). Read-only — creating/editing milestones is the Roadmap
tab's job (issue #878, out of scope here).

Mounted via ``include_router`` so no route lands in the server.py monolith
(COMMANDER_GATE_MONOLITH, issue #761).
"""
from __future__ import annotations

from fastapi import APIRouter

from . import milestones_service as service

router = APIRouter(prefix="/api/milestones", tags=["milestones"])


@router.get("")
def list_milestones(repo: str):
    """Available milestones for the selector + the Active milestone default."""
    return service.list_milestones(repo)
