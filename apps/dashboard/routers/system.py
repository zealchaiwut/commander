"""System endpoints (extracted from server.py, issue #794).

Second strangler-fig wave: the movable system-metadata surfaces. Service logic
lives in the sibling ``system_service`` module.

Out of this wave (pinned to server.py by pre-existing tests the AC forbids
modifying): ``/api/health`` (AST-pinned by test_418) and ``/api/environment``
(string-pinned by test_prd_uat_split__8).
"""
from fastapi import APIRouter

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
