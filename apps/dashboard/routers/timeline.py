"""Sprint timeline endpoint (issue #1146).

GET /api/sprints/{sprint_label}/timeline returns structured per-issue segment
data for the running-sprint pane. All segment times come from DB agent_runs
(no render-time disk reads); GitHub labels are the issue-status truth.
"""
from fastapi import APIRouter, HTTPException
from pathlib import Path
import re
import sys

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
if str(_DASHBOARD_ROOT) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_ROOT))

from . import timeline_service  # noqa: E402

router = APIRouter(tags=["sprints"])

from sprint_label_re import SPRINT_LABEL_RE

_SPRINT_LABEL_RE = SPRINT_LABEL_RE


@router.get("/api/sprints/{sprint_label}/timeline")
def get_sprint_timeline(sprint_label: str, project: str):
    """Return ordered segment list per issue for a sprint's running-pane timeline.

    Data shape:
      {sprint_started_at, server_now, issues: [...], wrap_up_estimate, projected_finish}

    Each issue includes: number, title, status (done|running|queued),
    calibrated_estimate, estimated_remaining (running only), segments (coder/tester).
    """
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(status_code=400, detail=f"Invalid sprint label: {sprint_label!r}")
    return timeline_service.get_timeline(sprint_label, project)
