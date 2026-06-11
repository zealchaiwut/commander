"""Sprint-history endpoint (issue #805).

A local-only, GitHub-free feed of terminal sprint records for the ledger UI.
The route is thin; all assembly lives in ``sprint_history_service``. New
endpoints belong here in ``routers/``, never in ``server.py`` (COMMANDER_GATE_MONOLITH).
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from . import sprint_history_service
from . import stale_branches_service

router = APIRouter(tags=["sprint-history"])


class SprintHistoryIssue(BaseModel):
    ticket_id: int | None = None
    state: str
    time_spent: int | None = None
    pr_number: int | None = None


class SprintHistoryItem(BaseModel):
    label: str | None = None
    project: str = ""
    lifecycle_state: str
    duration: int | None = None
    tokens: int | None = None
    estimate_accuracy: float | None = None
    pr_number: int | None = None
    summary_path: str | None = None
    issues: list[SprintHistoryIssue] = []


class SprintHistoryResponse(BaseModel):
    sprints: list[SprintHistoryItem]
    offset: int
    limit: int
    total: int


@router.get("/api/sprints/history", response_model=SprintHistoryResponse)
def get_sprint_history(offset: int = 0, limit: int = 20):
    """Return paginated, enriched sprint-history rows. Makes no GitHub calls."""
    return sprint_history_service.get_sprint_history(offset=offset, limit=limit)


# ── Stale-branch scan + cleanup (issue #808) ─────────────────────────────────
# These live on the History router (already mounted) rather than a new router so
# no route is added to server.py (COMMANDER_GATE_MONOLITH). Logic lives in the
# sibling stale_branches_service module; handlers stay thin.


class StaleBranch(BaseModel):
    branch: str
    issue: int | None = None
    sprint: str | None = None
    merged: bool = False


class StaleSprintGroup(BaseModel):
    sprint: str
    count: int = 0
    branches: list[str] = []
    merged: list[str] = []
    unmerged: list[str] = []


class ScanStaleResponse(BaseModel):
    repo: str
    target_branch: str
    branches: list[StaleBranch] = []
    by_sprint: dict[str, StaleSprintGroup] = {}


class CleanupStaleRequest(BaseModel):
    repo: str
    branches: list[str] = []
    target: str | None = None
    confirm: bool = False


class CleanupStaleResponse(BaseModel):
    dry_run: bool
    target_branch: str
    to_delete: list[str] = []
    deleted: list[str] = []
    skipped_unmerged: list[str] = []
    failed: list[str] = []


@router.get("/scan-stale-branches", response_model=ScanStaleResponse)
def scan_stale_branches(repo: str, target: str | None = None):
    """List feature/<N>-* remote branches, map each to a sprint, flag merged."""
    return stale_branches_service.scan_stale_branches(repo, target=target)


@router.post("/cleanup-stale-branches", response_model=CleanupStaleResponse)
def cleanup_stale_branches(body: CleanupStaleRequest):
    """Dry-run, then (on confirm) delete only merged branches; never unmerged."""
    return stale_branches_service.cleanup_stale_branches(
        body.repo, body.branches, target=body.target, confirm=body.confirm
    )
