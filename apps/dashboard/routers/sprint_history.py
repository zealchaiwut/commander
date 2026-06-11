"""Sprint-history endpoint (issue #805).

A local-only, GitHub-free feed of terminal sprint records for the ledger UI.
The route is thin; all assembly lives in ``sprint_history_service``. New
endpoints belong here in ``routers/``, never in ``server.py`` (COMMANDER_GATE_MONOLITH).
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from . import sprint_history_service

router = APIRouter(tags=["sprint-history"])


class SprintHistoryIssue(BaseModel):
    ticket_id: int | None = None
    state: str
    time_spent: int | None = None
    pr_number: int | None = None


class SprintHistoryItem(BaseModel):
    label: str | None = None
    project: str = ""
    lifecycle_state: str
    duration: int | None = None
    tokens: int | None = None
    estimate_accuracy: float | None = None
    pr_number: int | None = None
    summary_path: str | None = None
    issues: list[SprintHistoryIssue] = []


class SprintHistoryResponse(BaseModel):
    sprints: list[SprintHistoryItem]
    offset: int
    limit: int
    total: int


@router.get("/api/sprints/history", response_model=SprintHistoryResponse)
def get_sprint_history(offset: int = 0, limit: int = 20):
    """Return paginated, enriched sprint-history rows. Makes no GitHub calls."""
    return sprint_history_service.get_sprint_history(offset=offset, limit=limit)
