"""Milestone selector business logic (issue #879).

Backs the milestone selector shown in the BA bulk-create and single new-ticket
dialogs: list the project's available GitHub milestones and resolve which one is
the project's Active milestone (the default selection).

The Active milestone is owned by the Roadmap tab (issue #878), persisted in the
project-scoped ``roadmap`` setting as ``active_milestone`` (a milestone number).
This module reads that setting read-only and never mutates it — the selector is
a consumer, not an owner.

GitHub access goes through ``github_client``; settings go through
``settings_repo``. Both are module-level names so tests can inject fakes.
"""
from __future__ import annotations

from typing import Optional

import github_client as gh  # sibling on path (apps/dashboard)
import settings_repo as _settings  # sibling on path (services/sprint_manager)

# Same settings key the Roadmap tab writes the active milestone under.
ROADMAP_KEY = "roadmap"


def _active_number(repo: str) -> Optional[int]:
    data = _settings.get_setting_scoped("project", ROADMAP_KEY, project=repo) or {}
    num = data.get("active_milestone")
    return num if isinstance(num, int) else None


def list_milestones(repo: str) -> dict:
    """Return the selector model for a project.

    ``milestones`` is the list of open milestones (each tagged with ``active``);
    ``active`` is the title of the Active milestone (the default selection) or
    None when the project has no Active milestone set.
    """
    active_num = _active_number(repo)
    milestones = gh.list_milestones(repo_name=repo, state="open")

    out = []
    active_title: Optional[str] = None
    for m in milestones:
        is_active = m.get("number") == active_num and active_num is not None
        if is_active:
            active_title = m.get("title")
        out.append({
            "number": m.get("number"),
            "title": m.get("title"),
            "active": bool(is_active),
        })

    return {"milestones": out, "active": active_title}
