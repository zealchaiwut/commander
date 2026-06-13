"""Sprint sign-off endpoints (issue #862).

Approve / Reject actions for a sprint sitting in the pending sign-off gate.
Service logic lives in the sibling ``signoff_service`` module; route handlers
stay thin per the routers AGENTS.md contract.
"""
from fastapi import APIRouter
from pydantic import BaseModel

from . import signoff_service

router = APIRouter(tags=["signoff"])


class SprintSignoffBody(BaseModel):
    project: str


@router.post("/api/sprints/{sprint_label}/approve")
def approve_sprint(sprint_label: str, body: SprintSignoffBody):
    """Approve a pending sprint — clear the gate and record the approver."""
    return signoff_service.approve_sprint(body.project, sprint_label)


@router.post("/api/sprints/{sprint_label}/reject")
def reject_sprint(sprint_label: str, body: SprintSignoffBody):
    """Reject a pending sprint — dissolve it and return tickets to the backlog."""
    return signoff_service.reject_sprint(body.project, sprint_label)
