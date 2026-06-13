"""Active-milestone progress for the home cards and sprint nav (issue #880).

Surfaces a compact `X done + Y UAT / Z total` progress count for a project's
single *active* milestone so the operator sees goal tracking on the home page
and in the project header without opening the Roadmap tab.

Kept self-contained on purpose: the active-milestone designation is read from
the same project-settings key the Roadmap tab writes (issue #878), but milestone
data is fetched directly through ``github_client`` so this feature works whether
or not the roadmap service module is present. ``classify_issue`` is the same rule
the sprint board and roadmap use, so "done" / "UAT" counts stay consistent.

``gh`` and ``_settings`` are module-level names so tests can inject in-memory
fakes (see routers/AGENTS.md).
"""
from __future__ import annotations

from typing import Optional

import github_client as gh  # sibling on path (apps/dashboard)
import settings_repo as _settings  # sibling on path (services/sprint_manager)

# Project-settings key holding {"active_milestone": int|None, "order": [int]} —
# shared with the Roadmap tab (issue #878). We only read "active_milestone".
ROADMAP_KEY = "roadmap"


# ── progress ──────────────────────────────────────────────────────────────────

def compute_progress(issues: list[dict]) -> dict:
    """Return progress counts for a milestone's issues.

    `completed` is done + UAT (UAT is the dashboard's "done" state pending human
    sign-off — see CLAUDE.md). `total` is every issue in the milestone.
    """
    done = uat = 0
    for issue in issues:
        column = gh.classify_issue(issue)
        if column == "done":
            done += 1
        elif column == "uat":
            uat += 1
    total = len(issues)
    return {"done": done, "uat": uat, "total": total, "completed": done + uat}


def format_label(progress: dict) -> str:
    """The compact indicator text: ``X done + Y UAT / Z total`` (AC2)."""
    return (
        f"{progress['done']} done + {progress['uat']} UAT "
        f"/ {progress['total']} total"
    )


# ── github access (isolated so tests can monkeypatch) ────────────────────────

def _active_number(repo: str) -> Optional[int]:
    """The active milestone number persisted for this project, or None."""
    data = _settings.get_setting_scoped("project", ROADMAP_KEY, project=repo) or {}
    number = data.get("active_milestone")
    return int(number) if number is not None else None


def _fetch_milestone(repo: str, number: int) -> Optional[dict]:
    """One milestone by number (title etc.), or None if it can't be fetched."""
    try:
        return gh._json("api", f"repos/{repo}/milestones/{number}")
    except Exception:
        return None


def _fetch_milestone_issues(repo: str, number: int) -> list[dict]:
    """All issues (any state) belonging to the milestone; [] on failure."""
    try:
        result = gh._json(
            "api", "--paginate",
            f"repos/{repo}/issues?milestone={number}&state=all",
        )
        return result if isinstance(result, list) else []
    except Exception:
        return []


# ── public API ────────────────────────────────────────────────────────────────

def active_milestone_progress(repo: str) -> Optional[dict]:
    """Active milestone name + live progress for *repo*, or None when the
    project has no active milestone (AC3 — caller renders no indicator)."""
    number = _active_number(repo)
    if number is None:
        return None
    milestone = _fetch_milestone(repo, number)
    if not milestone:
        return None
    progress = compute_progress(_fetch_milestone_issues(repo, number))
    return {
        "number": number,
        "title": milestone.get("title", ""),
        "progress": progress,
        "label": format_label(progress),
    }
