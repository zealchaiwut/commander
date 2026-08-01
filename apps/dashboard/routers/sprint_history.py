"""Sprint-history endpoint (issue #805).

A local-only, GitHub-free feed of terminal sprint records for the ledger UI.
The route is thin; all assembly lives in ``sprint_history_service``. New
endpoints belong here in ``routers/``, never in ``server.py`` (COMMANDER_GATE_MONOLITH).
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from . import sprint_history_service
from . import sprint_reconcile_service
from . import stale_branches_service
from . import run_stats_service
from .board_cache import invalidate_board

router = APIRouter(tags=["sprint-history"])


class SprintHistoryIssue(BaseModel):
    ticket_id: int | None = None
    state: str
    time_spent: int | None = None
    pr_number: int | None = None
    failure_reason: str | None = None
    agent_status: str | None = None


class SprintHistoryFailedTicket(BaseModel):
    ticket_id: int | None = None
    failure_reason: str | None = None


class SprintHistoryPostSprintAgent(BaseModel):
    status: str | None = None
    files_touched: list[str] = []
    commit_sha: str | None = None
    comment_url: str | None = None
    blockers: int = 0
    suggestions: int = 0
    nits: int = 0
    follow_up_tickets: list[int] = []


class SprintHistoryPostSprint(BaseModel):
    note: str = "Agents ran after ticket work finished"
    documenter: SprintHistoryPostSprintAgent | None = None
    reviewer: SprintHistoryPostSprintAgent | None = None


class SprintHistoryItem(BaseModel):
    label: str | None = None
    project: str = ""
    lifecycle_state: str
    end_reason: str | None = None
    duration: int | None = None
    tokens: int | None = None
    estimate_accuracy: float | None = None
    pr_number: int | None = None
    summary_path: str | None = None
    summary_issue_url: str | None = None
    summary_issue_num: int | None = None
    failure_reason: str | None = None
    has_rerun_child: bool = False
    partial_children: list[str] = []
    # Post-sprint reconciliation result (issue #856): {all_clear, checks[], ...}
    # or None for sprints that closed before this feature was deployed.
    reconciliation: dict | None = None
    post_sprint: SprintHistoryPostSprint | None = None
    issues: list[SprintHistoryIssue] = []
    failed_tickets: list[SprintHistoryFailedTicket] = []
    # Inline run-stats block (issue #1639): eliminates per-card /run-stats
    # round-trips. Same fields as GET /api/sprints/{label}/run-stats.
    run_stats: dict | None = None


class SprintHistoryResponse(BaseModel):
    sprints: list[SprintHistoryItem]
    offset: int
    limit: int
    total: int


@router.get("/api/sprints/history", response_model=SprintHistoryResponse)
def get_sprint_history(
    background_tasks: BackgroundTasks,
    offset: int = 0,
    limit: int = 20,
    project: str | None = None,
    active_only: bool = False,
):
    """Return paginated, enriched sprint-history rows. Makes no GitHub calls.

    ``active_only`` returns actionable sprints + the few most-recent closed ones
    (the History pane's default fast view).
    """
    result = sprint_history_service.get_sprint_history(
        offset=offset,
        limit=limit,
        project=project,
        active_only=active_only,
    )
    if project:

        async def _broadcast(data: dict):
            import server as srv  # noqa: PLC0415

            await srv.broadcast(data)

        background_tasks.add_task(
            sprint_reconcile_service.reconcile_project_background,
            project,
            _broadcast,
        )
    return result


# ── Per-sprint reconcile against GitHub (zombie repair) ──────────────────────
# On-demand single-sprint version of the background reconcile sweep: re-check one
# sprint against GitHub truth (via the zero-quota mirror) and, on apply, correct
# its DB lifecycle + local state. Writes never touch GitHub. Lives on the History
# router (already mounted) per COMMANDER_GATE_MONOLITH.

from sprint_label_re import SPRINT_LABEL_RE  # noqa: E402

_SPRINT_LABEL_RE = SPRINT_LABEL_RE


@router.get("/api/sprints/{label}/reconcile-preview")
def get_sprint_reconcile_preview(label: str, project: str):
    """Dry-run: return GitHub-vs-DB diff + post-sprint checks for one sprint. No writes."""
    from fastapi import HTTPException  # noqa: PLC0415

    if not _SPRINT_LABEL_RE.match(label):
        raise HTTPException(400, detail=f"Invalid sprint label: {label!r}")
    return sprint_reconcile_service.reconcile_preview(label, project)


class ReconcileSprintBody(BaseModel):
    project: str
    confirmed: bool = True


@router.post("/api/sprints/{label}/reconcile")
async def post_sprint_reconcile(label: str, body: ReconcileSprintBody):
    """Apply reconcile for one sprint: correct DB lifecycle + local state from GitHub truth."""
    from fastapi import HTTPException  # noqa: PLC0415

    if not _SPRINT_LABEL_RE.match(label):
        raise HTTPException(400, detail=f"Invalid sprint label: {label!r}")
    if not body.confirmed:
        raise HTTPException(400, detail="Request must have confirmed=true")
    result = sprint_reconcile_service.reconcile_apply(label, body.project)
    if result.get("updated"):
        try:
            import server as srv  # noqa: PLC0415

            await srv.broadcast(
                {
                    "type": "update",
                    "event": {
                        "event_type": "sprint_reconciled",
                        "project": body.project,
                        "labels": [label],
                    },
                }
            )
        except Exception:
            pass
    invalidate_board(body.project)
    return result


# ── Split an XL ticket into smaller tickets (BA agent) ───────────────────────


@router.post("/api/sprints/{label}/split-xl/{issue}/preview")
async def split_xl_preview(label: str, issue: int, project: str):
    """Run the BA split agent on an XL ticket; return proposed children. No writes."""
    from fastapi import HTTPException  # noqa: PLC0415

    if not _SPRINT_LABEL_RE.match(label):
        raise HTTPException(400, detail=f"Invalid sprint label: {label!r}")
    from . import split_xl_service  # noqa: PLC0415

    return await split_xl_service.split_preview(project, label, issue)


class SplitXlApplyBody(BaseModel):
    project: str
    children: list[dict]


@router.post("/api/sprints/{label}/split-xl/{issue}/apply")
async def split_xl_apply(label: str, issue: int, body: SplitXlApplyBody):
    """Create the child tickets in the sprint + close the XL as not-planned."""
    from fastapi import HTTPException  # noqa: PLC0415

    if not _SPRINT_LABEL_RE.match(label):
        raise HTTPException(400, detail=f"Invalid sprint label: {label!r}")
    from . import split_xl_service  # noqa: PLC0415

    result = split_xl_service.split_apply(body.project, label, issue, body.children)
    if not result.get("ok"):
        # Surface partial-failure recovery info (created child numbers +
        # compensation outcome) as a structured 500 detail so the UI can tell the
        # user exactly what was created and whether manual cleanup is needed (#1453).
        raise HTTPException(
            500,
            detail={
                "error": result.get("error"),
                "partial": result.get("partial", False),
                "created": result.get("created_numbers", []),
                "compensation": result.get("compensation"),
            },
        )
    invalidate_board(body.project)
    try:
        import server as srv  # noqa: PLC0415

        await srv.broadcast(
            {
                "type": "update",
                "event": {
                    "event_type": "sprint_split",
                    "project": body.project,
                    "label": label,
                },
            }
        )
    except Exception:
        pass
    return result


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


# ── Run-stats block for History cards (issue #810) ───────────────────────────
# Mounted on the already-wired History router so no route lands in server.py
# (COMMANDER_GATE_MONOLITH). Aggregates the sprint's agent_runs rows into the
# stat-chips / split-bar / gantt payload; logic lives in run_stats_service.
# A loose dict response is used deliberately: the gantt segment shape is nested
# and frontend-driven, and the service already guarantees a stable contract.


@router.get("/api/sprints/{label}/run-stats")
def get_run_stats(label: str, project: str | None = None):
    """Per-sprint agent_runs aggregation for the expanded History run-stats block."""
    return run_stats_service.sprint_run_stats(label, project=project)


class ClearStaleLabelsBody(BaseModel):
    project: str
    tickets: list[dict] = []


class ClearStaleLabelsResponse(BaseModel):
    cleared: list[int] = []
    errors: list[str] = []


@router.post(
    "/api/sprints/{label}/clear-stale-labels", response_model=ClearStaleLabelsResponse
)
def clear_stale_labels(label: str, body: ClearStaleLabelsBody):
    """Remove stale sprint status labels flagged by post-sprint reconciliation."""
    import github_client as gh  # noqa: PLC0415

    cleared: list[int] = []
    errors: list[str] = []
    for ticket in body.tickets or []:
        num = ticket.get("issue") or ticket.get("issue_num") or ticket.get("ticket_id")
        labels = ticket.get("labels") or []
        if num is None or not labels:
            continue
        try:
            gh.update_labels(
                int(num), add=[], remove=list(labels), repo_name=body.project
            )
            cleared.append(int(num))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"#{num}: {exc}")

    if cleared:
        gh.invalidate("open_issues_body:")
        gh.invalidate("open_issues:")
        gh.invalidate("issues:")
        invalidate_board(body.project)
    return ClearStaleLabelsResponse(cleared=cleared, errors=errors)
