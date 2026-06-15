"""Roadmap Suggestions panel endpoints (issue #882).

Accept and Dismiss actions for advisor suggestion cards shown in the Roadmap tab.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from . import advisor_service
from . import suggestions_service

router = APIRouter(tags=["suggestions"])


@router.post("/api/projects/{project}/advisor/suggestions/{suggestion_id}/dismiss")
def dismiss_suggestion(project: str, suggestion_id: int):
    """Dismiss a suggestion card; persists the pitch so it never reappears."""
    try:
        return advisor_service.dismiss_suggestion(project, suggestion_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/projects/{project}/advisor/suggestions/{suggestion_id}/accept")
def accept_suggestion(project: str, suggestion_id: int):
    """Return a BA seed prompt built from the suggestion card."""
    suggestion = advisor_service.get_suggestion_by_id(project, suggestion_id)
    if suggestion is None:
        raise HTTPException(status_code=404, detail=f"Suggestion {suggestion_id} not found")
    return suggestions_service.build_accept_prompt(suggestion)


@router.get("/api/projects/{project}/advisor/dismissed")
def get_dismissed(project: str):
    """Return dismissed pitch strings for *project*."""
    return {"dismissed": advisor_service.get_dismissed_pitches(project)}
