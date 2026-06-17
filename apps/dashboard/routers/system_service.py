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
    """Return live GitHub CLI auth preflight (re-checked each request)."""
    srv = _server()
    srv._check_gh_auth()
    return JSONResponse(
        content=srv._GH_AUTH_STATUS,
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


def start_gh_auth_login() -> JSONResponse:
    from routers import gh_auth_service as gas

    return JSONResponse(content=gas.start_login())


def get_gh_auth_login_status() -> JSONResponse:
    from routers import gh_auth_service as gas

    body = gas.get_login_status()
    if body.get("done") and body.get("ok"):
        gas.refresh_server_gh_auth()
    return JSONResponse(
        content=body,
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


def send_gh_auth_input(payload: dict) -> JSONResponse:
    from routers import gh_auth_service as gas

    text = payload.get("text", "\n")
    if text is None:
        text = "\n"
    return JSONResponse(content=gas.send_input(str(text)))


def cancel_gh_auth_login() -> JSONResponse:
    from routers import gh_auth_service as gas

    return JSONResponse(content=gas.cancel_login())


def gh_auth_login_with_token(payload: dict) -> JSONResponse:
    from routers import gh_auth_service as gas

    body = gas.login_with_token(payload.get("token", ""))
    if body.get("ok"):
        gas.refresh_server_gh_auth()
    return JSONResponse(content=body)


def get_diagnostics_page():
    """Serve the system diagnostics page (issue #230)."""
    srv = _server()
    return srv._serve_html(srv.STATIC_DIR / "diagnostics.html")
