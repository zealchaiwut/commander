"""Service logic for the sprint sign-off router (issue #862).

A planned sprint enters a "pending sign-off" governance gate that blocks
dispatch until an explicit approval is recorded. This module owns the two
state transitions out of that gate:

- ``approve_sprint`` — record the approver + ISO-8601 timestamp in plan.json,
  clear the pending gate, and append an activity-log entry.
- ``reject_sprint`` — dissolve the sprint (delete its GitHub label, which
  returns every ticket to the backlog), remove the local sprint files, and
  append a rejection entry to the activity log.

The durable sign-off state, project-root resolution, GitHub client, and the
activity-log writer all live in ``server.py``; this service reaches them
through a deferred import at request time (the same pattern as
``sprints_service``) to avoid the import-time circular dependency.
"""
from __future__ import annotations

import sys as _sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

# server.py is a top-level module on the dashboard path; make sure it resolves.
_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
if str(_DASHBOARD_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_DASHBOARD_ROOT))

from sprint_label_re import SPRINT_LABEL_RE  # noqa: E402

_SPRINT_LABEL_RE = SPRINT_LABEL_RE


def _server():
    """Deferred import of the monolith — safe at request time."""
    import server  # noqa: PLC0415 — intentional late import (see module docstring)
    return server


def _validate_label(sprint_label: str) -> None:
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")


def approve_sprint(project: str, sprint_label: str) -> dict:
    """Approve a pending sprint: clear the gate, stamp approver + timestamp."""
    _validate_label(sprint_label)
    srv = _server()
    project_root = srv._project_root_path(project)

    state = srv._sprint_signoff_state(project_root, sprint_label)
    if state == "approved":
        # Idempotent: a second approval is a no-op, not an error.
        plan = srv._read_plan_json(project_root, sprint_label) or {}
        signoff = plan.get("signoff", {})
        return {
            "ok": True, "state": "approved", "already_approved": True,
            "approver": signoff.get("approver"),
            "approved_at": signoff.get("approved_at"),
        }
    if state != "pending":
        raise HTTPException(
            409, detail=f"Sprint {sprint_label} is not awaiting sign-off",
        )

    approver = srv._dashboard_actor()
    approved_at = datetime.now(timezone.utc).isoformat()
    srv._sprint_signoff_set_approved(project_root, sprint_label, approver, approved_at)

    srv._emit_dashboard_event(
        project=project or "dashboard",
        type="sprint_signoff_approved",
        target=sprint_label,
        detail={
            "sprint_label": sprint_label,
            "approver": approver,
            "approved_at": approved_at,
        },
        action_id=str(uuid.uuid4()),
    )
    return {
        "ok": True, "state": "approved",
        "approver": approver, "approved_at": approved_at,
    }


def uat_signoff_preview(project: str, sprint_label: str) -> dict:
    """Return what would be closed by a per-sprint UAT sign-off (dry run).

    Includes child sprint labels (sprint-N.1, .2, .3) per the sign-off contract.
    """
    _validate_label(sprint_label)
    srv = _server()
    project_root = srv._project_root_path(project)  # noqa: F841 (validates project exists)
    import github_client as _gc  # noqa: PLC0415
    issues = _gc.list_open_uat_issues_by_sprint_label(sprint_label, repo_name=project)
    return {
        "sprint_label": sprint_label,
        "count": len(issues),
        "issues": [{"number": i["number"], "title": i["title"], "url": i["url"]} for i in issues],
        "child_labels_included": True,
    }


def uat_signoff_apply(project: str, sprint_label: str) -> dict:
    """Close all open UAT tickets for this sprint label (and child labels).

    Calls approve_issue on each ticket, which adds UAT-approved and closes it.
    Child sprint labels (sprint-N.1, .2, .3) are included.
    """
    _validate_label(sprint_label)
    srv = _server()
    project_root = srv._project_root_path(project)  # noqa: F841 (validates project exists)
    import github_client as _gc  # noqa: PLC0415
    issues = _gc.list_open_uat_issues_by_sprint_label(sprint_label, repo_name=project)
    approved = []
    errors = []
    for iss in issues:
        try:
            _gc.approve_issue(iss["number"], repo_name=project)
            approved.append(iss["number"])
        except Exception as e:  # noqa: BLE001
            errors.append({"number": iss["number"], "error": str(e)})

    srv._emit_dashboard_event(
        project=project or "dashboard",
        type="sprint_uat_signed_off",
        target=sprint_label,
        detail={
            "sprint_label": sprint_label,
            "approved": approved,
            "errors": errors,
        },
        action_id=str(uuid.uuid4()),
    )
    return {
        "ok": not errors,
        "sprint_label": sprint_label,
        "approved": approved,
        "errors": errors,
        "count": len(approved),
        "child_labels_included": True,
    }


def reject_sprint(project: str, sprint_label: str) -> dict:
    """Reject a pending sprint: dissolve it and return tickets to the backlog."""
    _validate_label(sprint_label)
    srv = _server()
    project_root = srv._project_root_path(project)

    state = srv._sprint_signoff_state(project_root, sprint_label)
    if state != "pending":
        raise HTTPException(
            409, detail=f"Sprint {sprint_label} is not awaiting sign-off",
        )

    actor = srv._dashboard_actor()
    rejected_at = datetime.now(timezone.utc).isoformat()

    # Dissolve the sprint: deleting the GitHub label strips it from every open
    # ticket, returning them all to the backlog in one operation.
    try:
        srv.github_client.delete_label(sprint_label, repo_name=project)
    except Exception as e:  # noqa: BLE001 — surface GitHub failure to the caller
        raise HTTPException(502, detail=f"Failed to dissolve sprint: {e}")

    srv._sprint_signoff_cleanup_files(project_root, sprint_label)

    srv._emit_dashboard_event(
        project=project or "dashboard",
        type="sprint_signoff_rejected",
        target=sprint_label,
        detail={
            "sprint_label": sprint_label,
            "actor": actor,
            "rejected_at": rejected_at,
        },
        action_id=str(uuid.uuid4()),
    )
    return {"ok": True, "state": "rejected", "actor": actor, "rejected_at": rejected_at}
