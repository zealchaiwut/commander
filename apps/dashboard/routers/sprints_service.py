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
        # New sprints start as `draft` under the unified lifecycle, and enter
        # the pending sign-off gate (issue #862): they cannot run until an
        # explicit approval is recorded on the board.
        try:
            srv._plan_json_set_state(
                project_root, sprint_label, "draft",
                created_at=datetime.now(timezone.utc).isoformat(),
                signoff={"state": "pending"},
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


# ── Plan next sprint (issue #861) ────────────────────────────────────────────

def _planning_size_minutes(srv, project: str) -> dict:
    """Project-configured size->minutes table (falls back to planner defaults)."""
    from services.sprint_manager.sprint_planner import DEFAULT_SIZE_MINUTES
    try:
        eff = srv.build_effective_response(
            srv._settings_repo.get_setting(srv.APP_CONFIG_KEY, project=project))
    except Exception:  # noqa: BLE001 — settings backend optional
        return dict(DEFAULT_SIZE_MINUTES)
    return {
        "S": eff.get("estimation_s_minutes", DEFAULT_SIZE_MINUTES["S"]),
        "M": eff.get("estimation_m_minutes", DEFAULT_SIZE_MINUTES["M"]),
        "L": eff.get("estimation_l_minutes", DEFAULT_SIZE_MINUTES["L"]),
        "XL": eff.get("estimation_xl_minutes", DEFAULT_SIZE_MINUTES["XL"]),
    }


def _planning_capacity(srv, project: str) -> int:
    """Configured sprint capacity in minutes (``sprint_budget_minutes``)."""
    try:
        eff = srv.build_effective_response(
            srv._settings_repo.get_setting(srv.APP_CONFIG_KEY, project=project))
        return int(eff.get("sprint_budget_minutes", 180))
    except Exception:  # noqa: BLE001
        return 180


def _issue_to_plan_ticket(srv, iss: dict, estimates_dir, size_minutes: dict,
                          *, carry_over: bool = False):
    """Build a ``PlanTicket`` from a GitHub issue + its resolved estimate."""
    from services.sprint_manager.sprint_planner import PlanTicket
    resolved = srv._resolve_issue_estimate(iss, estimates_dir)
    size = resolved["size"]
    minutes = size_minutes.get(size) if size else None
    return PlanTicket(
        number=iss["number"], title=iss.get("title", ""),
        size=size, minutes=minutes, files=list(resolved.get("files") or []),
        carry_over=carry_over,
    )


def _carry_over_issues(srv, gc, project: str) -> list[dict]:
    """Unfinished tickets from the most recent completed sprint (AC4)."""
    from services.sprint_manager import sprint_planner
    try:
        finished = srv._finished_sprint_summaries(project) or {}
    except Exception:  # noqa: BLE001
        finished = {}
    nums = []
    for label in finished:
        try:
            nums.append(int(label.split("-")[1].split(".")[0]))
        except (IndexError, ValueError):
            continue
    if not nums:
        return []
    last = max(nums)
    try:
        sprint_issues = gc.list_issues(last, repo_name=project)
    except Exception:  # noqa: BLE001
        return []
    return sprint_planner.carry_over_tickets(
        sprint_issues, is_finished=lambda i: gc.classify_issue(i) == "done")


def _pending_signoff_labels(srv, project: str) -> list[str]:
    """All sprint labels whose persisted plan is in pending-sign-off state."""
    import json
    from services.sprint_manager.sprint_planner import PENDING_SIGNOFF_STATUS
    project_root = srv._project_root_path(project)
    sprints_dir = srv._commander_dir(project_root) / "sprints"
    if not sprints_dir.exists():
        return []
    labels = []
    for path in sorted(sprints_dir.glob("sprint-*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if data.get("status") == PENDING_SIGNOFF_STATUS:
            labels.append(data.get("label") or path.stem)
    return labels


def _existing_pending_signoff(srv, project: str):
    """Return the label of an existing pending-sign-off draft, or None (AC11)."""
    labels = _pending_signoff_labels(srv, project)
    return labels[0] if labels else None


def pending_signoff_sprints(project: str) -> dict:
    """Labels of sprints currently in pending-sign-off state (issue #861, AC9).

    Drives the board's visual distinction: the frontend marks these cards as
    pending sign-off after each render.
    """
    srv = _server()
    return {"labels": _pending_signoff_labels(srv, project)}


def _make_estimate_fn(srv, gc, project: str, issue_by_num: dict, size_minutes: dict):
    """Build the lazy estimator hook the planner calls for unsized tickets (AC7)."""
    from services.sprint_manager.estimate_issue import (
        apply_estimated_status, apply_label, run_estimator,
    )
    from services.sprint_manager.sprint_planner import PlanTicket

    def estimate_fn(ticket):
        iss = issue_by_num.get(ticket.number)
        if iss is None:
            return None
        issue_data = {
            "title": iss.get("title", ""),
            "body": iss.get("body", ""),
            "labels": iss.get("labels", []),
        }
        try:
            est, err = run_estimator(ticket.number, issue_data, project=project)
        except Exception:  # noqa: BLE001 — estimator is best-effort (AC7)
            return None
        if not est or err:
            return None
        size = est.get("size")
        if not size:
            return None
        # Persist the new size + estimated status to GitHub so the draft reflects it.
        try:
            apply_label(ticket.number, project, size)
            apply_estimated_status(ticket.number, project)
        except Exception:  # noqa: BLE001 — labelling failure must not lose the estimate
            pass
        return PlanTicket(
            number=ticket.number, title=ticket.title, size=size,
            minutes=size_minutes.get(size),
            files=list(est.get("files_likely_affected") or []),
            carry_over=ticket.carry_over,
        )

    return estimate_fn


def _persist_pending_signoff(srv, gc, project: str, sprint_num: int,
                             milestone_title: str, result) -> str:
    """Create the sprint label, apply it to the selected tickets, and write the
    pending-sign-off plan file — verified, with rollback on failure (AC2, AC9).
    """
    from services.sprint_manager import sprint_creation
    from services.sprint_manager.sprint_planner import PENDING_SIGNOFF_STATUS

    sprint_label = f"sprint-{sprint_num}"
    ticket_nums = [t.number for t in result.selected]
    project_root = srv._project_root_path(project)

    def _write_plan():
        srv._sprint_json_write(
            srv._sprint_json_path(project_root, sprint_label),
            {
                "label": sprint_label,
                "goal": f"Auto-planned from milestone {milestone_title}",
                "project": project,
                "status": PENDING_SIGNOFF_STATUS,
                "milestone": milestone_title,
                "tickets": ticket_nums,
                "total_minutes": result.total_minutes,
            },
        )

    def _plan_written():
        return srv._sprint_json_path(project_root, sprint_label).exists()

    deps = sprint_creation.SprintCreateDeps(
        github_client=gc, repo=project, sprint_num=sprint_num,
        tickets=ticket_nums, write_plan_fn=_write_plan, plan_written_fn=_plan_written,
    )
    try:
        sprint_creation.create_sprint_verified(deps)
    finally:
        gc.invalidate("sprints:")
    return sprint_label


def plan_next_sprint(project: str, replace: bool = False) -> dict:
    """Draft the next sprint from the active milestone's backlog (issue #861).

    Pulls open backlog tickets from the active milestone, carries over unfinished
    work from the most recent completed sprint, packs to the configured capacity
    (preferring estimated tickets and estimating unsized ones that fit), orders by
    the dependency DAG, and creates a pending-sign-off sprint. Returns a JSON dict
    describing the outcome; never raises for the expected empty/conflict cases.
    """
    from services.sprint_manager import sprint_planner
    from services.sprint_manager.sprint_planner import (
        STATUS_CONFLICT, STATUS_NO_MILESTONE, STATUS_OK,
    )

    srv = _server()
    gc = srv.github_client

    milestones = gc.list_milestones(repo_name=project)
    milestone = sprint_planner.active_milestone(milestones)
    if milestone is None:
        return {"status": STATUS_NO_MILESTONE, "created": False,
                "reason": "No active milestone — nothing to plan."}

    existing = _existing_pending_signoff(srv, project)
    if existing and not replace:
        return {"status": STATUS_CONFLICT, "created": False,
                "existing_label": existing,
                "reason": (f"A pending-sign-off sprint ({existing}) already exists. "
                           "Replace it or cancel.")}

    capacity = _planning_capacity(srv, project)
    size_minutes = _planning_size_minutes(srv, project)
    project_root = srv._project_root_path(project)
    estimates_dir = srv._commander_dir(project_root) / "estimates"

    issues = gc.list_open_issues_for_planning(repo_name=project) or []
    issue_by_num = {i["number"]: i for i in issues}

    carry_issues = _carry_over_issues(srv, gc, project)
    carry_nums = {i["number"] for i in carry_issues}
    for i in carry_issues:
        issue_by_num.setdefault(i["number"], i)

    backlog_issues = sprint_planner.select_milestone_backlog(
        issues, milestone["title"],
        is_backlog=lambda i: gc.classify_issue(i) == "backlog")
    backlog_issues = [i for i in backlog_issues if i["number"] not in carry_nums]

    carry_tickets = [
        _issue_to_plan_ticket(srv, i, estimates_dir, size_minutes, carry_over=True)
        for i in carry_issues
    ]
    backlog_tickets = [
        _issue_to_plan_ticket(srv, i, estimates_dir, size_minutes)
        for i in backlog_issues
    ]

    sprints = gc.list_sprints(repo_name=project)
    next_num = (max(sprints) if sprints else 0) + 1

    estimate_fn = _make_estimate_fn(srv, gc, project, issue_by_num, size_minutes)
    result = sprint_planner.plan_next_sprint(
        has_active_milestone=True, capacity_minutes=capacity,
        carry_over=carry_tickets, backlog=backlog_tickets,
        next_sprint_number=next_num, existing_pending_label=None,
        estimate_fn=estimate_fn, order_fn=sprint_planner.dag_order,
        size_minutes=size_minutes,
    )

    if result.status != STATUS_OK:
        return {"status": result.status, "created": False, "reason": result.reason}

    label = _persist_pending_signoff(
        srv, gc, project, next_num, milestone["title"], result)
    return {
        "status": STATUS_OK, "created": True, "sprint_label": label,
        "milestone": milestone["title"],
        "tickets": [t.number for t in result.selected],
        "total_minutes": result.total_minutes,
        "capacity_minutes": capacity,
    }


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

    from services.sprint_manager.estimate_accuracy import should_warn_unreliable
    accuracy_warning = should_warn_unreliable(estimates_dir / "accuracy")

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
        "accuracy_warning": accuracy_warning,
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
    # A shared-file conflict only matters when both tickets land in the SAME
    # dispatch level (they'd run in parallel). If the DAG already sequenced them
    # into different levels, there is no parallel risk — drop the conflict so the
    # run preview doesn't show a phantom "#a ∥ #b" with a fix chip that no-ops
    # because the tickets are already ordered. Same-level (and degraded
    # single-level) conflicts are the real ones and stay.
    _level_of: dict[str, int] = {}
    for _li, _layer in enumerate(levels):
        for _tid in _layer:
            _level_of[_tid] = _li
    same_level_conflicts = [
        c for c in result_base["conflicts"]
        if (
            _level_of.get(f"#{c['ticket1_id']}") is not None
            and _level_of.get(f"#{c['ticket1_id']}") == _level_of.get(f"#{c['ticket2_id']}")
        )
    ]
    return {
        **result_base,
        "conflicts": same_level_conflicts,
        "levels": levels,
        "cycles": [],
    }


def compute_dag_order(
    current_order: list[int],
    levels: list[list[str]],
    conflicts: list[dict],
) -> dict:
    """Compute DAG-topological ticket order and diff vs current order (issue #1420).

    Reorders ``current_order`` so tickets in lower DAG levels appear before
    higher levels; within each level the relative order from ``current_order``
    is preserved. Tickets absent from ``levels`` are appended after all levelled
    tickets in their original relative order.

    Returns:
      new_order   — list[int] in topological order
      diff        — list[str] describing each moved ticket
      is_noop     — True when new_order == current_order
    """
    # level index per ticket number
    level_by_num: dict[int, int] = {}
    for li, lvl in enumerate(levels):
        for tid in lvl:
            try:
                level_by_num[int(tid.lstrip("#"))] = li
            except ValueError:
                pass

    # conflict set for dep-edge hints
    conflicts_set: set[tuple[int, int]] = set()
    for c in conflicts:
        a = c.get("ticket1_id")
        b = c.get("ticket2_id")
        if a and b:
            conflicts_set.add((min(int(a), int(b)), max(int(a), int(b))))

    n_levels = len(levels)
    # group current_order tickets by DAG level, preserving within-level order
    by_level: dict[int, list[int]] = {}
    for num in current_order:
        li = level_by_num.get(num, n_levels)   # unknown → append after DAG tickets
        by_level.setdefault(li, []).append(num)

    max_level = max([n_levels] + list(by_level.keys())) if by_level else n_levels
    new_order: list[int] = []
    for li in range(max_level + 1):
        new_order.extend(by_level.get(li, []))

    is_noop = new_order == list(current_order)
    if is_noop:
        return {"new_order": new_order, "diff": [], "is_noop": True}

    # Build diff: one line per ticket that moves to an earlier position.
    new_idx_map = {num: i for i, num in enumerate(new_order)}
    old_idx_map = {num: i for i, num in enumerate(current_order)}
    seen_pairs: set[tuple[int, int]] = set()
    diff_lines: list[str] = []

    for num in current_order:
        old_idx = old_idx_map[num]
        new_idx = new_idx_map.get(num, old_idx)
        if new_idx >= old_idx:
            continue  # didn't move earlier — skip

        # Find the first ticket in new_order immediately after this one
        if new_idx + 1 < len(new_order):
            other = new_order[new_idx + 1]
            pair = (min(num, other), max(num, other))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)

            num_level = level_by_num.get(num)
            other_level = level_by_num.get(other)
            reason = ""
            if (num_level is not None and other_level is not None
                    and num_level < other_level
                    and pair in conflicts_set):
                reason = f" — dep edge #{num} blocks #{other}"
            elif (num_level is not None and other_level is not None
                    and num_level < other_level):
                reason = f" — #{num} (level {num_level}) before #{other} (level {other_level})"
            diff_lines.append(f"#{num} moves before #{other}{reason}")

    return {"new_order": new_order, "diff": diff_lines, "is_noop": False}


def compute_order_violations(
    current_order: list[int],
    levels: list[list[str]],
    conflicts: list[dict],
) -> dict[int, list[dict]]:
    """Return tickets that violate DAG order (issue #1422).

    A violation occurs when ticket B appears before its upstream dependency A
    in current_order. Dependency relationships are derived from conflicts
    (file-ownership edges: ticket1_id must precede ticket2_id).

    Returns a dict mapping violating ticket numbers to a list of violation
    objects, each with keys:
      upstream_num  — int, the dependency that must come first
      reason        — str, human-readable explanation
    """
    if not conflicts:
        return {}

    pos: dict[int, int] = {num: i for i, num in enumerate(current_order)}

    violations: dict[int, list[dict]] = {}
    for c in conflicts:
        upstream = c.get("ticket1_id")
        downstream = c.get("ticket2_id")
        if upstream is None or downstream is None:
            continue
        upstream = int(upstream)
        downstream = int(downstream)
        if upstream not in pos or downstream not in pos:
            continue
        # Violation: downstream appears before upstream
        if pos[downstream] < pos[upstream]:
            shared = c.get("shared_files", [])
            file_hint = shared[0] if shared else "shared file"
            reason = f"#{downstream} must come after #{upstream} (shares {file_hint})"
            violations.setdefault(downstream, []).append({
                "upstream_num": upstream,
                "reason": reason,
            })

    return violations


def compute_fix_order_slot(
    current_order: list[int],
    ticket_num: int,
    violations: list[dict],
) -> list[int]:
    """Move ticket_num to the earliest valid slot after all its dependencies (issue #1422).

    Removes ticket_num from current_order, finds the latest position among all
    upstream dependencies, then inserts ticket_num immediately after it.
    Other tickets keep their relative positions.

    Returns the new order list.
    """
    upstream_nums = {int(v["upstream_num"]) for v in violations}

    remaining = [n for n in current_order if n != ticket_num]

    latest_upstream_idx = -1
    for i, n in enumerate(remaining):
        if n in upstream_nums:
            latest_upstream_idx = i

    insert_at = latest_upstream_idx + 1
    new_order = remaining[:insert_at] + [ticket_num] + remaining[insert_at:]
    return new_order


def dag_order_preview(sprint_label: str, project: str) -> dict:
    """Compute DAG-ordered ticket sequence without persisting (issue #1420).

    Returns new_order, diff, is_noop, and the partial flag from preview_dag.
    Raises HTTPException 409 if the sprint has circular dependencies.
    """
    srv = _server()

    dag_data = preview_dag(sprint_label, project)
    if dag_data.get("cycles"):
        raise HTTPException(
            409,
            detail="Sprint has circular dependencies — cannot compute DAG order",
        )

    levels: list[list[str]] = dag_data.get("levels") or []
    conflicts: list[dict] = dag_data.get("conflicts") or []
    partial: bool = bool(dag_data.get("partial"))

    # Current order: plan.json tickets field, then ascending issue-number from DAG levels.
    project_root = srv._project_root_path(project)
    plan_data = srv._read_plan_json(project_root, sprint_label)
    current_order: list[int] = []
    if plan_data is not None:
        raw = plan_data.get("tickets", plan_data) if isinstance(plan_data, dict) else plan_data
        if isinstance(raw, list) and all(isinstance(n, int) for n in raw):
            current_order = list(raw)

    if not current_order:
        # Fall back to ascending issue-number order from DAG ticket list
        seen: set[int] = set()
        for lvl in levels:
            for tid in lvl:
                try:
                    num = int(tid.lstrip("#"))
                    if num not in seen:
                        seen.add(num)
                        current_order.append(num)
                except ValueError:
                    pass

    result = compute_dag_order(current_order, levels, conflicts)
    return {**result, "partial": partial}
