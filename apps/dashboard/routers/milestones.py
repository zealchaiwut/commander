"""Milestone + mirror-issues endpoints (issue #877).

Exposes GitHub's native Milestones API through the dashboard and serves issues
from the local mirror (with the milestone field) so reads cost zero GitHub
quota. Service logic lives in the sibling ``milestones_service`` module.
"""
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from . import milestones_service

router = APIRouter(tags=["milestones"])


class MilestoneCreate(BaseModel):
    title: str
    description: Optional[str] = None
    due_on: Optional[str] = None


class MilestoneUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    due_on: Optional[str] = None
    state: Optional[str] = None


@router.get("/api/projects/{slug}/milestones")
def list_milestones(slug: str, state: Optional[str] = None):
    """List milestones for a project from the mirror (live fallback pre-sync)."""
    return milestones_service.list_milestones(slug, state=state)


@router.post("/api/projects/{slug}/milestones", status_code=201)
def create_milestone(slug: str, body: MilestoneCreate):
    """Create a milestone on GitHub (title required; description/due_on optional)."""
    return milestones_service.create_milestone(slug, body.model_dump(exclude_none=True))


@router.patch("/api/projects/{slug}/milestones/{number}")
def update_milestone(slug: str, number: int, body: MilestoneUpdate):
    """Edit a milestone's title, description, due date, or state."""
    return milestones_service.update_milestone(slug, number, body.model_dump(exclude_none=True))


@router.delete("/api/projects/{slug}/milestones/{number}")
def close_milestone(slug: str, number: int):
    """Close a milestone on GitHub (DELETE maps to PATCH state=closed)."""
    return milestones_service.close_milestone(slug, number)


@router.get("/api/projects/{slug}/issues")
def list_issues(slug: str, state: Optional[str] = None):
    """List mirrored issues for a project, each carrying its milestone field."""
    return milestones_service.list_issues(slug, state=state)
