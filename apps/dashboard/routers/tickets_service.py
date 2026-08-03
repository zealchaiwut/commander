"""Service logic for the tickets router (extracted from server.py, issue #795).

The movable ticket surfaces of the API. Bulk-job state (the ``_bulk_jobs`` /
``_bulk_job_queues`` in-memory maps and their on-disk mirror) still lives in
``server.py``, so the service reaches it through a deferred import at request
time — one source of truth, no circular import at mount time.

Out of this wave (pinned to server.py by a pre-existing test): bulk_post_selected
(imported by test_331). The larger bulk-create / draft handlers are deferred to
a later wave.
"""
from __future__ import annotations

import subprocess
import sys as _sys
from pathlib import Path
from typing import Optional

from fastapi import HTTPException

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
if str(_DASHBOARD_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_DASHBOARD_ROOT))


def _server():
    """Deferred import of the monolith — safe at request time."""
    import server  # noqa: PLC0415 — intentional late import (see module docstring)
    return server


async def approve_ticket(issue_id: int, repo: Optional[str], request_id):
    """Close a UAT-labelled ticket on GitHub and remove the UAT label."""
    srv = _server()
    srv._slog.event(
        "route.entry", project="dashboard", request_id=request_id,
        route="/api/tickets/{issue_id}/approve", method="POST", issue_id=issue_id,
    )
    try:
        srv.github_client.approve_issue(issue_id, repo_name=repo)
    except subprocess.CalledProcessError as e:
        srv._slog.event(
            "route.error", project="dashboard", request_id=request_id,
            route="/api/tickets/{issue_id}/approve", level="error",
            issue_id=issue_id, error=str(e),
        )
        raise srv._gh_error(e)
    from .board_cache import invalidate_board  # noqa: PLC0415
    invalidate_board(srv.github_client.get_repo_for_operation(repo))
    await srv.broadcast(
        {"type": "update", "event": {"event_type": "ticket_approved", "issue": issue_id}}
    )
    return {"ok": True}


def bulk_stop_job(job_id: str):
    """Graceful stop: finish in-flight BA calls, mark remaining pending as skipped."""
    srv = _server()
    job = srv._get_bulk_job(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")
    job["stop_requested"] = True
    srv._persist_bulk_job(job)
    return {"ok": True}


def bulk_delete_job(job_id: str):
    """Discard a bulk job entirely — used by the "Start new batch" / clear action.

    Signals any live worker to stop, drops the in-memory entry, and removes the
    persisted JSON so a wedged or finished job never reloads. Idempotent:
    returns ok even if the job is already gone.
    """
    srv = _server()
    job = srv._bulk_jobs.get(job_id)
    if job is not None:
        job["stop_requested"] = True
    srv._bulk_jobs.pop(job_id, None)
    srv._bulk_job_queues.pop(job_id, None)
    try:
        path = srv._bulk_jobs_dir() / f"{job_id}.json"
        if path.exists():
            path.unlink()
    except Exception as e:
        srv.logger.warning(
            "bulk_delete_job: could not remove %s.json: %s", job_id, str(e)[:200]
        )
    return {"ok": True}
