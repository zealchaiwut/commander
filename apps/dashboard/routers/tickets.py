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


@router.post("/api/projects/{owner}/{repo_name}/backlog/triage-apply")
def backlog_triage_apply(owner: str, repo_name: str, body: BacklogCleanupBody):
    """Close the operator-selected triaged backlog tickets.

    Unlike backlog/cleanup (which only closes numbers that the regex scan
    independently flags), this trusts the human's triage selection and closes the
    given numbers directly — but stays safe by closing ONLY open backlog issues
    (no sprint-labelled ones). Returns {closed, errors}.
    """
    if not body.confirmed:
        raise HTTPException(status_code=400, detail="Request must have confirmed=true")
    repo = f"{owner}/{repo_name}"
    nums = [int(n) for n in (body.issue_numbers or [])]
    if not nums:
        return {"closed": [], "errors": []}
    try:
        import github_client as gc  # noqa: PLC0415
        backlog_nums = {
            i.get("number") for i in gc.list_open_issues_with_body(repo_name=repo, limit=300)
            if backlog_cleanup_service.is_backlog_issue(i)
        }
        closed: list[int] = []
        errors: list[str] = []
        for n in nums:
            if n not in backlog_nums:
                errors.append(f"#{n}: not an open backlog issue — skipped")
                continue
            try:
                gc.close_issue(n, repo_name=repo, reason="not planned")
                closed.append(n)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"#{n}: {exc}")
        if closed:
            for k in ("open_issues", "open_issues_body", "all_open_issues"):
                gc.invalidate(f"{k}:{repo}")
        return {"closed": closed, "errors": errors}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/projects/{owner}/{repo_name}/backlog/cleanup-preview")
def backlog_cleanup_preview(owner: str, repo_name: str):
    """Scan open backlog for test tickets and stale follow-ups to close."""
    repo = f"{owner}/{repo_name}"
    try:
        return backlog_cleanup_service.scan_backlog(repo)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/projects/{owner}/{repo_name}/backlog/triage")
def backlog_triage(owner: str, repo_name: str):
    """LLM triage of open `[follow-up]` backlog tickets (dry-run).

    Returns a per-ticket assessment (priority, semantic duplicate_of,
    recommend_close, reason) for the human to review as a checklist. Closes
    nothing — the confirm step reuses POST .../backlog/cleanup with the chosen
    issue_numbers.
    """
    repo = f"{owner}/{repo_name}"
    try:
        import github_client as gc  # noqa: PLC0415  (sibling on path)
        from services.sprint_manager import backlog_triage as bt  # noqa: PLC0415
        issues = gc.list_open_issues_with_body(repo_name=repo, limit=200)
        followups = [
            i for i in issues
            if backlog_cleanup_service.is_backlog_issue(i)
            and backlog_cleanup_service.is_follow_up_title(i.get("title") or "")
        ]
        tickets = [
            {"number": i.get("number"), "title": i.get("title"), "body": i.get("body")}
            for i in followups
        ]
        result, err = bt.triage_backlog(tickets, project=repo)
        if err and err not in (None, "no_tickets"):
            raise HTTPException(status_code=502, detail=f"Triage agent error: {err}")
        return {"tickets": (result or {}).get("tickets", []), "error": err}
    except HTTPException:
        raise
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
