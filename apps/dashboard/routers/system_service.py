"""Service logic for the system router (extracted from server.py, issue #794).

These endpoints surface process/build metadata that the monolith computes at
startup (`_GIT_SHA`, `_GIT_BRANCH`, `_BUILD_TIMESTAMP`, `_GH_AUTH_STATUS`).
That state lives in and is mutated by ``server.py``, so the service reads it
back through a deferred import at request time — keeping a single source of
truth and avoiding the import-time circular dependency (``server.py`` imports
this package while mounting routers).
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path

from fastapi.responses import JSONResponse

# server.py is a top-level module on the dashboard path; make sure it resolves.
_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
if str(_DASHBOARD_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_DASHBOARD_ROOT))


def _server():
    """Deferred import of the monolith — safe at request time."""
    import server  # noqa: PLC0415 — intentional late import (see module docstring)
    return server


def get_version() -> JSONResponse:
    """Return build metadata for the running process (issue #421).

    Response shape:
    {
      "git_sha": "<full-commit-hash>",
      "branch": "main",
      "build_timestamp": "2026-05-30T12:00:00+00:00"
    }
    """
    srv = _server()
    return JSONResponse(
        content={
            "git_sha": srv._GIT_SHA,
            "branch": srv._GIT_BRANCH,
            "build_timestamp": srv._BUILD_TIMESTAMP,
        },
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


def get_gh_auth_status() -> JSONResponse:
    """Return the GitHub CLI auth preflight result from startup (issue #424)."""
    srv = _server()
    return JSONResponse(
        content=srv._GH_AUTH_STATUS,
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


def get_diagnostics_page():
    """Serve the system diagnostics page (issue #230)."""
    from routers.pages import _serve_html, _STATIC_DIR  # noqa: PLC0415
    return _serve_html(_STATIC_DIR / "diagnostics.html")


# ── Issue #1247: health, environment, repo/config, github/labels ──────────────

async def check_health() -> "JSONResponse":
    """Run the full health check and return a cached JSONResponse.

    Business logic (collectors, cache, helpers) lives in server.py; this
    function delegates entirely via the deferred import so the monolith
    remains the single source of truth for those globals.
    """
    import asyncio
    import time
    from datetime import datetime, timezone

    srv = _server()
    now = time.monotonic()

    if srv._health_cache is not None:
        ts, cached = srv._health_cache
        if now - ts < srv._HEALTH_CACHE_TTL:
            return JSONResponse(content=cached, status_code=200)

    checked_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    loop = asyncio.get_event_loop()

    try:
        results = await asyncio.wait_for(
            asyncio.gather(
                loop.run_in_executor(None, srv._health_collect_gh_auth_scopes),
                loop.run_in_executor(None, srv._health_collect_disk),
                loop.run_in_executor(None, srv._health_collect_sprints),
                loop.run_in_executor(None, srv._health_collect_orphan_pids),
                loop.run_in_executor(None, srv._health_collect_recent_dispatches),
                return_exceptions=True,
            ),
            timeout=5.0,
        )
    except asyncio.TimeoutError:
        results = [None, None, None, None, None]

    def _safe(r):
        return None if isinstance(r, Exception) else r

    gh_auth = _safe(results[0])
    disk = _safe(results[1])
    sprints = _safe(results[2])
    orphan_pids = _safe(results[3])
    recent_dispatches = _safe(results[4])

    status = srv._compute_health_status(gh_auth, disk, orphan_pids, recent_dispatches)

    import time as _time
    response = {
        "status": status,
        "uptime_seconds": int(_time.monotonic() - srv._start_time),
        "gh_auth_scopes": gh_auth,
        "disk": disk,
        "sprints": sprints,
        "orphan_pids": orphan_pids,
        "orphans_removed": srv._orphans_removed_total,
        "recent_dispatches": recent_dispatches,
        "checked_at": checked_at,
    }
    srv._health_cache = (now, response)
    return JSONResponse(content=response, status_code=200)


def get_environment() -> dict:
    """Return the current runtime environment (prd or uat)."""
    srv = _server()
    return {"environment": srv.ENVIRONMENT}


def get_repo_config():
    """Return repo configuration from github_client."""
    import github_client
    from fastapi import HTTPException
    try:
        return github_client.repo_config()
    except ValueError as e:
        raise HTTPException(400, detail=str(e))


def list_github_labels(repo: "str | None" = None):
    """Return all GitHub labels for the repo (cached 30 s)."""
    import github_client
    from fastapi import HTTPException
    srv = _server()
    try:
        return github_client.list_labels(repo_name=repo)
    except Exception as e:
        import subprocess
        if isinstance(e, subprocess.CalledProcessError):
            raise srv._gh_error(e)
        raise HTTPException(400, detail=str(e))


def create_github_label(name: str, color: str, description: str, repo: "str | None"):
    """Create a new GitHub label and return the updated label list."""
    import github_client
    from fastapi import HTTPException
    srv = _server()
    name = name.strip()
    if not name:
        raise HTTPException(400, detail="Label name is required.")
    try:
        github_client.create_label(name, color, description=description, repo_name=repo)
        return github_client.list_labels(repo_name=repo)
    except Exception as e:
        import subprocess
        if isinstance(e, subprocess.CalledProcessError):
            raise srv._gh_error(e)
        raise HTTPException(400, detail=str(e))
