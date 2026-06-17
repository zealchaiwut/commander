"""Bulk ticket job read routes extracted from server.py (issue #1264).

GET routes moved here:
  GET /api/tickets/bulk/{job_id}
  GET /api/tickets/bulk/{job_id}/stream

The _get_bulk_job loader is also defined here and re-imported into server.py
so all existing callers continue to work without modification.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import StreamingResponse

router = APIRouter(tags=["bulk_tickets"])


def _server():
    """Deferred import of the monolith — avoids circular import."""
    import server  # noqa: PLC0415
    return server


def _get_bulk_job(job_id: str) -> dict | None:
    """Return job from memory, or lazy-load from disk after restart.

    If found on disk and not in memory, the job is restored to _bulk_jobs so
    subsequent calls hit memory. Returns None if not found anywhere.
    """
    srv = _server()
    job = srv._bulk_jobs.get(job_id)
    if job is not None:
        return job
    # Disk fallback — covers server reload / restart scenarios
    try:
        jobs_dir = srv._bulk_jobs_dir()
        path = jobs_dir / f"{job_id}.json"
        if path.exists():
            job = json.loads(path.read_text(encoding="utf-8"))
            # A job that was 'running' when the server restarted can never
            # resume — its in-flight BA processes are gone. Mark it cancelled
            # so the client gets accurate state instead of a perpetual spinner.
            if job.get("status") == "running":
                srv._bulk_cancel_interrupted(job)
            srv._bulk_jobs[job_id] = job
            return job
    except Exception:
        pass
    return None


@router.get("/api/tickets/bulk/{job_id}")
async def bulk_get_job(job_id: str):
    """Return the current state of a bulk job."""
    srv = _server()
    job = _get_bulk_job(job_id)
    if not job:
        # Try to load from disk
        try:
            path = srv._bulk_jobs_dir() / f"{job_id}.json"
            if path.exists():
                job = json.loads(path.read_text())
                srv._bulk_jobs[job_id] = job
        except Exception:
            pass
    if not job:
        raise HTTPException(404, detail="Job not found")
    # Strip internal fields from response
    tickets = [
        {k: v for k, v in t.items() if not k.startswith("_")}
        for t in job["tickets"]
    ]
    return {
        "job_id": job["job_id"],
        "repo": job.get("repo", ""),
        "status": job["status"],
        "concurrency": job["concurrency"],
        "default_labels": job.get("default_labels", []),
        "tickets": tickets,
    }


@router.get("/api/tickets/bulk/{job_id}/stream")
async def bulk_job_stream(job_id: str, request: Request):
    """SSE stream of state-change events for a bulk job."""
    srv = _server()
    job = _get_bulk_job(job_id)
    if not job:
        # Rehydrate from disk if the job was persisted but evicted from memory
        # (e.g. server restart). Mirrors bulk_get_job so a reconnecting client
        # doesn't get a fatal 404 for a job that still exists on disk.
        try:
            path = srv._bulk_jobs_dir() / f"{job_id}.json"
            if path.exists():
                job = json.loads(path.read_text())
                srv._bulk_jobs[job_id] = job
        except Exception:
            pass
    if not job:
        raise HTTPException(404, detail="Job not found")

    queue: asyncio.Queue = asyncio.Queue(maxsize=200)

    # Register this subscriber
    if job_id not in srv._bulk_job_queues:
        srv._bulk_job_queues[job_id] = []
    srv._bulk_job_queues[job_id].append(queue)

    async def generator():
        try:
            # On connect: send full current state first
            state_snapshot = await bulk_get_job(job_id)
            yield f"event: snapshot\ndata: {json.dumps(state_snapshot)}\n\n"

            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"event: update\ndata: {data}\n\n"
                    # Stop streaming if job is done
                    parsed = json.loads(data)
                    if parsed.get("type") == "job_done":
                        break
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                except Exception:
                    break
        finally:
            qlist = srv._bulk_job_queues.get(job_id, [])
            if queue in qlist:
                qlist.remove(queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
