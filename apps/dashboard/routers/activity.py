"""Activity / event endpoints (extracted from server.py, issue #794).

The activity feed surfaces consumed by the dashboard Activity tab. Service
logic lives in the sibling ``activity_service`` module.
"""
from typing import Optional

from fastapi import APIRouter

from . import activity_service

router = APIRouter(tags=["activity"])


@router.get("/api/agents")
def list_agents():
    return activity_service.list_agents()


@router.get("/api/events")
def list_events():
    return activity_service.list_events()


@router.get("/api/projects/{slug}/events")
def get_project_events(
    slug: str,
    source: Optional[str] = None,
    target: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 100,
    category: Optional[str] = None,
):
    """Return structured events for a project, newest-first.

    Filters: source (agent|dashboard|github), target (exact), since/until (ISO date),
    category (agent|error|gate|sprint — dispatch lifecycle events only), limit.
    404 — unknown project slug.
    400 — invalid source or category value.
    """
    return activity_service.get_project_events(slug, source, target, since, until, limit, category)
