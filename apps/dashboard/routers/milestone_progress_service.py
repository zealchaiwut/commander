"""Active-milestone progress logic for the home page and project header (issue #880).

Surfaces a compact ``X done + Y UAT / Z total`` progress summary for each
project's *active* milestone so users see high-level goal tracking without
opening the Roadmap tab.

The active milestone is the one the Roadmap tab persists under the project
``roadmap`` settings key (``active_milestone``, an int milestone number — see
``roadmap_service`` from issue #878). When no active milestone is set the
project has no indicator, so ``active_milestone_progress`` returns ``None`` and
the frontend renders the card/header unchanged (AC: no active milestone -> no
indicator).

Progress is ``done + UAT`` over the milestone's issues, classified with the same
``classify_issue`` rule the sprint board uses (closed/UAT-approved = done; UAT
label = uat). GitHub reads go through a small cached ``gh api`` helper kept local
to this module; settings go through ``settings_repo``. ``github_client`` and
``settings_repo`` are module-level names so tests can inject fakes.
"""
from __future__ import annotations

import json
import subprocess
import time
from typing import Optional

import github_client  # sibling on path (apps/dashboard) — classify_issue + repo resolution
import projects as projects_module  # sibling on path (apps/dashboard)
import settings_repo as _settings  # sibling on path (services/sprint_manager)

# Project-settings key the Roadmap tab writes (shared with roadmap_service, #878).
ROADMAP_KEY = "roadmap"

# 30s read cache, matching github_client's CACHE_TTL, so home/header polling does
# not spend a GitHub call per render.
_CACHE_TTL = 30.0
_cache: dict[str, tuple[float, object]] = {}


def _cached(key: str, fn):
    now = time.monotonic()
    hit = _cache.get(key)
    if hit is not None and now - hit[0] < _CACHE_TTL:
        return hit[1]
    val = fn()
    _cache[key] = (now, val)
    return val


def _gh_api(path: str) -> list:
    """Return the parsed JSON array from ``gh api <path>`` (empty list on shape miss)."""
    result = subprocess.run(
        ["gh", "api", path], capture_output=True, text=True, check=True
    )
    data = json.loads(result.stdout)
    return data if isinstance(data, list) else []


def _active_milestone_number(repo: str) -> Optional[int]:
    """Return the persisted active milestone number for *repo*, or None."""
    settings = _settings.get_setting_scoped("project", ROADMAP_KEY, project=repo) or {}
    number = settings.get("active_milestone")
    return number if isinstance(number, int) else None


def _list_milestones(repo: str) -> list:
    return _cached(
        f"milestones:{repo}",
        lambda: _gh_api(f"repos/{repo}/milestones?state=all&per_page=100"),
    )


def _milestone_issues(repo: str, number: int) -> list:
    """Every issue (open + closed) assigned to milestone ``number``; PRs filtered out."""
    items = _cached(
        f"milestone_issues:{repo}:{number}",
        lambda: _gh_api(f"repos/{repo}/issues?milestone={number}&state=all&per_page=100"),
    )
    return [i for i in items if "pull_request" not in i]


def compute_progress(issues: list[dict]) -> dict:
    """Count milestone issues by board column.

    ``done`` is closed/UAT-approved issues; ``uat`` is issues still in the UAT
    column (the dashboard's awaiting-sign-off state). ``total`` is every issue
    in the milestone. ``completed`` (done + uat) is the progress numerator.
    """
    done = uat = 0
    for issue in issues:
        column = github_client.classify_issue(issue)
        if column == "done":
            done += 1
        elif column == "uat":
            uat += 1
    total = len(issues)
    return {"done": done, "uat": uat, "total": total, "completed": done + uat}


def active_milestone_progress(repo: str) -> Optional[dict]:
    """Return the active milestone's name + progress for *repo*, or None.

    None when the project has no active milestone set, the milestone no longer
    exists, or GitHub is unreachable — in every case the caller shows no
    indicator and renders normally.
    """
    number = _active_milestone_number(repo)
    if number is None:
        return None
    try:
        milestones = _list_milestones(repo)
        milestone = next((m for m in milestones if m.get("number") == number), None)
        if milestone is None:
            return None
        issues = _milestone_issues(repo, number)
    except Exception:
        return None

    progress = compute_progress(issues)
    return {
        "number": number,
        "title": milestone.get("title", ""),
        "done": progress["done"],
        "uat": progress["uat"],
        "total": progress["total"],
        "completed": progress["completed"],
    }


def home_milestones() -> dict:
    """Map each project slug to its active milestone progress (or None).

    One entry per configured project so the home page can attach the indicator
    to the matching card. A failing project degrades to ``None`` (no indicator)
    rather than failing the whole payload.
    """
    result: dict[str, Optional[dict]] = {}
    for proj in projects_module.load_projects():
        repo = proj["repo"]
        slug = repo.split("/")[-1]
        try:
            result[slug] = active_milestone_progress(repo)
        except Exception:
            result[slug] = None
    return result
