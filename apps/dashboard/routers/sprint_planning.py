"""Sprint planning routes extracted from server.py (issue #1267).

GET  /api/sprint-planning/issues — return all open issues with sprint assignment and size estimate
POST /api/sprint-planning/assign — assign or remove a sprint label on an issue
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _DASHBOARD_ROOT.parent.parent
_SERVICES_ROOT = _REPO_ROOT / "services" / "sprint_manager"
for _p in (str(_DASHBOARD_ROOT), str(_SERVICES_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import db  # noqa: E402
import github_client  # noqa: E402
from routers.logs_service import broadcast  # noqa: E402
from .board_cache import invalidate_board  # noqa: E402

router = APIRouter()

from sprint_label_re import SPRINT_LABEL_RE  # noqa: E402

_SPRINT_LABEL_RE = SPRINT_LABEL_RE
_SPRINT_LABEL_RE_ALL = SPRINT_LABEL_RE


def _server():
    """Deferred import of the monolith — safe at request time, avoids circular import."""
    import server  # noqa: PLC0415
    return server


def _dashboard_actor() -> str:
    return os.environ.get("COMMANDER_USER", "dashboard")


def _emit_dashboard_event(project, type, target, detail, action_id):
    try:
        db.record_event(
            project=project,
            source="dashboard",
            actor=_dashboard_actor(),
            type=type,
            target=target,
            detail=detail,
            action_id=action_id,
        )
    except Exception:
        pass


class SprintAssignBody(BaseModel):
    issue: int
    sprint: Optional[int] = None        # None = remove all sprint labels (legacy)
    sprint_label: Optional[str] = None  # e.g. "sprint-15.1"; takes precedence over sprint


@router.get("/api/sprint-planning/issues")
def get_sprint_planning_issues():
    """Return all open issues with sprint assignment and size estimate.

    Cache TTL: 30s. Cache is invalidated after label mutations via POST /assign.
    """
    try:
        issues = github_client.list_open_issues_with_body(limit=200)
        sprints = github_client.list_sprints()
    except subprocess.CalledProcessError as e:
        raise _server()._gh_error(e)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))

    sprint_re_local = re.compile(r"^sprint-(\d+)$")

    result_issues = []
    for iss in issues:
        sprint_num = None
        for lbl in iss.get("labels", []):
            m = sprint_re_local.match(lbl["name"])
            if m:
                sprint_num = int(m.group(1))
                break

        size = _server()._sprint_estimate_size(iss)
        status = github_client.classify_issue(iss)

        result_issues.append({
            "number": iss["number"],
            "title": iss["title"],
            "labels": iss.get("labels", []),
            "sprint": sprint_num,
            "size": size,
            "status": status,
            "url": iss.get("url", ""),
        })

    return {
        "sprints": sprints,
        "issues": result_issues,
    }


@router.post("/api/sprint-planning/assign")
async def assign_sprint_label(body: SprintAssignBody):
    """Assign or remove a sprint label on an issue.

    Body: {"issue": 21, "sprint": 3}               — assigns sprint-3 (legacy)
    Body: {"issue": 21, "sprint_label": "sprint-15.1"} — assigns sprint-15.1 (dotted sub-label)
    Body: {"issue": 21, "sprint": null}             — removes all sprint-* labels

    On success: invalidates cache, broadcasts SSE sprint_plan_update, returns {"ok": true}.
    Creates sprint-N label if it doesn't exist.
    """
    action_id = str(uuid.uuid4())

    # Capture current sprint labels before the change for from_sprint
    try:
        _issue_data = github_client.get_issue(body.issue)
        _cur_labels = [lbl["name"] for lbl in _issue_data.get("labels", [])]
        _from_sprint = next(
            (lbl for lbl in _cur_labels if _SPRINT_LABEL_RE_ALL.match(lbl)), None
        ) or "backlog"
    except Exception:
        _from_sprint = None

    try:
        if body.sprint_label is not None:
            # Dotted or plain label string — use the string-based assign function
            label = body.sprint_label.strip() or None
            if label and not _SPRINT_LABEL_RE.match(label):
                raise HTTPException(400, detail=f"Invalid sprint_label: {label!r}")
            github_client.assign_sprint_by_label(body.issue, label)
        else:
            github_client.assign_sprint(body.issue, body.sprint)
        # Invalidate open_issues_body cache so next GET reflects the change
        github_client.invalidate("open_issues_body:")
        github_client.invalidate("open_issues:")
        github_client.invalidate("sprints:")
        github_client.invalidate("sprint_labels:")
    except subprocess.CalledProcessError as e:
        raise _server()._gh_error(e)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))

    _to_sprint: str | None
    if body.sprint_label is not None:
        _to_sprint = (body.sprint_label.strip() or None) or "backlog"
    elif body.sprint is not None:
        _to_sprint = f"sprint-{body.sprint}"
    else:
        _to_sprint = "backlog"

    _emit_dashboard_event(
        project="dashboard",
        type="ticket_moved",
        target=f"#{body.issue}",
        detail={"from_sprint": _from_sprint, "to_sprint": _to_sprint},
        action_id=action_id,
    )
    if _to_sprint and _to_sprint != "backlog" and _to_sprint != _from_sprint:
        _emit_dashboard_event(
            project="dashboard",
            type="label_added",
            target=f"#{body.issue}",
            detail={"label": _to_sprint},
            action_id=action_id,
        )
    if _from_sprint and _from_sprint != "backlog" and _from_sprint != _to_sprint:
        _emit_dashboard_event(
            project="dashboard",
            type="label_removed",
            target=f"#{body.issue}",
            detail={"label": _from_sprint},
            action_id=action_id,
        )

    await broadcast({"type": "update", "event": {"event_type": "sprint_plan_update"}})
    invalidate_board(github_client.get_repo_for_operation())
    return {"ok": True}
