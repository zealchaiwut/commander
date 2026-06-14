"""Pure advisor-agent logic — no I/O, fully unit-testable.

Scheduling, suggestion validation, and settings storage for the daily
next-build advisor (issue #881). I/O-coupled glue lives in
``apps/dashboard/routers/advisor_service.py``.

Design split (mirrors sprint_scheduler):
  * Pure logic (``should_fire``, ``validate_suggestions``,
    ``validate_suggestion_structure``) has no I/O and is unit-tested directly.
  * Storage helpers persist through ``settings_repo`` so they ride the existing
    Neon-optional settings store with local JSON fallback.

The daily-fire trigger is an external POST to ``/api/advisor/tick`` (launchd /
cron), consistent with the scheduler-service model from issue #863.
"""
from __future__ import annotations

from typing import Optional

from services.sprint_manager import settings_repo

# Settings keys (project scope)
_TIME_KEY = "advisor_time"              # {"value": "09:00"}
_LAST_FIRED_KEY = "advisor_last_fired"  # {"date": "2026-06-14"}
_RESET_ON_DEMAND_KEY = "advisor_reset_on_demand"  # {"value": true}

VALID_SCOPES = frozenset({"S", "M", "L"})
SUGGESTION_MIN = 3
SUGGESTION_MAX = 5


def should_fire(
    scheduled_time: Optional[str],
    now_hhmm: str,
    last_fired_date: Optional[str],
    today: str,
) -> bool:
    """Return True when the daily advisor should run right now.

    Fires only when:
    - A non-empty scheduled time is configured.
    - The current minute matches it (HH:MM equality).
    - The advisor has not already fired today.

    There is intentionally no catch-up logic: if the dashboard was down
    at the scheduled time, the next fire is at the same time tomorrow.
    """
    if not scheduled_time or not scheduled_time.strip():
        return False
    if now_hhmm != scheduled_time.strip():
        return False
    if last_fired_date == today:
        return False
    return True


def validate_suggestion_structure(s: dict) -> None:
    """Validate a single suggestion dict. Raises ValueError describing the problem."""
    for field in ("pitch", "rationale", "milestone", "scope"):
        if field not in s:
            raise ValueError(f"Suggestion missing required field: '{field}'")
    scope = s["scope"]
    if scope not in VALID_SCOPES:
        raise ValueError(
            f"Invalid scope {scope!r}; must be one of {sorted(VALID_SCOPES)}"
        )


def validate_suggestions(suggestions: list) -> list:
    """Validate count (3-5) and structure of all suggestions. Raises ValueError."""
    n = len(suggestions)
    if n < SUGGESTION_MIN:
        raise ValueError(
            f"Advisor produced {n} suggestion(s); minimum is {SUGGESTION_MIN}"
        )
    if n > SUGGESTION_MAX:
        raise ValueError(
            f"Advisor produced {n} suggestion(s); maximum is {SUGGESTION_MAX}"
        )
    for s in suggestions:
        validate_suggestion_structure(s)
    return suggestions


# ── Settings helpers ──────────────────────────────────────────────────────────

def get_advisor_time(project: str) -> str:
    """Return the project's configured daily advisor run time, or '' if unset."""
    stored = settings_repo.get_setting_scoped("project", _TIME_KEY, project) or {}
    value = stored.get("value", "")
    return str(value).strip() if value else ""


def set_advisor_time(project: str, value: Optional[str]) -> str:
    """Persist the daily advisor run time. Returns the stored value."""
    canonical = str(value).strip() if value else ""
    settings_repo.set_setting("project", _TIME_KEY, {"value": canonical}, project)
    return canonical


def get_advisor_last_fired(project: str) -> Optional[str]:
    stored = settings_repo.get_setting_scoped("project", _LAST_FIRED_KEY, project) or {}
    return stored.get("date")


def set_advisor_last_fired(project: str, date: str) -> None:
    settings_repo.set_setting("project", _LAST_FIRED_KEY, {"date": date}, project)


def get_advisor_reset_on_demand(project: str) -> bool:
    """Return whether an on-demand run resets the daily-fire clock (default off)."""
    stored = settings_repo.get_setting_scoped(
        "project", _RESET_ON_DEMAND_KEY, project
    ) or {}
    return bool(stored.get("value", False))
