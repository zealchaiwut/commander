"""Ticket endpoints (extracted from server.py, issue #795).

The movable ticket surfaces of the API: the UAT approve action and the
self-contained bulk-job stop/discard controls. Service logic lives in the
sibling ``tickets_service`` module.

Out of this wave (pinned to server.py by a pre-existing test): bulk_post_selected
(imported by test_331); the larger bulk-create / draft handlers are deferred to
a later wave.
"""
from typing import Optional

from fastapi import APIRouter, Request

from . import tickets_service

router = APIRouter(tags=["tickets"])


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
