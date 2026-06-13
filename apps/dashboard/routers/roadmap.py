"""Roadmap endpoints (issue #878).

Backs the project Roadmap tab: list milestone cards with ticket progress,
inline create / edit / close / reopen of milestones, and persistence of the
two view settings (active milestone, card order). Business logic lives in the
sibling ``roadmap_service`` module; these handlers stay thin.

Mounted via ``include_router`` so no route lands in the server.py monolith
(COMMANDER_GATE_MONOLITH, issue #761).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from . import roadmap_service as service

router = APIRouter(prefix="/api/roadmap", tags=["roadmap"])


class MilestoneIn(BaseModel):
    title: str
    description: str = ""
    due_on: Optional[str] = None


class MilestoneEdit(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    due_on: Optional[str] = None


class RoadmapSettingsIn(BaseModel):
    active_milestone: Optional[int] = None
    order: Optional[list[int]] = None


@router.get("")
def get_roadmap(repo: str):
    """Milestone cards (open, ordered) + closed history rows + view settings."""
    return service.get_roadmap(repo)


@router.post("/milestones")
def create_milestone(repo: str, body: MilestoneIn):
    """Inline-create a milestone (AC6)."""
    return service.create_milestone(repo, body.title, body.description, body.due_on)


@router.patch("/milestones/{number}")
def edit_milestone(number: int, repo: str, body: MilestoneEdit):
    """Inline-edit title / description / due date (AC7)."""
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    return service.update_milestone(repo, number, fields)


@router.post("/milestones/{number}/close")
def close_milestone(number: int, repo: str):
    """Close a milestone — UI collapses it into a history row (AC8)."""
    return service.close_milestone(repo, number)


@router.post("/milestones/{number}/reopen")
def reopen_milestone(number: int, repo: str):
    """Re-open a closed milestone from its history row (AC9)."""
    return service.reopen_milestone(repo, number)


@router.put("/settings")
def put_settings(repo: str, body: RoadmapSettingsIn):
    """Persist active milestone and/or card order (AC4 / AC5 / AC12)."""
    return service.update_settings(
        repo, active_milestone=body.active_milestone, order=body.order
    )
