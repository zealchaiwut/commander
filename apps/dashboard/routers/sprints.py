"""Sprint endpoints (extracted from server.py, issue #795).

The movable sprint surfaces of the API. Service logic lives in the sibling
``sprints_service`` module; the request/response models for the two POST
bodies are declared here (they were single-use in the monolith and move with
the routes).

Out of this wave (pinned to server.py by pre-existing tests the AC forbids
modifying): the sprint *lifecycle* handlers — run_sprint_managed, rerun_sprint,
finish_sprint, set_sprint_status — plus get_sprint_management_issues,
get_sprint_estimate_vs_actual, get_sprint_estimate_summary and
get_sprint_branch_status.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import config

from . import brief
from . import sprints_service
from .board_cache import invalidate_board

router = APIRouter(tags=["sprints"])

# Brief assembly endpoints (issue #839) ride on this already-mounted router so
# no route lands in server.py (COMMANDER_GATE_MONOLITH, issue #761). The brief
# router declares full paths and carries no prefix, so include_router mounts
# them unchanged.
router.include_router(brief.router)

class SprintOrderBody(BaseModel):
    order: list[str]


class PlanNextSprintBody(BaseModel):
    project: str
    # When a pending-sign-off draft already exists, the client re-submits with
    # replace=true after confirming the prompt (issue #861, AC11).
    replace: bool = False


class SprintGoalBody(BaseModel):
    project: str
    sprint_label: str
    goal: str


@router.get("/api/sprints")
def get_sprints():
    return sprints_service.get_sprints()


@router.get("/api/sprints/goal")
def get_sprint_goal(project: str, sprint: str):
    """Return the persisted sprint goal for a project/sprint."""
    return sprints_service.get_sprint_goal(project, sprint)


@router.post("/api/sprints/goal")
def save_sprint_goal(body: SprintGoalBody):
    """Persist sprint goal to .commander/sprints/<label>-goal.txt."""
    result = sprints_service.save_sprint_goal(body.project, body.sprint_label, body.goal)
    invalidate_board(body.project)
    return result


@router.get("/api/sprints/order")
def get_sprint_order(project: str):
    """Return the persisted sprint display order for a project slug."""
    return sprints_service.get_sprint_order(project)


@router.post("/api/sprints/order")
def save_sprint_order(project: str, body: SprintOrderBody):
    """Persist sprint display order for a project slug."""
    result = sprints_service.save_sprint_order(project, body.order)
    invalidate_board(project)
    return result


class SprintRerunBody(BaseModel):
    """Body for POST /api/sprints/{label}/rerun (issue #2318)."""

    repo: str | None = None
    dry_run: bool = False


@router.post("/api/sprints/{sprint_label}/rerun")
def rerun_sprint(sprint_label: str, body: SprintRerunBody | None = None):
    """Reset every failed ticket in a sprint back to a dispatchable state.

    Restored per the operator decision on #2314. This is **not** the rerun that
    was deleted in #2250: it keeps the sprint's own label, imposes no ordering,
    and never writes sprint lifecycle state (see #2311 for why each of those
    matters). It also does not dispatch — resetting and running are separate
    calls so a reset can be inspected before anything executes.
    """
    import github_client
    from services.sprint_manager.ticket_retry import reset_sprint

    payload = body or SprintRerunBody()
    repo_root = _commander_repo_root()

    try:
        result = reset_sprint(
            sprint_label,
            github_client=github_client,
            repo=payload.repo,
            repo_root=repo_root,
            dry_run=payload.dry_run,
        )
    except Exception as exc:  # surfaced to the caller rather than a bare 500
        raise HTTPException(status_code=502, detail=f"rerun failed: {exc}") from exc

    if not payload.dry_run and result.reset:
        invalidate_board(payload.repo or "")

    return result.to_dict()


class SprintDispatchBody(BaseModel):
    """Body for POST /api/sprints/{label}/dispatch (issue #2315).

    ``tickets`` is executed in the order given. The runner never sorts or
    reorders it — ticket sequencing belongs to the operator (#2311).
    """

    tickets: list[int]
    repo: str | None = None
    cwd: str | None = None
    baseline_note: str | None = None


def _commander_repo_root():
    from pathlib import Path

    return Path(config.__file__).resolve().parent.parent.parent


@router.post("/api/sprints/{sprint_label}/dispatch")
def dispatch_sprint(sprint_label: str, body: SprintDispatchBody):
    """Start a dispatch run for a sprint and return its handle (issue #2315).

    The privileged agent spawn happens inside this service, which is the point
    of the #2314 decision: the caller makes an ordinary API call rather than
    elevating anything itself.

    Returns immediately; poll ``GET /api/sprints/dispatch/{run_id}`` for status.
    """
    from pathlib import Path

    from services.sprint_manager.dispatch_runner import (
        DispatchConfigError,
        load_project_config,
        start_run,
    )

    if not body.tickets:
        raise HTTPException(status_code=400, detail="tickets must not be empty")

    repo_root = _commander_repo_root()
    cwd = Path(body.cwd) if body.cwd else repo_root
    if not cwd.exists():
        raise HTTPException(status_code=400, detail=f"cwd does not exist: {cwd}")

    # Prompt templates, per-step worktrees and the model come from the target
    # project's .commander/sprint.yaml (issue #2325). A missing or incomplete
    # config is a 400 here rather than a run that starts and silently does
    # nothing — which is what happened before this was read.
    try:
        config = load_project_config(cwd)
    except DispatchConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    kwargs = {}
    if body.baseline_note:
        kwargs["baseline_note"] = body.baseline_note

    run = start_run(
        sprint_label,
        body.tickets,
        repo=body.repo or config.repo_name or None,
        repo_root=repo_root,
        cwd=cwd,
        config=config,
        **kwargs,
    )
    return run.to_dict()


@router.get("/api/sprints/dispatch/{run_id}")
def get_dispatch_run(run_id: str):
    """Live status of a dispatch run: current ticket, step, and outcomes."""
    from services.sprint_manager.dispatch_runner import load_run

    data = load_run(run_id, _commander_repo_root())
    if data is None:
        raise HTTPException(status_code=404, detail=f"unknown dispatch run: {run_id}")
    return data


@router.post("/api/sprints/dispatch/{run_id}/stop")
def stop_dispatch_run(run_id: str):
    """Ask a run to stop at the next step boundary.

    Not mid-step: a coder step that has already pushed and labelled SIT is a
    consistent state, and interrupting inside one could leave a ticket
    half-labelled.
    """
    from services.sprint_manager.dispatch_runner import request_stop

    if not request_stop(run_id, _commander_repo_root()):
        raise HTTPException(status_code=404, detail=f"unknown dispatch run: {run_id}")
    return {"run_id": run_id, "stop_requested": True}


@router.post("/api/sprints/plan-next")
def plan_next_sprint(body: PlanNextSprintBody):
    """Draft the next sprint from the active milestone's backlog (issue #861).

    Returns ``{status, created, ...}``. ``status`` is one of ``ok``,
    ``no_milestone``, ``empty`` (zero capacity / no eligible tickets), or
    ``conflict`` (a pending-sign-off draft already exists — re-submit with
    ``replace: true`` to discard and re-plan). No GitHub label is changed unless
    ``status == "ok"``.
    """
    if config.sprint_planning_disabled():
        raise HTTPException(404, detail="Sprint planning is disabled")
    result = sprints_service.plan_next_sprint(body.project, body.replace)
    invalidate_board(body.project)
    return result


@router.get("/api/sprints/pending-signoff")
def get_pending_signoff_sprints(project: str):
    """Labels of sprints in pending-sign-off state for a project (issue #861)."""
    if config.sprint_signoff_disabled():
        return {"labels": []}
    return sprints_service.pending_signoff_sprints(project)


@router.get("/api/sprints/running-all")
def get_all_running_sprints():
    return sprints_service.get_all_running_sprints()


@router.get("/api/sprints/{sprint_label}/dispatch-log")
def get_dispatch_log(sprint_label: str, project: str, tail_lines: int = 200):
    """Return the last N lines of the most recent sprint-run-<label>-*.log."""
    return sprints_service.get_dispatch_log(sprint_label, project, tail_lines)


@router.get("/api/sprints/{sprint_label}/preview-dag")
def get_sprint_preview_dag(sprint_label: str, project: str):
    """Read-only execution preview for a planned sprint (issue #809).

    Returns predicted dispatch levels, file conflicts, cycles, and the
    unestimated-ticket list using dag_builder over the sprint's cached tickets.
    Uses cached data only — adds zero GitHub API calls.
    """
    return sprints_service.preview_dag(sprint_label, project)


@router.get("/api/sprints/{sprint_label}/dag-order-preview")
def get_dag_order_preview(sprint_label: str, project: str):
    """Compute DAG-ordered ticket sequence without persisting (issue #1420).

    Returns:
      new_order — proposed ticket order following DAG topological levels
      diff      — human-readable lines describing each position change
      is_noop   — true when current plan already matches DAG order
      partial   — true when preview-dag has unestimated tickets
    """
    return sprints_service.dag_order_preview(sprint_label, project)
