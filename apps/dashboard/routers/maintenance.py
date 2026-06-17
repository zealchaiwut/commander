"""Maintenance endpoints — calibration cache rebuild (issue #1332).

New endpoints belong in routers/<area>.py, never in server.py
(COMMANDER_GATE_MONOLITH, issue #761).
"""
from fastapi import APIRouter

from . import maintenance_service

router = APIRouter(tags=["maintenance"])


@router.post("/api/maintenance/calibration/rebuild")
def post_rebuild_calibration(project: str):
    """POST /api/maintenance/calibration/rebuild?project=<slug>.

    Clears and rebuilds the calibration cache by rescanning all sprint-*-state.json
    files under .commander/sprints/ and .commander/sprints/archive/.

    Returns {"total": N, "by_size": {"S": x, "M": y, "L": z, "XL": w}}.
    """
    return maintenance_service.do_rebuild(project, dry_run=False)
