"""Roadmap Suggestions panel endpoints (issue #882).

Accept and Dismiss actions for advisor suggestion cards shown in the Roadmap tab.

Routes:
  POST /api/projects/{project}/advisor/suggestions/{id}/dismiss — dismiss a card
  POST /api/projects/{project}/advisor/suggestions/{id}/accept  — accept → BA seed
  GET  /api/projects/{project}/advisor/dismissed                 — dismissed pitches

New endpoints only — never in server.py (COMMANDER_GATE_MONOLITH, issue #761).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from . import advisor_service
from . import suggestions_service

router = APIRouter(tags=["suggestions"])


@router.post("/api/projects/{project}/advisor/suggestions/{suggestion_id}/dismiss")
def dismiss_suggestion(project: str, suggestion_id: int):
    """Dismiss a suggestion card; persists the pitch so it never reappears (AC6, AC8).

    Returns ``{"dismissed": True, "pitch": "<pitch text>"}``.
    """
    try:
        result = advisor_service.dismiss_suggestion(project, suggestion_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return result


@router.post("/api/projects/{project}/advisor/suggestions/{suggestion_id}/accept")
def accept_suggestion(project: str, suggestion_id: int):
    """Return a BA seed prompt built from the suggestion card (AC3, AC4, AC5).

    The frontend uses the returned ``seed_prompt`` to start a bulk-create job
    (POST /api/tickets/bulk), then navigates to the bulk-create review UI.

    Returns:
      seed_prompt   — BA prompt string containing pitch / rationale / milestone / scope
      suggestion_id — DB id of the accepted suggestion (for attribution in the UI)
      pitch         — one-line pitch label
      milestone_name — GitHub milestone title to apply when posting the drafted tickets
    """
    rows = advisor_service.get_suggestions(project)
    suggestion = next((r for r in rows if r["id"] == suggestion_id), None)
    if suggestion is None:
        raise HTTPException(status_code=404, detail=f"Suggestion {suggestion_id} not found")
    return suggestions_service.build_accept_prompt(suggestion)


@router.get("/api/projects/{project}/advisor/dismissed")
def get_dismissed(project: str):
    """Return the list of dismissed pitch strings for *project* (AC7, AC8)."""
    pitches = advisor_service.get_dismissed_pitches(project)
    return {"dismissed": pitches}
