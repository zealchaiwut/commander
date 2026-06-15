"""Milestone service logic for issues #877 and #879.

Issue #877: Service logic for the milestones router (CRUD + mirror).
Resolves project slugs via the shared ``projects`` module and reads/writes the
local milestones + issues mirror via the shared ``db`` module. GitHub REST calls
go through the ``github_milestones`` module.

Issue #879: Milestone selector business logic. Backs the milestone selector shown
in the BA bulk-create and single new-ticket dialogs. The Active milestone is owned
by the Roadmap tab (issue #878), persisted as a project-scoped setting.

GitHub access goes through ``github_client`` (legacy) and ``github_milestones``
(new CRUD); settings go through ``settings_repo``. Both are module-level names so
tests can inject fakes.
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
import github_client as gh  # noqa: E402
import github_milestones  # noqa: E402
import projects as projects_module  # noqa: E402
import settings_repo as _settings  # noqa: E402

# Same settings key the Roadmap tab writes the active milestone under (issue #878).
ROADMAP_KEY = "roadmap"


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


def _active_number(repo: str) -> Optional[int]:
    """Resolve the Active milestone number for a project (issue #879)."""
    data = _settings.get_setting_scoped("project", ROADMAP_KEY, project=repo) or {}
    num = data.get("active_milestone")
    return num if isinstance(num, int) else None


# ── Issue #879: Milestone selector (read-only) ──────────────────────────────

def list_milestones(slug: str, state: Optional[str] = None) -> dict | list:
    """List milestones for a project.

    For issue #877 (CRUD): returns a list of milestone dicts from mirror (fallback
    to live GitHub before first sync). Accepts optional state filter.

    For issue #879 (selector): when called without state, returns a dict with
    milestones list (each tagged with 'active') and the active milestone title.
    """
    repo = _resolve_repo(slug)

    # Issue #877: mirror read path (with optional state filter)
    mirrored = db.get_mirrored_milestones(repo, state=state)
    if mirrored:
        # If state was requested and we got results from mirror, return as list
        if state is not None:
            return mirrored
        # If no state filter, check if this looks like an issue #879 selector call
        # (no state param, returning full dict with active marker)
        active_num = _active_number(repo)
        out = []
        active_title: Optional[str] = None
        for m in mirrored:
            is_active = m.get("number") == active_num and active_num is not None
            if is_active:
                active_title = m.get("title")
            out.append({
                "number": m.get("number"),
                "title": m.get("title"),
                "active": bool(is_active),
            })
        return {"milestones": out, "active": active_title}

    # Empty mirror -> fall back to live GitHub fetch (bootstrap path, issue #877)
    live = github_milestones.fetch_milestones_live(repo)
    if state is not None:
        live = [m for m in live if m.get("state") == state]
        return live

    # For selector calls, augment live results with active markers (issue #879)
    active_num = _active_number(repo)
    out = []
    active_title: Optional[str] = None
    for m in live:
        is_active = m.get("number") == active_num and active_num is not None
        if is_active:
            active_title = m.get("title")
        out.append({
            "number": m.get("number"),
            "title": m.get("title"),
            "active": bool(is_active),
        })
    return {"milestones": out, "active": active_title}


# ── Issue #877: Milestone CRUD operations ───────────────────────────────────

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


# ── Issue #879: Bulk create milestone resolution ──────────────────────────────

def resolve_bulk_milestone(milestone: Optional[str], job: Optional[dict]) -> Optional[str]:
    """Resolve a bulk job's milestone selection (issue #879).

    The Sprint stage sends the chosen milestone title in the request body; fall
    back to the job's stored milestone for resilience (e.g. resumed sessions).
    An empty/blank value means "no milestone" (AC5). Kept here (not in the
    server monolith) so handlers stay thin and the logic is unit-testable.
    """
    chosen = milestone if milestone is not None else (job or {}).get("milestone")
    return ((chosen or "").strip()) or None
