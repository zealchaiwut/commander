"""Issues route handlers extracted from server.py (issue #1267).

Routes owned by this module:
  GET  /api/issues                            — list open issues
  POST /api/issues/{issue_id}/approve         — approve a UAT issue
  POST /api/issues/{issue_id}/reject          — reject a UAT issue
  POST /api/issues/{issue_id}/close           — close an issue
  GET  /api/issues/{issue_id}/test-report     — fetch test report for an issue
  POST /api/issues/{issue_id}/sprint-label    — assign/change sprint label

Request models moved from server.py:
  RejectBody
  SprintLabelBody

Shared server.py helpers (_emit_dashboard_event, _SPRINT_LABEL_RE, _SPRINT_LABEL_RE_ALL)
are accessed via the deferred ``_server()`` import to avoid circular imports.
"""
from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _DASHBOARD_ROOT.parent.parent
_SERVICES_ROOT = _REPO_ROOT / "services" / "sprint_manager"
for _p in (str(_DASHBOARD_ROOT), str(_SERVICES_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import github_client  # noqa: E402
from services.logging import log as _slog  # noqa: E402
from .board_cache import invalidate_board  # noqa: E402

router = APIRouter(tags=["issues"])


def _server():
    """Deferred import of the monolith — safe at request time, avoids circular import."""
    import server  # noqa: PLC0415
    return server


# ── helpers ───────────────────────────────────────────────────────────────────

def _gh_error(e: subprocess.CalledProcessError) -> HTTPException:
    """Map a GitHub CLI error to an HTTP exception.

    Delegates to the monolith's _gh_error which handles rate-limit 429 mapping.
    Defined locally here to avoid importing sprint_nav (cross-router coupling);
    the implementation mirrors sprint_nav._gh_error exactly.
    """
    return _server()._gh_error(e)


# ── request models ─────────────────────────────────────────────────────────────

class RejectBody(BaseModel):
    reason: str


class SprintLabelBody(BaseModel):
    # Accepts either sprint_label (e.g. "sprint-3" or null for backlog) or
    # the legacy sprint: int field for backward compatibility.
    sprint_label: Optional[str] = None
    sprint: Optional[int] = None
    project: Optional[str] = None


# ── routes ────────────────────────────────────────────────────────────────────

@router.get("/api/issues")
def get_issues(sprint: Optional[int] = None):
    try:
        if sprint is None:
            return github_client.list_all_open_issues()
        return github_client.list_issues(sprint)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))


@router.post("/api/issues/{issue_id}/approve")
def approve_issue(request: Request, issue_id: int, repo: Optional[str] = None):
    _slog.event("route.entry", project="dashboard", request_id=request.state.request_id, route="/api/issues/{issue_id}/approve", method="POST", issue_id=issue_id)
    try:
        github_client.approve_issue(issue_id, repo_name=repo)
        return {"ok": True}
    except subprocess.CalledProcessError as e:
        _slog.event("route.error", project="dashboard", request_id=request.state.request_id, route="/api/issues/{issue_id}/approve", level="error", issue_id=issue_id, error=str(e))
        raise _gh_error(e)


@router.post("/api/issues/{issue_id}/reject")
def reject_issue(request: Request, issue_id: int, body: RejectBody, repo: Optional[str] = None):
    _slog.event("route.entry", project="dashboard", request_id=request.state.request_id, route="/api/issues/{issue_id}/reject", method="POST", issue_id=issue_id)
    try:
        github_client.reject_issue(issue_id, body.reason, repo_name=repo)
        return {"ok": True}
    except subprocess.CalledProcessError as e:
        _slog.event("route.error", project="dashboard", request_id=request.state.request_id, route="/api/issues/{issue_id}/reject", level="error", issue_id=issue_id, error=str(e))
        raise _gh_error(e)


@router.post("/api/issues/{issue_id}/close")
def close_issue_endpoint(request: Request, issue_id: int, repo: Optional[str] = None):
    _slog.event("route.entry", project="dashboard", request_id=request.state.request_id, route="/api/issues/{issue_id}/close", method="POST", issue_id=issue_id)
    try:
        github_client.close_issue(issue_id, repo_name=repo)
        return {"ok": True}
    except subprocess.CalledProcessError as e:
        _slog.event("route.error", project="dashboard", request_id=request.state.request_id, route="/api/issues/{issue_id}/close", level="error", issue_id=issue_id, error=str(e))
        raise _gh_error(e)


@router.get("/api/issues/{issue_id}/test-report")
def get_test_report(issue_id: int, repo: Optional[str] = None):
    try:
        return github_client.get_test_report(issue_id, repo_name=repo)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))


@router.post("/api/issues/{issue_id}/sprint-label")
async def add_sprint_label(issue_id: int, body: SprintLabelBody):
    """Assign a sprint label to an issue (replaces any existing sprint-N labels).

    Accepts either:
    - sprint_label: "sprint-N" string (or null/empty to remove sprint label)
    - sprint: int (legacy; converted to "sprint-N")
    """
    srv = _server()
    action_id = str(uuid.uuid4())

    # Capture from_sprint before change
    try:
        _issue_data = github_client.get_issue(issue_id, repo_name=body.project or None)
        _cur_labels = [lbl["name"] for lbl in _issue_data.get("labels", [])]
        _from_sprint = next(
            (lbl for lbl in _cur_labels if srv._SPRINT_LABEL_RE_ALL.match(lbl)), None
        ) or "backlog"
    except Exception:
        _from_sprint = None

    # Resolve the sprint label from whichever field was provided
    if body.sprint_label is not None:
        raw = body.sprint_label.strip()
        if raw == "" or raw == "backlog":
            label_to_assign: str | None = None
        elif srv._SPRINT_LABEL_RE.match(raw):
            label_to_assign = raw
        else:
            raise HTTPException(400, detail=f"Invalid sprint_label: {raw!r}")
    elif body.sprint is not None:
        label_to_assign = f"sprint-{body.sprint}"
    else:
        raise HTTPException(400, detail="Provide sprint_label or sprint")

    repo = body.project or None
    try:
        github_client.assign_sprint_by_label(issue_id, label_to_assign, repo_name=repo)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))

    _to_sprint = label_to_assign or "backlog"
    srv._emit_dashboard_event(
        project=body.project or "dashboard",
        type="ticket_moved",
        target=f"#{issue_id}",
        detail={"from_sprint": _from_sprint, "to_sprint": _to_sprint},
        action_id=action_id,
    )
    if label_to_assign and label_to_assign != _from_sprint:
        srv._emit_dashboard_event(
            project=body.project or "dashboard",
            type="label_added",
            target=f"#{issue_id}",
            detail={"label": label_to_assign},
            action_id=action_id,
        )
    if _from_sprint and _from_sprint != "backlog" and _from_sprint != label_to_assign:
        srv._emit_dashboard_event(
            project=body.project or "dashboard",
            type="label_removed",
            target=f"#{issue_id}",
            detail={"label": _from_sprint},
            action_id=action_id,
        )
    invalidate_board(github_client.get_repo_for_operation(body.project))
    return {"ok": True}
