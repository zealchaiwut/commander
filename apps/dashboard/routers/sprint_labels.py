"""Sprint label routes extracted from server.py (issue #1267).

GET  /api/open-issues          — return all open issues including body for conflict detection
POST /api/sprints/batch-labels — batch-move tickets to their target sprint labels
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _DASHBOARD_ROOT.parent.parent
_SERVICES_ROOT = _REPO_ROOT / "services" / "sprint_manager"
for _p in (str(_DASHBOARD_ROOT), str(_SERVICES_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import db  # noqa: E402
import github_client  # noqa: E402
from .board_cache import invalidate_board  # noqa: E402
from .board_service import SPRINT_LABEL_FORMAT_ERROR  # noqa: E402

router = APIRouter()

_SPRINT_LABEL_RE = re.compile(r"^sprint-\d+(\.\d+)?$")
_SPRINT_LABEL_RE_ALL = re.compile(r"^sprint-\d+(\.\d+)?$")


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


@router.get("/api/open-issues")
def get_open_issues():
    """Return all open issues including body for conflict detection.  Cached 30 s."""
    try:
        issues = github_client.list_open_issues_with_body(limit=200)
    except subprocess.CalledProcessError as e:
        raise _server()._gh_error(e)
    return [
        {
            "number": iss["number"],
            "title": iss["title"],
            "labels": iss.get("labels", []),
            "body": iss.get("body") or "",
            "url": iss.get("url", ""),
            "state": iss.get("state", "open"),
        }
        for iss in issues
    ]


class BatchLabelChange(BaseModel):
    issue_num: int
    sprint_label: str  # e.g. "sprint-3" or "backlog"


class BatchLabelsBody(BaseModel):
    changes: list[BatchLabelChange]
    project: Optional[str] = None


@router.post("/api/sprints/batch-labels")
async def batch_sprint_labels(body: BatchLabelsBody):
    """Batch-move tickets to their target sprint labels.

    Accepts: {"changes": [{"issue_num": N, "sprint_label": "sprint-3"}, ...], "project": "owner/repo"}
    Returns: {"applied": N, "failed": N, "errors": [...]}
    """
    applied = 0
    failed = 0
    errors: list[str] = []

    repo = body.project or None

    for change in body.changes:
        raw = change.sprint_label.strip()
        if raw == "" or raw == "backlog":
            label_to_assign: str | None = None
        elif _SPRINT_LABEL_RE.match(raw):
            label_to_assign = raw
        else:
            errors.append(f"#{change.issue_num}: {raw!r} — {SPRINT_LABEL_FORMAT_ERROR}")
            failed += 1
            continue

        try:
            github_client.assign_sprint_by_label(
                change.issue_num, label_to_assign, repo_name=repo,
            )
            applied += 1
        except subprocess.CalledProcessError as e:
            err_msg = e.stderr.strip() if e.stderr else str(e)
            errors.append(f"#{change.issue_num}: {err_msg}")
            failed += 1
        except Exception as e:
            errors.append(f"#{change.issue_num}: {e}")
            failed += 1

    if applied > 0:
        invalidate_board(github_client.get_repo_for_operation(repo))
    return {"applied": applied, "failed": failed, "errors": errors}
