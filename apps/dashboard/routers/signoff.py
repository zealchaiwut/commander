"""Sprint sign-off endpoints (issue #862, #2305).

Approve / Reject actions for a sprint sitting in the pending sign-off gate.
Per-sprint UAT sign-off actions (issue #2305) close exactly the UAT tickets
carrying the target sprint label (and its child labels).
Service logic lives in the sibling ``signoff_service`` module; route handlers
stay thin per the routers AGENTS.md contract.
"""
import config
from fastapi import APIRouter, HTTPException, Query
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


@router.get("/api/sprints/{sprint_label}/uat-preview")
def uat_signoff_preview(sprint_label: str, project: str = Query(...)):
    """Dry-run: list UAT tickets that would be closed for this sprint.

    Returns count + issue numbers so the operator can confirm scope before
    committing. Includes child sprint labels (sprint-N.1, .2, .3).
    """
    return signoff_service.uat_signoff_preview(project, sprint_label)


@router.post("/api/sprints/{sprint_label}/uat-signoff")
def uat_signoff(sprint_label: str, body: SprintSignoffBody):
    """Close all open UAT tickets for this sprint (and child labels).

    Closes exactly the tickets carrying this sprint label — no other sprints
    are touched. Call GET uat-preview first to confirm scope.
    """
    return signoff_service.uat_signoff_apply(body.project, sprint_label)
