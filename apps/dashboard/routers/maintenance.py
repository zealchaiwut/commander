"""Maintenance endpoints — calibration cache rebuild (issue #1332).

New endpoints belong in routers/<area>.py, never in server.py
(COMMANDER_GATE_MONOLITH, issue #761).
"""
from fastapi import APIRouter, Request, Response

from . import maintenance_service, sprints_csv_service

router = APIRouter(tags=["maintenance"])


@router.get("/api/maintenance/sprints/export")
def export_sprints(project: str):
    """GET /api/maintenance/sprints/export?project=<owner/repo>.

    Download one project's `sprints` lifecycle rows as CSV — a snapshot to load
    into another instance (e.g. PRD → UAT) for testing. Read-only.
    """
    csv_text = sprints_csv_service.export_sprints_csv(project)
    fname = "sprints-" + (project.split("/")[-1] or "project") + ".csv"
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post("/api/maintenance/sprints/import")
async def import_sprints(project: str, request: Request, mode: str = "merge"):
    """POST /api/maintenance/sprints/import?project=<owner/repo>&mode=merge.

    Body is raw CSV (text/csv) from a prior export. Upserts rows into the given
    project by sprint label; mode=replace wipes the project's rows first.
    Returns {ok, inserted, updated, before, after, errors}.
    """
    raw = await request.body()
    csv_text = raw.decode("utf-8", errors="replace")
    return sprints_csv_service.import_sprints_csv(project, csv_text, mode=mode)


@router.post("/api/maintenance/calibration/rebuild")
def post_rebuild_calibration(project: str):
    """POST /api/maintenance/calibration/rebuild?project=<slug>.

    Clears and rebuilds the calibration cache by rescanning all sprint-*-state.json
    files under .commander/sprints/ and .commander/sprints/archive/.

    Returns {"total": N, "by_size": {"S": x, "M": y, "L": z, "XL": w}}.
    """
    return maintenance_service.do_rebuild(project, dry_run=False)
