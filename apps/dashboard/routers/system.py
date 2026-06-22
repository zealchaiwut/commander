"""System endpoints (extracted from server.py, issues #794 and #1247).

Third strangler-fig wave: health, environment, repo/config, and github/labels
moved here in issue #1247 to complete the system cluster extraction. Service
logic lives in the sibling ``system_service`` module.
"""
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel
from services.logging import log as _slog

from . import system_service

router = APIRouter(tags=["system"])


@router.get("/diagnostics")
async def diagnostics_page():
    """Serve the system diagnostics page (issue #230)."""
    return system_service.get_diagnostics_page()


@router.get("/api/version")
def get_version():
    """Return build metadata for the running process (issue #421)."""
    return system_service.get_version()


@router.get("/api/gh-auth-status")
def get_gh_auth_status():
    """Return the GitHub CLI auth preflight result (issue #424)."""
    return system_service.get_gh_auth_status()


# ── Issue #1247: health, environment, repo/config, github/labels ──────────────

@router.get("/api/health")
async def health_check(request: Request):
    """Structured operational health check (issue #474, extracted #1247)."""
    _slog.event("route.entry", project="dashboard", request_id=request.state.request_id, route="/api/health", method="GET")
    return await system_service.check_health()


@router.get("/api/environment")
def get_environment():
    """Return the current runtime environment (prd or uat)."""
    return system_service.get_environment()


@router.get("/api/repo/config")
def get_repo_config():
    """Return repo configuration."""
    return system_service.get_repo_config()


class CreateLabelBody(BaseModel):
    name: str
    color: str = "a2eeef"
    description: str = ""
    repo: Optional[str] = None


@router.get("/api/github/labels")
def get_github_labels(repo: Optional[str] = None):
    """Return all GitHub labels for the repo (cached 30 s)."""
    return system_service.list_github_labels(repo=repo)


@router.post("/api/github/labels")
def post_create_label(body: CreateLabelBody):
    """Create a new GitHub label in the repo; returns updated label list."""
    return system_service.create_github_label(
        name=body.name,
        color=body.color,
        description=body.description,
        repo=body.repo,
    )


@router.post("/api/gh-auth/login/start")
def start_gh_auth_login():
    return system_service.start_gh_auth_login()


@router.get("/api/gh-auth/login/status")
def get_gh_auth_login_status():
    return system_service.get_gh_auth_login_status()


@router.post("/api/gh-auth/login/input")
def send_gh_auth_input(payload: dict):
    return system_service.send_gh_auth_input(payload)


@router.post("/api/gh-auth/login/cancel")
def cancel_gh_auth_login():
    return system_service.cancel_gh_auth_login()


@router.post("/api/gh-auth/login/token")
def gh_auth_login_with_token(payload: dict):
    return system_service.gh_auth_login_with_token(payload)
