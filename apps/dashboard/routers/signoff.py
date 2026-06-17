"""Sprint sign-off endpoints (issue #862).

Approve / Reject actions for a sprint sitting in the pending sign-off gate.
Service logic lives in the sibling ``signoff_service`` module; route handlers
stay thin per the routers AGENTS.md contract.
"""
import config
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import signoff_service

router = APIRouter(tags=["signoff"])


class SprintSignoffBody(BaseModel):
    project: str


def _require_signoff_enabled() -> None:
    if config.sprint_signoff_disabled():
        raise HTTPException(404, detail="Sprint sign-off is disabled")


@router.post("/api/sprints/{sprint_label}/approve")
def approve_sprint(sprint_label: str, body: SprintSignoffBody):
    """Approve a pending sprint — clear the gate and record the approver."""
    _require_signoff_enabled()
    return signoff_service.approve_sprint(body.project, sprint_label)


@router.post("/api/sprints/{sprint_label}/reject")
def reject_sprint(sprint_label: str, body: SprintSignoffBody):
    """Reject a pending sprint — dissolve it and return tickets to the backlog."""
    _require_signoff_enabled()
    return signoff_service.reject_sprint(body.project, sprint_label)
