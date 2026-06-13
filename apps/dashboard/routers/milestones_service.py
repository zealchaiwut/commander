"""Service logic for the milestones router (issue #877).

Resolves project slugs via the shared ``projects`` module and reads/writes the
local milestones + issues mirror via the shared ``db`` module — the same module
objects the monolith uses, so test patches against ``db`` / ``projects`` apply
here too. GitHub REST calls go through the ``github_milestones`` module.

Read path: serve milestones from the mirror; before the first sync (empty
mirror) fall back to a live GitHub fetch. Write path: hit GitHub live and upsert
the mirror so the change is reflected within the same request cycle.
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path
from typing import Optional

from fastapi import HTTPException

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
if str(_DASHBOARD_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_DASHBOARD_ROOT))

import db  # noqa: E402
import github_milestones  # noqa: E402
import projects as projects_module  # noqa: E402


def _resolve_repo(slug: str) -> str:
    """Resolve a project slug to its full ``owner/repo`` path, or 404."""
    try:
        all_projects = projects_module.load_projects()
    except Exception:
        all_projects = []
    matched = next(
        (p for p in all_projects
         if p["repo"].split("/")[-1] == slug or p["repo"] == slug),
        None,
    )
    if matched is None:
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
    return matched["repo"]


def list_milestones(slug: str, state: Optional[str] = None) -> list[dict]:
    """Return milestones for a project from the mirror.

    Falls back to a live GitHub fetch when the mirror is empty (before first
    sync). After a sync the mirror serves the read with zero GitHub quota.
    """
    repo = _resolve_repo(slug)
    mirrored = db.get_mirrored_milestones(repo, state=state)
    if mirrored:
        return mirrored
    # Empty mirror -> fall back to a live GitHub fetch (bootstrap path).
    live = github_milestones.fetch_milestones_live(repo)
    if state is not None:
        live = [m for m in live if m.get("state") == state]
    return live


def create_milestone(slug: str, body: dict) -> dict:
    """Create a milestone on GitHub for a project. `title` is required."""
    repo = _resolve_repo(slug)
    title = (body or {}).get("title")
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    return github_milestones.create_milestone(
        repo,
        title=title,
        description=body.get("description"),
        due_on=body.get("due_on"),
    )


def update_milestone(slug: str, number: int, body: dict) -> dict:
    """Edit a milestone on GitHub (title/description/due_on/state)."""
    repo = _resolve_repo(slug)
    body = body or {}
    return github_milestones.update_milestone(
        repo,
        number,
        title=body.get("title"),
        description=body.get("description"),
        due_on=body.get("due_on"),
        state=body.get("state"),
    )


def close_milestone(slug: str, number: int) -> dict:
    """Close a milestone on GitHub (PATCH state=closed)."""
    repo = _resolve_repo(slug)
    return github_milestones.close_milestone(repo, number)


def list_issues(slug: str, state: Optional[str] = None) -> list[dict]:
    """Return mirrored issues for a project, each carrying its `milestone` field.

    Served from the local mirror — zero GitHub quota post-sync (issue #877 AC-6).
    """
    repo = _resolve_repo(slug)
    return db.get_mirrored_issues(repo, state=state)
