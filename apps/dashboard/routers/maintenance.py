"""Maintenance endpoints — test cleanup (#1267) and calibration/sprint CSV (#1332).

Routes in this module:
  POST /api/maintenance/tests/cleanup
  GET  /api/maintenance/sprints/export
  POST /api/maintenance/sprints/import
  POST /api/maintenance/calibration/rebuild

New endpoints belong in routers/<area>.py, never in server.py
(COMMANDER_GATE_MONOLITH, issue #761).
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from . import maintenance_service, sprints_csv_service

# ── Path setup ───────────────────────────────────────────────────────────────
_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent  # apps/dashboard/
_REPO_ROOT = _DASHBOARD_ROOT.parent.parent  # repo root
_SERVICES_ROOT = _REPO_ROOT / "services" / "sprint_manager"
for _p in (str(_DASHBOARD_ROOT), str(_SERVICES_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import projects as _projects_module  # noqa: E402

_PROJECTS_BASE = Path.home() / "dev"

# ── Optional prune_test_files module ─────────────────────────────────────────
try:
    import prune_test_files as _prune_test_files  # noqa: E402
    _PRUNE_TESTS_AVAILABLE = True
except ImportError:
    _prune_test_files = None  # type: ignore[assignment]
    _PRUNE_TESTS_AVAILABLE = False

router = APIRouter(tags=["maintenance"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _resolve_project_slug(slug: str) -> str:
    """Resolve slug to owner/repo string; raise 404 if not found."""
    try:
        all_projects = _projects_module.load_projects()
    except Exception:
        all_projects = []
    matched = next(
        (p for p in all_projects
         if p["repo"].split("/")[-1] == slug or p["repo"] == slug),
        None,
    )
    if matched is None:
        raise HTTPException(status_code=404, detail=f"Project {slug!r} not found")
    return matched["repo"]


def _project_root_path(repo: str) -> Path:
    """Return the project root directory for a given repo (owner/repo)."""
    slug = repo.split("/")[-1] if "/" in repo else repo
    return _PROJECTS_BASE / slug


def _maintenance_repo_root(project: str) -> Path:
    """Resolve the git clone that holds tests/ for maintenance actions."""
    slug = _resolve_project_slug(project.strip())
    project_root = _project_root_path(slug)
    repo_root = _REPO_ROOT
    for candidate in (project_root / "uat", project_root, repo_root):
        if (candidate / "tests").is_dir():
            return candidate.resolve()
    return project_root.resolve()


# ── Request body ──────────────────────────────────────────────────────────────

class _TestCleanupBody(BaseModel):
    project: str
    keep: int = 100
    dry_run: bool = True


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/api/maintenance/tests/cleanup")
def post_test_files_cleanup(body: _TestCleanupBody):
    """Remove old ``tests/test_*.py`` files, keeping the N most recent in git history."""
    if not _PRUNE_TESTS_AVAILABLE:
        raise HTTPException(status_code=503, detail="prune_test_files module unavailable")

    project = (body.project or "").strip()
    if not project:
        raise HTTPException(status_code=400, detail="project is required")

    keep = max(int(body.keep or 100), 0)
    repo_root = _maintenance_repo_root(project)
    if not (repo_root / "tests").is_dir():
        return {
            "repo_root": str(repo_root),
            "keep_limit": keep,
            "kept": [],
            "remove": [],
            "kept_count": 0,
            "remove_count": 0,
            "total_count": 0,
            "dry_run": body.dry_run,
            "deleted": [],
            "failed": [],
        }

    try:
        return _prune_test_files.run_prune(repo_root, keep=keep, dry_run=body.dry_run)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Test cleanup failed: {exc}") from exc


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
