"""Roadmap business logic (issue #878).

Builds the per-milestone card model for the Roadmap tab and owns the project-
scoped persistence of the two view settings the AC calls out: the active
milestone and the card order. Ticket progress is `(done + UAT) / total`,
sourced from the issue mirror and classified with the same `classify_issue`
rule the sprint board uses.

Route handlers in ``roadmap.py`` stay thin and delegate here (see
routers/AGENTS.md). GitHub access goes through ``github_client``; settings go
through ``settings_repo`` — both are module-level names so tests can inject
in-memory fakes.
"""
from __future__ import annotations

import subprocess
from typing import Any, Optional

import github_client as gh  # sibling on path (apps/dashboard)
import settings_repo as _settings  # sibling on path (services/sprint_manager)

try:  # structured logging is best-effort; never let its absence break a render
    from services.logging import log as _log
except Exception:  # pragma: no cover - defensive import guard
    _log = None

# gh CLI failures we degrade on instead of 500ing: command non-zero exit and
# a missing/uninstalled gh binary.
_GH_UNAVAILABLE = (subprocess.CalledProcessError, FileNotFoundError)

# Project-settings key holding {"active_milestone": int|None, "order": [int]}.
ROADMAP_KEY = "roadmap"

# Distinguishes "field omitted" from an explicit None (clearing the active card).
_SENTINEL = object()


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


# ── settings ────────────────────────────────────────────────────────────────

def _load_settings(repo: str) -> dict:
    return dict(_settings.get_setting_scoped("project", ROADMAP_KEY, project=repo) or {})


def _save_settings(repo: str, data: dict) -> dict:
    _settings.set_setting("project", ROADMAP_KEY, data, project=repo)
    return data


def set_active(repo: str, number: Optional[int]) -> dict:
    """Mark exactly one milestone active (or clear with None). Persisted."""
    data = _load_settings(repo)
    data["active_milestone"] = number
    return _save_settings(repo, data)


def set_order(repo: str, order: list[int]) -> dict:
    """Persist the card display order (list of milestone numbers)."""
    data = _load_settings(repo)
    data["order"] = list(order)
    return _save_settings(repo, data)


def update_settings(repo: str, active_milestone: Any = _SENTINEL,
                    order: Any = None) -> dict:
    """Update active milestone and/or order in one write (PUT settings)."""
    data = _load_settings(repo)
    if active_milestone is not _SENTINEL:
        data["active_milestone"] = active_milestone
    if order is not None:
        data["order"] = list(order)
    return _save_settings(repo, data)


def _apply_order(cards: list[dict], order: list[int]) -> list[dict]:
    """Sort cards by the persisted order; unlisted cards fall to the end,
    stable by milestone number."""
    pos = {num: idx for idx, num in enumerate(order)}
    return sorted(cards, key=lambda c: (pos.get(c["number"], len(order)), c["number"]))


# ── roadmap model ─────────────────────────────────────────────────────────────

def _to_card(milestone: dict, issues: list[dict], active: Optional[int]) -> dict:
    return {
        "number": milestone["number"],
        "title": milestone.get("title", ""),
        "description": milestone.get("description") or "",
        "due_on": milestone.get("due_on"),
        "state": milestone.get("state", "open"),
        "progress": compute_progress(issues),
        "active": milestone["number"] == active,
    }


def _warn_github_unavailable(repo: str, what: str, exc: Exception) -> None:
    """Best-effort structured warning when a gh read fails (phase-1 fallback)."""
    if _log is None:
        return
    stderr = getattr(exc, "stderr", "") or ""
    try:
        _log.warn(
            "roadmap_github_unavailable",
            f"roadmap {what} fetch failed for {repo}; serving cached/empty roadmap",
            project=repo,
            stderr_tail=str(stderr)[-500:],
        )
    except Exception:  # pragma: no cover - logging must never raise here
        pass


def get_roadmap(repo: str) -> dict:
    """Assemble the Roadmap payload: open cards (ordered), closed history rows,
    and the persisted active/order settings.

    Reads come from GitHub with a fallback: ``list_issues_with_milestone`` is
    backed by the issues mirror when available, and milestone metadata is
    gh-only. When the gh CLI is unavailable (``CalledProcessError`` /
    ``FileNotFoundError``) we never 500 — we serve whatever the mirror has (or
    an empty roadmap) and flag the payload ``stale`` so the UI can show a
    "couldn't refresh from GitHub" notice instead of a hard failure.
    """
    # Settings are local (no GitHub) — always available, even when gh is down.
    settings = _load_settings(repo)
    active = settings.get("active_milestone")
    order = settings.get("order", []) or []

    stale = False
    error: Optional[str] = None

    try:
        milestones = gh.list_milestones(repo, state="all")
    except _GH_UNAVAILABLE as exc:
        milestones, stale, error = [], True, "github_unavailable"
        _warn_github_unavailable(repo, "milestone", exc)

    try:
        issues = gh.list_issues_with_milestone(repo)
    except _GH_UNAVAILABLE as exc:
        issues, stale, error = [], True, "github_unavailable"
        _warn_github_unavailable(repo, "issue", exc)

    by_milestone: dict[int, list[dict]] = {}
    for issue in issues:
        ms = issue.get("milestone")
        if ms:
            by_milestone.setdefault(ms["number"], []).append(issue)

    open_cards, closed_cards = [], []
    for milestone in milestones:
        card = _to_card(milestone, by_milestone.get(milestone["number"], []), active)
        (closed_cards if card["state"] == "closed" else open_cards).append(card)

    payload: dict = {
        "milestones": _apply_order(open_cards, order),
        "closed_milestones": closed_cards,
        "active_milestone": active,
        "order": order,
        "stale": stale,
    }
    if error:
        payload["error"] = error
    return payload


# ── milestone mutations (thin pass-through to github_client) ─────────────────

def create_milestone(repo: str, title: str, description: str = "",
                     due_on: Optional[str] = None) -> dict:
    return gh.create_milestone(title, description=description, due_on=due_on,
                               repo_name=repo)


def update_milestone(repo: str, number: int, fields: dict) -> dict:
    return gh.update_milestone(number, fields, repo_name=repo)


def close_milestone(repo: str, number: int) -> dict:
    return gh.set_milestone_state(number, "closed", repo_name=repo)


def reopen_milestone(repo: str, number: int) -> dict:
    return gh.set_milestone_state(number, "open", repo_name=repo)
