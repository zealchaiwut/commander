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
    """Body for POST /api/sprints/{label}/dispatch (issue #2315 / #2353).

    Non-empty ``tickets`` are executed in the order given — the runner never
    sorts or reorders that list (#2311). When ``tickets`` is empty (or
    ``all`` is true), open issues for the sprint label are resolved from the
    issues mirror and ordered by ascending issue number unless
    ``order="dag"``.
    """

    tickets: list[int] = []
    all: bool = False
    order: str | None = None  # "dag" | None (default: ascending issue number)
    repo: str | None = None
    cwd: str | None = None
    baseline_note: str | None = None


def _commander_repo_root():
    from pathlib import Path

    return Path(config.__file__).resolve().parent.parent.parent


def _apply_dag_order(sprint_label: str, project: str, tickets: list[int]) -> list[int]:
    """Reorder ``tickets`` using the existing DAG preview; keep unknowns last."""
    preview = sprints_service.dag_order_preview(sprint_label, project)
    new_order = preview.get("new_order") or []
    ticket_set = set(tickets)
    ordered = [n for n in new_order if n in ticket_set]
    seen = set(ordered)
    return ordered + [n for n in tickets if n not in seen]


def _resolve_dispatch_tickets(
    sprint_label: str,
    body: SprintDispatchBody,
    *,
    repo: str | None,
) -> list[int]:
    """Resolve the ticket list for a dispatch call (issue #2353).

    Explicit non-empty ``tickets`` win (order preserved). Otherwise open
    issues for the exact sprint label are loaded (child labels like
    ``sprint-N.1`` are excluded). Default order is ascending issue number;
    ``order="dag"`` uses :func:`sprints_service.dag_order_preview`.
    """
    if body.tickets:
        tickets = list(body.tickets)
    else:
        import github_client
        from services.sprint_manager.ticket_retry import open_issue_numbers_for_label

        tickets = open_issue_numbers_for_label(github_client, sprint_label, repo)

    if not tickets:
        raise HTTPException(
            status_code=400,
            detail=(
                f"no open tickets for label {sprint_label!r}"
                if not body.tickets
                else "tickets must not be empty"
            ),
        )

    if (body.order or "").lower() == "dag":
        if not repo:
            raise HTTPException(
                status_code=400,
                detail="order=dag requires repo (or sprint.yaml repo_name)",
            )
        tickets = _apply_dag_order(sprint_label, repo, tickets)

    return tickets


@router.post("/api/sprints/{sprint_label}/dispatch")
def dispatch_sprint(sprint_label: str, body: SprintDispatchBody):
    """Start a dispatch run for a sprint and return its handle (issue #2315).

    The privileged agent spawn happens inside this service, which is the point
    of the #2314 decision: the caller makes an ordinary API call rather than
    elevating anything itself.

    Returns immediately; poll ``GET /api/sprints/dispatch/{run_id}`` for status.

    When ``tickets`` is omitted/empty (or ``all: true``), open issues carrying
    this sprint label are resolved automatically (#2353). Child labels
    (``sprint-N.1``) are not included when dispatching parent ``sprint-N``.
    """
    from pathlib import Path

    from services.sprint_manager.dispatch_runner import (
        DispatchConfigError,
        load_project_config,
        start_run,
    )

    # Empty ``tickets`` (or ``all: true``) resolves open issues for the label
    # (#2353). Explicit non-empty ``tickets`` always win, even with ``all``.
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

    repo = body.repo or config.repo_name or None

    tickets = _resolve_dispatch_tickets(sprint_label, body, repo=repo)

    # Sprint-branch model: create sprint/sprint-N from develop before
    # dispatching tickets so feature branches have a stable base (#2329).
    sprint_branch = None
    if repo:
        try:
            from services.sprint_manager.sprint_branch import ensure_sprint_branch  # noqa: PLC0415
            sprint_branch = ensure_sprint_branch(sprint_label, repo, cwd=str(cwd))
        except Exception as exc:
            # Non-fatal: projects without the sprint-branch model keep working.
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "sprint branch creation failed for %s/%s: %s",
                repo, sprint_label, exc,
            )

    run = start_run(
        sprint_label,
        tickets,
        repo=repo,
        repo_root=repo_root,
        cwd=cwd,
        config=config,
        sprint_branch=sprint_branch,
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


class SprintOvernightBody(BaseModel):
    """Body for POST /api/sprints/{label}/overnight (issue #2354).

    ``max_retries`` is the number of retries *after* the first failed dispatch
    (default 2). ``max_retries: 0`` exhausts immediately on first failure.
    Empty ``tickets`` resolves open issues for the label (#2353).
    """

    tickets: list[int] = []
    all: bool = True
    max_retries: int = 2
    repo: str | None = None
    cwd: str | None = None
    baseline_note: str | None = None


@router.post("/api/sprints/{sprint_label}/overnight")
def overnight_sprint(sprint_label: str, body: SprintOvernightBody):
    """Start an overnight babysitter: dispatch, reset, re-dispatch (#2354).

    Returns immediately with ``overnight_id``. Poll
    ``GET /api/sprints/overnight/{overnight_id}``. Stop with
    ``POST /api/sprints/overnight/{overnight_id}/stop``.
    """
    from pathlib import Path

    import github_client
    from services.sprint_manager.dispatch_runner import (
        DispatchConfigError,
        load_project_config,
    )
    from services.sprint_manager.overnight_runner import start_overnight

    repo_root = _commander_repo_root()
    cwd = Path(body.cwd) if body.cwd else repo_root
    if not cwd.exists():
        raise HTTPException(status_code=400, detail=f"cwd does not exist: {cwd}")

    try:
        config = load_project_config(cwd)
    except DispatchConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    repo = body.repo or config.repo_name or None
    # Reuse dispatch resolution (empty / all → open issues for label).
    dispatch_body = SprintDispatchBody(
        tickets=list(body.tickets),
        all=body.all if not body.tickets else False,
        repo=repo,
        cwd=str(cwd),
        baseline_note=body.baseline_note,
    )
    tickets = _resolve_dispatch_tickets(sprint_label, dispatch_body, repo=repo)

    if body.max_retries < 0:
        raise HTTPException(status_code=400, detail="max_retries must be >= 0")

    run = start_overnight(
        sprint_label,
        tickets,
        repo=repo,
        repo_root=repo_root,
        cwd=cwd,
        max_retries=body.max_retries,
        config=config,
        github_client=github_client,
        background=True,
    )
    return run.to_dict()


@router.get("/api/sprints/overnight/{overnight_id}")
def get_overnight_run(overnight_id: str):
    """Status of an overnight babysitter run (#2354)."""
    from services.sprint_manager.overnight_runner import load_overnight

    data = load_overnight(overnight_id, _commander_repo_root())
    if data is None:
        raise HTTPException(
            status_code=404, detail=f"unknown overnight run: {overnight_id}"
        )
    return data


@router.post("/api/sprints/overnight/{overnight_id}/stop")
def stop_overnight_run(overnight_id: str):
    """Stop an overnight run at the next dispatch/retry boundary (#2354)."""
    from services.sprint_manager.overnight_runner import request_stop

    if not request_stop(overnight_id, _commander_repo_root()):
        raise HTTPException(
            status_code=404, detail=f"unknown overnight run: {overnight_id}"
        )
    return {"overnight_id": overnight_id, "stop_requested": True}


class CompleteAfterDispatchBody(BaseModel):
    """Body for POST /api/sprints/{label}/complete-after-dispatch (#2357).

    ``uat_signoff`` default false: merge the sprint→develop PR only.
    When true, runs the existing Finish path (merge + close UAT issues).
    ``preview`` / ``dry_run`` reports the plan without mutating.
    """

    project: str
    uat_signoff: bool = False
    preview: bool = False
    dry_run: bool = False


@router.post("/api/sprints/{sprint_label}/complete-after-dispatch")
async def complete_after_dispatch_endpoint(
    sprint_label: str,
    body: CompleteAfterDispatchBody,
):
    """Merge the sprint PR opened by a green dispatch; optional UAT finish (#2357)."""
    from pathlib import Path

    import github_client
    from project_resolver import resolve_project_path as _project_root_path
    from services.sprint_manager.complete_after_dispatch import complete_after_dispatch

    project_root = Path(_project_root_path(body.project))
    preview = body.preview or body.dry_run

    def _merge_pr(url: str, repo: str) -> tuple[bool, str]:
        import subprocess

        merge_res = subprocess.run(
            ["gh", "pr", "merge", url, "--repo", repo, "--merge", "--delete-branch"],
            capture_output=True,
            text=True,
            check=False,
        )
        if merge_res.returncode != 0:
            return False, (
                merge_res.stderr.strip()
                or merge_res.stdout.strip()
                or "merge failed"
            )
        return True, "merged"

    async def _run_finish(sprint_pr_url: str):
        from routers.sprint_finish import FinishSprintBody, finish_sprint_flat

        return await finish_sprint_flat(
            sprint_label,
            body.project,
            FinishSprintBody(
                confirmed=True,
                merge_pr=True,
                sprint_pr_url=sprint_pr_url,
            ),
        )

    # Service layer is sync; for uat_signoff we merge via finish in this coroutine
    # after the preview/lookup step so we stay on the FastAPI event loop.
    lookup = complete_after_dispatch(
        project_root=project_root,
        sprint_label=sprint_label,
        repo=body.project,
        preview=True,  # always lookup first without side effects
        uat_signoff=body.uat_signoff,
        list_uat_fn=lambda repo_name=None: github_client.list_open_uat_issues(
            repo_name=repo_name
        ),
    )
    if not lookup.get("ok"):
        raise HTTPException(
            status_code=409,
            detail=(
                f"No successful sprint→develop PR found for {sprint_label!r}. "
                "Dispatch must complete with status=done and sprint_pr_number set."
            ),
        )
    if preview:
        return lookup

    pr_url = lookup["sprint_pr"]["url"]
    if body.uat_signoff:
        finish_result = await _run_finish(pr_url)
        return {
            "preview": False,
            "ok": True,
            "merged": True,
            "uat_signoff": True,
            "sprint_pr": lookup["sprint_pr"],
            "uat_tickets": lookup.get("uat_tickets") or [],
            "finish_result": finish_result,
            "errors": [],
        }

    ok, detail = _merge_pr(pr_url, body.project)
    return {
        "preview": False,
        "ok": ok,
        "merged": ok,
        "uat_signoff": False,
        "sprint_pr": lookup["sprint_pr"],
        "uat_tickets": lookup.get("uat_tickets") or [],
        "detail": detail,
        "errors": [] if ok else [detail],
    }


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
