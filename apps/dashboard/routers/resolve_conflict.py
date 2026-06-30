"""API routes for AI-powered branch conflict resolution (issue #1619-followup).

POST /api/projects/{owner}/{repo}/resolve-branch-conflict
     Body: { head, base, sprint_label? }
     Returns { started, job_key }

GET  /api/projects/{owner}/{repo}/resolve-conflict-stream/{job_key}
     SSE stream of ProgressActivity-shaped snapshots.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from . import resolve_conflict_service as _svc

router = APIRouter(tags=["resolve-conflict"])

_BRANCH_RE = re.compile(r"^[\w.\-/]+$")


class ResolveConflictBody(BaseModel):
    head: str
    base: str
    sprint_label: Optional[str] = None


@router.post("/api/projects/{owner}/{repo_name}/resolve-branch-conflict")
async def start_resolve_conflict(
    owner: str,
    repo_name: str,
    body: ResolveConflictBody,
    background_tasks: BackgroundTasks,
) -> dict:
    if not _BRANCH_RE.match(body.head) or not _BRANCH_RE.match(body.base):
        raise HTTPException(400, detail="Invalid branch names")

    key = _svc.job_key(owner, repo_name, body.head)
    if _svc.is_running(key):
        return {"started": False, "job_key": key, "already_running": True}

    background_tasks.add_task(
        _svc.run_resolve_conflict,
        key,
        f"{owner}/{repo_name}",
        body.head,
        body.base,
    )
    return {"started": True, "job_key": key}


@router.get("/api/projects/{owner}/{repo_name}/resolve-conflict-stream/{job_key_path:path}")
async def resolve_conflict_stream(owner: str, repo_name: str, job_key_path: str):
    key = job_key_path

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
