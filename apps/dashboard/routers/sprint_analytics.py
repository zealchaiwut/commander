"""Sprint analytics and metrics routes extracted from server.py (issue #1252).

Handles: estimate-summary, estimate, outcome, estimate-vs-actual,
estimates/batch, calibration, metrics/sprints. All business logic is in
sprint_analytics_service.py; route handlers here are thin delegators.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request

try:
    from . import sprint_analytics_service as _svc
except ImportError:
    import importlib.util as _ilu
    from pathlib import Path as _Path
    _spec = _ilu.spec_from_file_location(
        "sprint_analytics_service",
        _Path(__file__).parent / "sprint_analytics_service.py",
    )
    _svc = _ilu.module_from_spec(_spec)  # type: ignore[assignment]
    _spec.loader.exec_module(_svc)  # type: ignore[union-attr]

router = APIRouter(tags=["sprint_analytics"])


@router.get("/api/sprints/{sprint_label}/estimate-summary")
def get_sprint_estimate_summary(sprint_label: str, project: str):
    """Return rolled-up estimate summary for a sprint."""
    return _svc.get_estimate_summary(sprint_label, project)


@router.get("/api/sprints/{sprint_label}/estimate")
def get_sprint_estimate(sprint_label: str, project: str):
    """Return the sprint estimate JSON file content for sprint_label."""
    return _svc.get_sprint_estimate(sprint_label, project)


@router.get("/api/sprints/{sprint_label}/outcome")
def get_sprint_outcome(sprint_label: str, project: str):
    """Return frozen outcome data for a completed or stopped sprint."""
    return _svc.get_sprint_outcome(sprint_label, project)


@router.get("/api/sprints/{sprint_label}/estimate-vs-actual")
def get_sprint_estimate_vs_actual(sprint_label: str, project: str):
    """Return per-ticket estimate-vs-actual comparison for a finished sprint."""
    return _svc.get_estimate_vs_actual(sprint_label, project)


@router.get("/api/estimates/batch")
def get_estimates_batch(project: str, issues: str = ""):
    """Return summed estimated_hours and per-issue size/confidence."""
    return _svc.get_estimates_batch(project, issues)


@router.get("/api/calibration")
def get_calibration(project: str):
    """Return average actual time per size bucket (legacy calibration tab)."""
    return _svc.get_calibration(project)


@router.get("/api/metrics/sprints")
def get_sprint_metrics(request: Request):
    """Return per-sprint aggregate metrics across all registered projects."""
    today = datetime.now(tz=timezone.utc).date()
    raw_from = request.query_params.get("from")
    raw_to = request.query_params.get("to")

    if raw_from is None and raw_to is None:
        from_date = today - timedelta(days=30)
        to_date = today
    else:
        if raw_from is not None:
            try:
                from_date = datetime.strptime(raw_from, "%Y-%m-%d").date()
            except ValueError:
                raise HTTPException(
                    400, detail=f"Invalid 'from' date {raw_from!r} — expected YYYY-MM-DD"
                )
        else:
            from_date = today - timedelta(days=30)

        if raw_to is not None:
            try:
                to_date = datetime.strptime(raw_to, "%Y-%m-%d").date()
            except ValueError:
                raise HTTPException(
                    400, detail=f"Invalid 'to' date {raw_to!r} — expected YYYY-MM-DD"
                )
        else:
            to_date = today

    if from_date > to_date:
        raise HTTPException(
            400, detail=f"'from' date ({from_date}) must not be after 'to' date ({to_date})"
        )

    return _svc.get_sprint_metrics(from_date, to_date)
