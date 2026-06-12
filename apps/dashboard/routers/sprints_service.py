"""Service logic for the sprints router (extracted from server.py, issue #795).

The movable sprint surfaces from the monolith. The heavy lifting (GitHub
client, project-root resolution, sprint-state helpers) still lives in and is
mutated by ``server.py``, so the service reads it back through a deferred
import at request time — keeping a single source of truth and avoiding the
import-time circular dependency (``server.py`` imports this package while
mounting routers).

Out of this wave (pinned to server.py by pre-existing tests the AC forbids
modifying): run_sprint_managed, rerun_sprint, finish_sprint, set_sprint_status,
get_sprint_management_issues, get_sprint_estimate_vs_actual,
get_sprint_estimate_summary, get_sprint_branch_status.
"""
from __future__ import annotations

import subprocess
import sys as _sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from services.sprint_manager import sprint_creation
from services.sprint_manager.sprint_creation import SprintCreationError  # re-export

# server.py is a top-level module on the dashboard path; make sure it resolves.
_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
if str(_DASHBOARD_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_DASHBOARD_ROOT))


def _server():
    """Deferred import of the monolith — safe at request time."""
    import server  # noqa: PLC0415 — intentional late import (see module docstring)
    return server


def create_sprint_verified(project: str, sprint_number=None, goal=None, tickets=None) -> str:
    """Create a sprint-N label for a project as a verified, ordered sequence.

    Runs (1) create GitHub label, (2) apply label to any selected tickets,
    (3) write the plan file — verifying each step, retrying step 1 once on a
    transient failure, and rolling back earlier steps on a non-recoverable
    failure (issue #857). Returns the created ``sprint-N`` label string.

    Raises ``HTTPException`` for validation/conflict errors (400/409) and
    ``SprintCreationError`` (named for the failed step, with a status_code)
    when a creation step fails after retry/rollback — so the caller can surface
    it loudly in the New Sprint dialog. The ``sprint_created`` dashboard event
    is emitted by the caller (server.py) on success.
    """
    srv = _server()
    gc = srv.github_client
    if goal is not None and len(goal.strip()) < 10:
        raise HTTPException(400, detail="Sprint goal must be at least 10 characters")
    try:
        sprints = gc.list_sprints(repo_name=project)
    except subprocess.CalledProcessError as e:
        raise srv._gh_error(e)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))

    if sprint_number is not None:
        if sprint_number in sprints:
            raise HTTPException(409, detail=f"Sprint {sprint_number} already exists")
        target_num = sprint_number
    else:
        target_num = (max(sprints) if sprints else 0) + 1

    sprint_label = f"sprint-{target_num}"
    eff_goal = (goal or sprint_label).strip() or sprint_label
    project_root = srv._project_root_path(project)
    ticket_list = list(tickets or [])

    def _write_plan():
        # Sprint metadata -> local JSON (authoritative plan file) + goal file.
        if goal is not None:
            goal_path = srv._sprint_goal_path(project_root, sprint_label)
            goal_path.parent.mkdir(parents=True, exist_ok=True)
            goal_path.write_text(goal.strip(), encoding="utf-8")
        srv._sprint_json_write(
            srv._sprint_json_path(project_root, sprint_label),
            {"label": sprint_label, "goal": eff_goal, "project": project,
             "status": "pending", "tickets": ticket_list},
        )
        # plan.json is a deprecated cache (issue #757) — best-effort dual-write.
        try:
            srv._plan_json_set_state(
                project_root, sprint_label, "planning",
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        except Exception:
            pass

    def _plan_written():
        return srv._sprint_json_path(project_root, sprint_label).exists()

    deps = sprint_creation.SprintCreateDeps(
        github_client=gc, repo=project, sprint_num=target_num,
        tickets=ticket_list, write_plan_fn=_write_plan, plan_written_fn=_plan_written,
    )
    try:
        sprint_creation.create_sprint_verified(deps)
    finally:
        gc.invalidate("sprints:")
    return sprint_label


def get_sprints():
    srv = _server()
    try:
        sprints = srv.github_client.list_sprints()
        default = srv.github_client.latest_active_sprint()
        return {"sprints": sprints, "default": default}
    except subprocess.CalledProcessError as e:
        raise srv._gh_error(e)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))


def get_sprint_goal(project: str, sprint: str):
    """Return the persisted sprint goal for a project/sprint."""
    srv = _server()
    project_root = srv._project_root_path(project)
    goal_path = srv._sprint_goal_path(project_root, sprint)
    if goal_path.exists():
        return {"goal": goal_path.read_text(encoding="utf-8").strip()}
    return {"goal": ""}


def save_sprint_goal(project: str, sprint_label: str, goal: str):
    """Persist sprint goal to .commander/sprints/<label>-goal.txt."""
    srv = _server()
    if not srv._SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")
    project_root = srv._project_root_path(project)
    goal_path = srv._sprint_goal_path(project_root, sprint_label)
    goal_path.parent.mkdir(parents=True, exist_ok=True)
    goal_path.write_text(goal, encoding="utf-8")
    return {"ok": True}


def get_sprint_order(project: str):
    """Return the persisted sprint display order for a project slug."""
    srv = _server()
    project_root = srv._project_root_path(project)
    try:
        sprints = srv.github_client.list_sprints(repo_name=None)
    except Exception:
        sprints = []
    order = srv._load_sprint_order(project_root, sprints)
    return {"order": order}


def save_sprint_order(project: str, order: list[str]):
    """Persist sprint display order for a project slug."""
    import json

    srv = _server()
    project_root = srv._project_root_path(project)
    order_path = srv._sprint_order_path(project_root)
    order_path.parent.mkdir(parents=True, exist_ok=True)
    order_path.write_text(json.dumps(order), encoding="utf-8")
    return {"ok": True}


def get_all_running_sprints():
    """Return ALL currently running sprints across all projects.

    Reads plan.json state=running as the authoritative source (issue #507).
    PID files are retained only for process-killing.

    Returns: {"running": [{"project": ..., "sprint_label": ...}, ...]}
    Empty list means no sprints are running.
    """
    srv = _server()
    all_running = srv._all_sprints_running()
    return {"running": all_running}


def get_dispatch_log(sprint_label: str, project: str, tail_lines: int = 200):
    """Return the last N lines of the most recent sprint-run-<label>-*.log."""
    srv = _server()
    if not srv._SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")
    from log_source import read_log  # local import keeps startup fast

    project_root = srv._project_root_path(project)
    return read_log("dispatch", project_root, label=sprint_label, tail_lines=tail_lines)


def preview_dag(sprint_label: str, project: str):
    """Read-only execution preview for a planned sprint (issue #809).

    Builds the predicted dispatch ordering with the same ``dag_builder`` the
    sprint_manager uses, so the preview's execution order matches what a real
    run would produce for the same ticket set (AC7).

    Returns ``{levels, conflicts, cycles, unestimated, partial}``:
      - ``levels``      — topological layers (lists of ``#<n>`` ids); tickets in
                          the same layer are parallel-eligible.
      - ``conflicts``   — pairs of pending tickets that share at least one file.
      - ``cycles``      — cycle paths if dag_builder reports any (else ``[]``).
      - ``unestimated`` — pending tickets lacking a usable estimate; their files
                          are unknown so the preview is partial, not wrong.
      - ``partial``     — true when any ticket is unestimated (drives the amber
                          warning, AC5).

    Sources issues from the github_client cache only — zero GitHub API calls
    (AC2). Size/files resolved via ``server._resolve_issue_estimate`` (JSON +
    GitHub size-* labels — same rule as the board UI).
    """
    srv = _server()
    if not srv._SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    # Cache-only read — never spends a GitHub API call (AC2).
    all_issues = srv.github_client.cached_open_issues_with_body(repo_name=project) or []
    sprint_issues = [
        iss for iss in all_issues
        if any(lbl["name"] == sprint_label for lbl in iss.get("labels", []))
    ]
    if not sprint_issues:
        raise HTTPException(404, detail=f"Sprint {sprint_label!r} not found")

    pending = [
        iss for iss in sprint_issues
        if srv.github_client.classify_issue(iss) == "backlog"
    ]
    # Deterministic input order (ascending issue number) so DAG ownership and
    # layering match the sprint_manager's number-ascending default (AC7).
    pending.sort(key=lambda iss: iss["number"])

    project_root = srv._project_root_path(project)
    estimates_dir = srv._commander_dir(project_root) / "estimates"

    ticket_files: list[dict] = []
    unestimated: list[str] = []
    for iss in pending:
        num = iss["number"]
        tid = f"#{num}"
        resolved = srv._resolve_issue_estimate(iss, estimates_dir)
        if not resolved["estimated"]:
            unestimated.append(tid)
        ticket_files.append({
            "id": tid,
            "number": num,
            "title": iss.get("title", ""),
            "files": resolved["files"],
        })

    # Pairwise file conflicts among pending tickets.
    conflicts: list[dict] = []
    for i in range(len(ticket_files)):
        for j in range(i + 1, len(ticket_files)):
            a, b = ticket_files[i], ticket_files[j]
            shared = sorted(set(a["files"]) & set(b["files"]))
            if shared:
                conflicts.append({
                    "ticket1_id": a["number"],
                    "ticket1_title": a["title"],
                    "ticket2_id": b["number"],
                    "ticket2_title": b["title"],
                    "shared_files": shared,
                })

    partial = bool(unestimated)
    result_base = {
        "sprint_label": sprint_label,
        "conflicts": conflicts,
        "unestimated": unestimated,
        "partial": partial,
    }

    if not srv._DAG_BUILDER_AVAILABLE:
        # Degrade gracefully — one flat level, no cycles.
        return {
            **result_base,
            "levels": [[tf["id"] for tf in ticket_files]] if ticket_files else [],
            "cycles": [],
            "warning": "dag_builder_unavailable",
        }

    dag_tickets = [{"id": tf["id"], "files_touched": tf["files"]} for tf in ticket_files]
    result = srv._build_dag(dag_tickets)

    if isinstance(result, srv._CycleError):
        return {**result_base, "levels": [], "cycles": result.cycles}

    # Sort within each layer by ascending issue number to match sprint_manager.
    levels = [
        sorted(layer, key=lambda tid: int(tid.lstrip("#")))
        for layer in result.layers
    ]
    return {**result_base, "levels": levels, "cycles": []}
