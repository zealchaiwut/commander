"""Sprint CRUD route handlers extracted from server.py (issue #1257).

Routes owned by this module:
  POST   /api/sprints/create                         — create a new sprint label
  POST   /api/sprints/{sprint_label}/rename          — rename a sprint label
  POST   /api/sprints/{sprint_label}/tickets/reorder — reorder tickets within a sprint
  POST   /api/sprints/{sprint_label}/plan            — persist ticket execution order
  DELETE /api/sprints/{sprint_label}                 — remove a sprint label and unlabel tickets
  POST   /api/sprints/delete-empty                   — delete empty sprint labels (explicit list)
  POST   /api/sprints/cleanup-empty                  — delete leading consecutive empty sprint labels

Shared server.py helpers (_SPRINT_LABEL_RE, _project_root_path, _commander_dir, etc.)
are accessed via the deferred ``_server()`` import to keep the circular-import guard intact.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
if str(_DASHBOARD_ROOT) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_ROOT))

import db          # noqa: E402
import github_client  # noqa: E402
from .board_cache import invalidate_board  # noqa: E402
from services.sprint_manager.sprint_creation import SprintCreationError  # noqa: E402

router = APIRouter(tags=["sprint_crud"])


def _server():
    """Deferred import of the monolith — safe at request time, avoids circular import."""
    import server  # noqa: PLC0415
    return server


# ── Pydantic request models (moved from server.py) ────────────────────────────

class SprintCreateBody(BaseModel):
    project: str
    sprint_number: Optional[int] = None
    goal: Optional[str] = None
    tickets: Optional[list[int]] = None


class SprintRenameBody(BaseModel):
    new_sprint_number: int
    project: str


class SprintTicketReorderBody(BaseModel):
    issue_numbers: list[int]
    project: str


class SprintDeleteBody(BaseModel):
    labels: list[str]  # list of sprint-N label names to delete
    project: str


class SprintCleanupBody(BaseModel):
    project: str


# ── Route handlers ─────────────────────────────────────────────────────────────

@router.post("/api/sprints/create")
async def create_sprint_label(body: SprintCreateBody):
    """Create a sprint-N label as a verified, retry-and-rollback sequence (#857).

    The step orchestration (create label -> apply ticket labels -> write plan,
    each verified, step 1 retried once, rolled back on failure) lives in
    sprints_service; a failed step surfaces as a loud, step-named HTTP error.
    """
    from routers import sprints_service  # noqa: PLC0415 — deferred (router import cycle)
    from services.sprint_manager.sprint_creation import SprintCreationError  # noqa: PLC0415
    srv = _server()
    try:
        sprint_label = sprints_service.create_sprint_verified(
            body.project, body.sprint_number, body.goal, body.tickets,
        )
    except SprintCreationError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
    srv._emit_dashboard_event(
        project=body.project or "dashboard",
        type="sprint_created",
        target=sprint_label,
        detail={"sprint_name": sprint_label},
        action_id=str(uuid.uuid4()),
    )
    invalidate_board(body.project)
    return {"ok": True, "sprint_label": sprint_label}


@router.post("/api/sprints/{sprint_label}/rename")
async def rename_sprint_label(sprint_label: str, body: SprintRenameBody):
    """Rename a sprint label to a new sprint number.

    GitHub's label edit API updates all issues automatically — no per-issue
    re-labelling is required.
    """
    srv = _server()
    if not srv._SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    if body.new_sprint_number <= 0:
        raise HTTPException(400, detail="Sprint number must be a positive integer")

    new_label = f"sprint-{body.new_sprint_number}"
    if new_label == sprint_label:
        raise HTTPException(400, detail="New sprint number is the same as the current one")

    try:
        existing = github_client.list_sprints(repo_name=body.project)
    except subprocess.CalledProcessError as e:
        raise srv._gh_error(e)

    if body.new_sprint_number in existing or srv._sprint_number_reserved(
        body.project, body.new_sprint_number,
    ):
        raise HTTPException(409, detail=f"Sprint {body.new_sprint_number} already exists")

    project_root = srv._project_root_path(body.project)
    if srv._is_sprint_running(project_root, sprint_label):
        raise HTTPException(409, detail="Cannot rename a sprint that is currently running")

    # Rename the GitHub label (updates all issues automatically via GitHub API)
    try:
        github_client.edit_label(
            sprint_label,
            new_label,
            description=f"Sprint {body.new_sprint_number} issues",
            repo_name=body.project,
        )
    except subprocess.CalledProcessError as e:
        raise srv._gh_error(e)

    # Rename local files
    commander = srv._commander_dir(project_root)
    sprints_dir = commander / "sprints"
    for suffix in ("-goal.txt", "-state.json", "-plan.json"):
        old_path = sprints_dir / f"{sprint_label}{suffix}"
        new_path = sprints_dir / f"{new_label}{suffix}"
        if old_path.exists():
            old_path.rename(new_path)

    # Update sprint order JSON
    order_path = srv._sprint_order_path(project_root)
    if order_path.exists():
        try:
            order: list[str] = json.loads(order_path.read_text(encoding="utf-8"))
            order = [new_label if s == sprint_label else s for s in order]
            order_path.write_text(json.dumps(order), encoding="utf-8")
        except Exception:
            pass

    # Mirror the rename into the durable SQLite sprints table (issue #758 removed
    # the Neon mirror; best-effort — GitHub is the source of truth for labels).
    try:
        db.rename_sprint(sprint_label, new_label, body.project)
    except Exception:
        pass

    invalidate_board(body.project)
    return {"ok": True, "old_label": sprint_label, "new_label": new_label}


@router.post("/api/sprints/{sprint_label}/tickets/reorder")
def reorder_sprint_tickets(sprint_label: str, body: SprintTicketReorderBody):
    """Reorder tickets within a sprint. Persists to local JSON + durable SQLite."""
    srv = _server()
    if not srv._SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    # Persist to local JSON; the durable SQLite ticket-order write happens below.
    # The Neon mirror was removed in issue #758.
    project_root = srv._project_root_path(body.project)
    json_path = srv._sprint_json_path(project_root, sprint_label)
    data = srv._sprint_json_read(json_path)
    if "tickets" in data:
        by_num = {t["issue_number"]: t for t in data["tickets"]}
        data["tickets"] = [
            {**by_num[n], "position": pos}
            for pos, n in enumerate(body.issue_numbers)
            if n in by_num
        ]
        srv._sprint_json_write(json_path, data)

    # Durable ticket order (issue #757); best-effort so it never breaks the reorder.
    try:
        db.set_sprint_ticket_order(sprint_label, body.issue_numbers)
    except Exception:
        pass

    invalidate_board(body.project)
    return {"ok": True}


@router.post("/api/sprints/{sprint_label}/plan")
async def save_sprint_plan(sprint_label: str, project: str, request: Request):
    """Persist ticket execution order to {label}-plan.json (issue #441).

    Preserves existing state/timestamp fields (issue #507) when updating tickets.
    """
    srv = _server()
    if not srv._SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, detail="Body must be a JSON array of integers")
    if not isinstance(body, list) or not all(isinstance(n, int) for n in body):
        raise HTTPException(400, detail="Body must be a JSON array of integers")
    project_root = srv._project_root_path(project)
    existing = srv._read_plan_json(project_root, sprint_label) or {}
    existing["tickets"] = body
    srv._write_plan_json(project_root, sprint_label, existing)
    # Durable ticket order (issue #757); plan.json is the deprecated cache.
    try:
        db.set_sprint_ticket_order(sprint_label, body)
    except Exception:
        pass
    invalidate_board(project)
    return {"ok": True}


@router.post("/api/sprints/delete-empty")
async def delete_empty_sprints(body: SprintDeleteBody):
    """Delete empty sprint labels from GitHub.

    Only allows deleting sprint-N labels with 0 tickets that are strictly below
    the lowest active sprint number.
    """
    srv = _server()
    sprint_re_local = re.compile(r"^sprint-(\d+)$")

    # Validate all labels are sprint-N pattern
    for label in body.labels:
        if not srv._SPRINT_LABEL_RE.match(label):
            raise HTTPException(400, detail=f"Invalid sprint label: {label!r}")

    # Verify each label has 0 tickets before deleting, and compute lowest active sprint
    try:
        issues = github_client.list_open_issues_with_body(repo_name=body.project, limit=200)
    except subprocess.CalledProcessError as e:
        raise srv._gh_error(e)

    # Compute sprint ticket counts to determine min_active_sprint
    issue_sprint_counts: dict[int, int] = {}
    label_set = set(body.labels)
    for iss in issues:
        for lbl in iss.get("labels", []):
            m = sprint_re_local.match(lbl["name"])
            if m:
                n = int(m.group(1))
                issue_sprint_counts[n] = issue_sprint_counts.get(n, 0) + 1
            if lbl["name"] in label_set:
                raise HTTPException(
                    400,
                    detail=f"Label {lbl['name']!r} still has open tickets — cannot delete",
                )

    active_sprint_nums = [n for n, count in issue_sprint_counts.items() if count > 0]
    min_active_sprint = min(active_sprint_nums) if active_sprint_nums else None

    # Reject any label whose sprint number >= min_active_sprint (or if no active sprints exist)
    for label in body.labels:
        m = sprint_re_local.match(label)
        if m:
            label_num = int(m.group(1))
            if min_active_sprint is None:
                raise HTTPException(
                    422,
                    detail=f"Cannot delete {label}: no active sprints exist, nothing is eligible for cleanup",
                )
            if label_num >= min_active_sprint:
                raise HTTPException(
                    422,
                    detail=(
                        f"Cannot delete {label}: sprint number {label_num} is not below "
                        f"the lowest active sprint ({min_active_sprint})"
                    ),
                )

    deleted = []
    errors = []
    for label in body.labels:
        try:
            github_client.delete_label(label, repo_name=body.project)
            deleted.append(label)
        except subprocess.CalledProcessError as e:
            errors.append(f"{label}: {e.stderr.strip() if e.stderr else str(e)}")

    github_client.invalidate("sprints:")
    invalidate_board(body.project)
    result: dict = {"ok": True, "deleted": deleted}
    if errors:
        result["errors"] = errors
    return result


@router.post("/api/sprints/cleanup-empty")
async def cleanup_empty_sprints(body: SprintCleanupBody):
    """Delete leading consecutive empty sprint labels from GitHub (issue #658).

    Only removes sprint-N labels that appear before the first sprint with ≥ 1 open
    ticket (in ascending number order). Trailing empty sprints — those after any
    sprint that has tickets — are preserved. If no sprint has tickets, nothing is
    deleted.
    """
    try:
        all_sprint_labels = github_client.list_sprint_labels(repo_name=body.project)
    except subprocess.CalledProcessError as e:
        raise _server()._gh_error(e)

    try:
        issues = github_client.list_open_issues_with_body(repo_name=body.project, limit=200)
    except subprocess.CalledProcessError as e:
        raise _server()._gh_error(e)

    sprint_re_any = re.compile(r"^sprint-(\d+)(?:\.\d+)?$")
    sprint_re_plain = re.compile(r"^sprint-(\d+)$")

    # Collect base sprint numbers that have ≥ 1 open ticket (plain or dotted sub-labels)
    active_base_nums: set[int] = set()
    for iss in issues:
        for lbl in iss.get("labels", []):
            m = sprint_re_any.match(lbl["name"])
            if m:
                active_base_nums.add(int(m.group(1)))

    # Iterate plain sprint-N labels in ascending order; collect consecutive leading empties
    plain_labels = sorted(
        [lbl for lbl in all_sprint_labels if sprint_re_plain.match(lbl)],
        key=lambda lbl: int(sprint_re_plain.match(lbl).group(1)),  # type: ignore[union-attr]
    )

    leading_empty: list[str] = []
    found_active = False
    for label in plain_labels:
        base_num = int(sprint_re_plain.match(label).group(1))  # type: ignore[union-attr]
        if base_num in active_base_nums:
            found_active = True
            break
        leading_empty.append(label)

    # AC3: if no sprint has tickets, nothing is eligible for cleanup
    if not found_active:
        leading_empty = []

    deleted = []
    errors = []
    for label in leading_empty:
        try:
            github_client.delete_label(label, repo_name=body.project)
            deleted.append(label)
        except subprocess.CalledProcessError as e:
            errors.append(f"{label}: {e.stderr.strip() if e.stderr else str(e)}")

    github_client.invalidate("sprints:")
    invalidate_board(body.project)
    result: dict = {"ok": True, "deleted": deleted}
    if errors:
        result["errors"] = errors
    return result


@router.delete("/api/sprints/{sprint_label}")
def delete_sprint(sprint_label: str, project: str):
    """Remove a sprint label from GitHub and unlabel all attached tickets.

    Does NOT delete the issues themselves — only the sprint label is removed.
    """
    srv = _server()
    if not srv._SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    if srv._is_sprint_running(srv._project_root_path(project), sprint_label):
        return JSONResponse(
            status_code=409,
            content={"error": "Sprint is currently running.", "suggestion": "Cancel the sprint first, then delete."},
        )

    project_root = srv._project_root_path(project)
    commander = srv._commander_dir(project_root)

    try:
        sprint_issues = srv._get_sprint_issues(project, sprint_label)
    except subprocess.CalledProcessError as e:
        raise srv._gh_error(e)

    from routers import sprint_history_service  # noqa: PLC0415 — snapshot history BEFORE label-strip (#805)
    sprint_history_service.record_deleted_sprint(sprint_label, project, sprint_issues, commander)
    errors: list[str] = []
    unlabelled_count = 0

    for iss in sprint_issues:
        try:
            github_client.update_labels(iss["number"], add=[], remove=[sprint_label], repo_name=project)
            unlabelled_count += 1
        except subprocess.CalledProcessError as e:
            errors.append(f"#{iss['number']} failed: {e.stderr.strip()}")

    try:
        github_client.delete_label(sprint_label, repo_name=project)
    except subprocess.CalledProcessError as e:
        errors.append(f"Label deletion failed: {e.stderr.strip()}")

    (commander / "sprints" / f"{sprint_label}-state.json").unlink(missing_ok=True)
    (commander / "sprints" / f"{sprint_label}-goal.txt").unlink(missing_ok=True)

    for _ck in ("open_issues_body:", "open_issues:", "issues:", "sprints:"):
        github_client.invalidate(_ck)

    invalidate_board(project)
    result: dict = {
        "deleted_label": sprint_label,
        "unlabelled_count": unlabelled_count,
        **({"errors": errors} if errors else {}),
    }
    return result
