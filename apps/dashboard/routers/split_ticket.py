"""Split-ticket flow API (issue #1423).

Endpoints:
  POST /api/sprints/{sprint_label}/tickets/{issue_number}/split/suggest
  POST /api/sprints/{sprint_label}/tickets/{issue_number}/split/confirm

Route handlers are thin; all business logic lives in split_ticket_service.
"""
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import split_ticket_service

router = APIRouter(tags=["split-ticket"])


class SuggestBody(BaseModel):
    project: str


class ConfirmBody(BaseModel):
    project: str
    child1_title: str
    child1_body: str = ""
    child2_title: str
    child2_body: str = ""
    action: Literal["remove", "close"] = "remove"


@router.post("/api/sprints/{sprint_label}/tickets/{issue_number}/split/suggest")
def split_suggest(sprint_label: str, issue_number: int, body: SuggestBody):
    """Generate two draft child titles and AC scopes via a lightweight BA call (AC2).

    Returns {child1: {title, ac_scope}, child2: {title, ac_scope}, fallback: bool}.
    When the BA call is unavailable, fallback=True and titles are empty strings (AC7).
    """
    try:
        result = split_ticket_service.suggest_split(
            issue_num=issue_number,
            project=body.project,
        )
    except Exception as exc:
        raise HTTPException(500, detail=f"Suggest failed: {exc}") from exc
    return result


@router.post("/api/sprints/{sprint_label}/tickets/{issue_number}/split/confirm")
def split_confirm(sprint_label: str, issue_number: int, body: ConfirmBody):
    """Create two child issues, handle the original, and trigger estimates (AC3–AC5).

    Returns {child1_num, child2_num, ok}.
    Raises 400 when child titles are missing.
    """
    if not body.child1_title.strip() or not body.child2_title.strip():
        raise HTTPException(400, detail="Both child titles are required")

    try:
        result = split_ticket_service.confirm_split(
            issue_num=issue_number,
            sprint_label=sprint_label,
            project=body.project,
            child1_title=body.child1_title.strip(),
            child1_body=body.child1_body,
            child2_title=body.child2_title.strip(),
            child2_body=body.child2_body,
            action=body.action,
        )
    except Exception as exc:
        raise HTTPException(500, detail=f"Split failed: {exc}") from exc
    return result
