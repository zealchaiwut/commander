"""Brief assembly endpoints (issue #839).

Two read-only endpoints that assemble a deterministic brief payload from local
DB tables — no LLM, no network. The per-project endpoint serves one project's
sections; the home endpoint rolls up across all projects.

The route handlers are thin; all assembly lives in ``brief_service``. This
router is mounted onto an already-wired router (``sprints``) so no route is
added to ``server.py`` (COMMANDER_GATE_MONOLITH, issue #761).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from . import brief_service

router = APIRouter(tags=["brief"])


# ── response models ───────────────────────────────────────────────────────────

class CurrentTicket(BaseModel):
    issue_number: Optional[int] = None
    title: str = ""


class Progress(BaseModel):
    done: int = 0
    total: int = 0
    percent: int = 0


class ShippedSprint(BaseModel):
    label: Optional[str] = None
    goal: str = ""
    features: list[str] = []
    done: int = 0
    skipped: int = 0
    duration: Optional[int] = None
    pr_number: Optional[int] = None
    summary_issue_number: Optional[int] = None


class InProgress(BaseModel):
    sprint_label: Optional[str] = None
    current_ticket: Optional[CurrentTicket] = None
    active_agent: Optional[str] = None
    progress: Progress = Progress()
    elapsed: Optional[int] = None


class UpNext(BaseModel):
    label: Optional[str] = None
    ticket_count: int = 0
    ready: bool = False


class BlockedTicket(BaseModel):
    issue_number: Optional[int] = None
    title: str = ""
    type: str


class ProjectKpis(BaseModel):
    sprints_shipped: int = 0
    tickets_done: int = 0
    in_progress: bool = False
    in_progress_percent: int = 0
    needs_you: int = 0


class ActivityItem(BaseModel):
    time: Optional[str] = None
    source: Optional[str] = None
    message: str = ""


class ProjectBrief(BaseModel):
    project: str
    date: str
    shipped: list[ShippedSprint] = []
    in_progress: Optional[InProgress] = None
    up_next: Optional[UpNext] = None
    blocked: list[BlockedTicket] = []
    kpis: ProjectKpis = ProjectKpis()
    recent_activity: list[ActivityItem] = []


class GlobalKpis(BaseModel):
    sprints_shipped: int = 0
    tickets_done: int = 0
    in_progress: int = 0
    needs_your_call: int = 0


class Decision(BaseModel):
    project: str
    type: str
    label: Optional[str] = None
    suggested_action: str


class HomeBrief(BaseModel):
    date: str
    global_kpis: GlobalKpis = GlobalKpis()
    decisions: list[Decision] = []
    projects: list[ProjectBrief] = []


# ── routes ────────────────────────────────────────────────────────────────────

@router.get("/api/projects/{slug}/brief", response_model=ProjectBrief)
def get_project_brief(slug: str, date: Optional[str] = None):
    """Assemble one project's brief for the given (or today's) window."""
    return brief_service.build_project_brief(slug, date=date)


@router.get("/api/brief", response_model=HomeBrief)
def get_home_brief(date: Optional[str] = None):
    """Assemble the home roll-up across all tracked projects."""
    return brief_service.build_home_brief(date=date)
