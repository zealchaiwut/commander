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
from fastapi import APIRouter
from pydantic import BaseModel

from . import brief
from . import sprints_service
from . import todos

router = APIRouter(tags=["sprints"])

# Brief assembly endpoints (issue #839) ride on this already-mounted router so
# no route lands in server.py (COMMANDER_GATE_MONOLITH, issue #761). The brief
# router declares full paths and carries no prefix, so include_router mounts
# them unchanged.
router.include_router(brief.router)

# Per-project to-do list endpoints (issue #843) ride on this already-mounted
# router for the same reason — todos carries its own ``/api/projects`` prefix,
# so include_router mounts the routes unchanged without growing server.py.
router.include_router(todos.router)


class SprintOrderBody(BaseModel):
    order: list[str]


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
    return sprints_service.save_sprint_goal(body.project, body.sprint_label, body.goal)


@router.get("/api/sprints/order")
def get_sprint_order(project: str):
    """Return the persisted sprint display order for a project slug."""
    return sprints_service.get_sprint_order(project)


@router.post("/api/sprints/order")
def save_sprint_order(project: str, body: SprintOrderBody):
    """Persist sprint display order for a project slug."""
    return sprints_service.save_sprint_order(project, body.order)


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
