"""Changelog index endpoint (issue #1860).

Routes:
  GET /api/projects/{slug}/changelog?env=uat|prd&limit=N
"""
from typing import Optional

from fastapi import APIRouter

from . import changelog_service

router = APIRouter(tags=["changelog"])


@router.get("/api/projects/{slug}/changelog")
def get_project_changelog(
    slug: str,
    env: Optional[str] = None,
    limit: Optional[int] = None,
):
    """Return changelog entries from docs/changelog/{uat,prd}/, newest-first.

    env   — filter to "uat" or "prd"; omit for both.
    limit — cap result count.
    """
    clone_root = changelog_service.resolve_clone_root(slug)
    return changelog_service.list_changelog(clone_root, env=env, limit=limit)
