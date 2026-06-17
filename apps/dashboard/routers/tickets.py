"""Ticket endpoints (extracted from server.py, issue #795).

The movable ticket surfaces of the API: the UAT approve action and the
self-contained bulk-job stop/discard controls. Service logic lives in the
sibling ``tickets_service`` module.

Out of this wave (pinned to server.py by a pre-existing test): bulk_post_selected
(imported by test_331); the larger bulk-create / draft handlers are deferred to
a later wave.
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import backlog_cleanup_service
from . import tickets_service

router = APIRouter(tags=["tickets"])


class BacklogCleanupBody(BaseModel):
    confirmed: bool = False
    issue_numbers: list[int] = []


@router.post("/api/projects/{owner}/{repo_name}/backlog/cleanup-preview")
def backlog_cleanup_preview(owner: str, repo_name: str):
    """Scan open backlog for test tickets and stale follow-ups to close."""
    repo = f"{owner}/{repo_name}"
    try:
        return backlog_cleanup_service.scan_backlog(repo)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/projects/{owner}/{repo_name}/backlog/cleanup")
def backlog_cleanup(owner: str, repo_name: str, body: BacklogCleanupBody):
    """Close selected backlog cleanup candidates on GitHub."""
    if not body.confirmed:
        raise HTTPException(status_code=400, detail="Request must have confirmed=true")
    repo = f"{owner}/{repo_name}"
    numbers = body.issue_numbers or []
    if not numbers:
        preview = backlog_cleanup_service.scan_backlog(repo)
        numbers = [c["number"] for c in preview.get("candidates") or []]
    try:
        return backlog_cleanup_service.apply_backlog_cleanup(repo, numbers)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/tickets/{issue_id}/approve")
async def approve_ticket(request: Request, issue_id: int, repo: Optional[str] = None):
    """Close a UAT-labelled ticket on GitHub and remove the UAT label."""
    return await tickets_service.approve_ticket(issue_id, repo, request.state.request_id)


@router.post("/api/tickets/bulk/{job_id}/stop")
async def bulk_stop_job(job_id: str):
    """Graceful stop: finish in-flight BA calls, mark remaining pending as skipped."""
    return tickets_service.bulk_stop_job(job_id)


@router.delete("/api/tickets/bulk/{job_id}")
async def bulk_delete_job(job_id: str):
    """Discard a bulk job entirely — used by the "Start new batch" / clear action."""
    return tickets_service.bulk_delete_job(job_id)
