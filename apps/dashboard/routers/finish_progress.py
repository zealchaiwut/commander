"""Finish-sprint progress streaming router (issue #929).

Endpoints:
  POST /api/projects/{owner}/{repo}/sprints/{label}/finish-bg
       Start a background finish-sprint task; returns { started, job_key }.
       If a job is already running for the same sprint, returns it without
       starting a duplicate.

  GET  /api/projects/{owner}/{repo}/sprints/{label}/finish-stream
       SSE stream of ProgressActivity snapshots for the active finish job.
       Sends the current snapshot immediately on connect (reconnect support — AC6).
       Closing the connection does NOT cancel the background task (AC5).
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from . import finish_progress_service as _svc
from . import sprint_finish as _fs

router = APIRouter(tags=["finish-progress"])

from sprint_label_re import SPRINT_LABEL_RE

_SPRINT_LABEL_RE = SPRINT_LABEL_RE


class FinishBgBody(BaseModel):
    confirmed: bool
    move_non_uat_to: str = ""
    selected_ticket_numbers: list[int] = []
    selected_tickets: list[dict] = []
    merge_pr: bool = False
    sprint_pr_url: Optional[str] = None
    # issue #1696: soft rework guard — set true only after the confirmation
    # modal shows the rework ticket list and the human explicitly overrides.
    confirm_rework: bool = False


@router.post("/api/projects/{owner}/{repo_name}/sprints/{label}/finish-bg")
async def start_finish_sprint_bg(
    owner: str,
    repo_name: str,
    label: str,
    body: FinishBgBody,
    background_tasks: BackgroundTasks,
) -> dict:
    """Start finish-sprint as a background task (AC1).

    Returns { started, job_key } immediately so the client can subscribe to
    /finish-stream without waiting for the operation to complete.
    """
    if not body.confirmed:
        raise HTTPException(400, detail="Request must have confirmed=true")
    if not _SPRINT_LABEL_RE.match(label):
        raise HTTPException(400, detail=f"Invalid sprint label: {label!r}")

    repo = f"{owner}/{repo_name}"

    # Soft rework guard (issue #1696): this endpoint — not the sibling
    # POST /finish in sprint_finish.py — is what the dashboard UI actually
    # calls. It closed whatever tickets the client sent with no server-side
    # check that they'd reached UAT. Recompute from GitHub (not the client's
    # selected_tickets payload, which carries no label data) so the guard
    # can't be bypassed by an unmodified client. Nothing is merged or closed
    # before this check.
    try:
        srv = _svc._server()
        sprint_issues = _fs._finish_sprint_issues(srv, repo, label)
        rework_tickets = _fs._finish_rework_tickets(srv, sprint_issues)
    except Exception:
        rework_tickets = []  # best-effort: a lookup failure must not block finish
    if rework_tickets and not body.confirm_rework:
        raise HTTPException(
            409,
            detail={
                "code": "rework_tickets_present",
                "message": (
                    f"{len(rework_tickets)} ticket(s) have not reached UAT and "
                    f"will be closed unfinished. Confirm to proceed anyway."
                ),
                "rework_tickets": rework_tickets,
            },
        )

    key = _svc.job_key(owner, repo_name, label)
    if _svc.is_running(key):
        return {"started": False, "job_key": key, "already_running": True}

    background_tasks.add_task(
        _svc.run_finish_sprint,
        key,
        f"{owner}/{repo_name}",
        label,
        body.selected_ticket_numbers,
        body.selected_tickets,
        body.merge_pr,
        body.sprint_pr_url,
        body.move_non_uat_to,
    )
    return {"started": True, "job_key": key}


@router.get("/api/projects/{owner}/{repo_name}/sprints/{label}/finish-stream")
async def finish_sprint_stream(owner: str, repo_name: str, label: str):
    """SSE stream of ProgressActivity snapshots for the finish-sprint job.

    Sends the current snapshot immediately on connect so clients reconnecting
    mid-operation see current progress (AC6). The subscriber queue is cleaned up
    in a finally block so the background task continues after disconnects (AC5).
    """
    key = _svc.job_key(owner, repo_name, label)

    async def event_generator():
        snapshot = _svc.get_snapshot(key)
        if snapshot:
            yield {"data": json.dumps(snapshot)}
            if snapshot.get("status") in ("done", "error"):
                return

        q = _svc.subscribe(key)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=30.0)
                    yield {"data": json.dumps(event)}
                    if event.get("status") in ("done", "error"):
                        break
                except asyncio.TimeoutError:
                    yield {"data": json.dumps({"ping": True})}
        finally:
            _svc.unsubscribe(key, q)

    return EventSourceResponse(event_generator())
