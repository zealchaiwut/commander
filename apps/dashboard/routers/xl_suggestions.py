"""XL split suggestions endpoints (issue #1424).

Routes in this module:
  POST /api/sprints/{sprint_label}/xl-suggestions/{issue_number}/dismiss

Dismiss logic is thin here — business logic lives in xl_suggestions_service.py.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import xl_suggestions_service as _svc

router = APIRouter(tags=["xl_suggestions"])


class _DismissBody(BaseModel):
    project: str


@router.post("/api/sprints/{sprint_label}/xl-suggestions/{issue_number}/dismiss")
def dismiss_xl_suggestion(sprint_label: str, issue_number: int, body: _DismissBody):
    """Persist a dismiss decision for one ticket in a sprint (AC8).

    The dismissed ticket will no longer appear in xl_suggestions in subsequent
    preflight calls for this sprint. Dismiss is additive and idempotent.

    Returns {ok: true, issue_number, sprint_label}.
    """
    def _server():
        import server  # noqa: PLC0415
        return server

    srv = _server()
    if not srv._SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    project_root = srv._project_root_path(body.project)
    dismissed = _svc.load_xl_dismissed(project_root, sprint_label)
    dismissed.add(issue_number)
    _svc.save_xl_dismissed(project_root, sprint_label, dismissed)

    return {"ok": True, "issue_number": issue_number, "sprint_label": sprint_label}
