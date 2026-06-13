"""System endpoints (extracted from server.py, issue #794).

Second strangler-fig wave: the movable system-metadata surfaces. Service logic
lives in the sibling ``system_service`` module.

Out of this wave (pinned to server.py by pre-existing tests the AC forbids
modifying): ``/api/health`` (AST-pinned by test_418) and ``/api/environment``
(string-pinned by test_prd_uat_split__8).
"""
from fastapi import APIRouter

from . import milestone_progress_service
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
    """Return the GitHub CLI auth preflight result from startup (issue #424)."""
    return system_service.get_gh_auth_status()


# ── active-milestone progress (issue #880) ────────────────────────────────────
# Read-only home/header surfaces. Registered on this already-mounted router so no
# route is added to the server.py monolith (COMMANDER_GATE_MONOLITH, issue #761).
# Logic lives in the sibling ``milestone_progress_service`` module.


@router.get("/api/milestones/home")
def home_milestones():
    """Per-project active milestone progress, keyed by project slug.

    Shape: ``{"projects": {"<slug>": {milestone fields} | null}}``. The home page
    attaches a compact ``X done + Y UAT / Z total`` indicator to each card.
    """
    return {"projects": milestone_progress_service.home_milestones()}


@router.get("/api/milestones/project")
def project_milestone(repo: str):
    """Active milestone progress for one project (``?repo=owner/name``).

    Returns the milestone fields, or ``null`` when the project has no active
    milestone. Used by the project header / sprint nav.
    """
    return milestone_progress_service.active_milestone_progress(repo)
