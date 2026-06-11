"""Daily brief artifact store: generate once, serve instantly (issue #841).

Sits on top of the structured brief (:mod:`brief_service`, #839) and its LLM
summary (:mod:`brief_summary`, #840) and turns "the brief" into a persistent
per-``(project, date)`` artifact rather than something recomputed on every page
load.

Design:

* **One fixed daily window.** A brief dated ``D`` covers the 24h window
  ``[(D-1) anchor, D anchor)`` where ``anchor`` defaults to 6 AM (configurable
  via ``BRIEF_WINDOW_ANCHOR_HOUR``). So the brief you read on the morning of
  ``D`` summarises everything since the previous day's anchor — and exposes a
  "covering since" label, e.g. *"Jun 10, 6:00 AM"* (AC5, AC6).
* **Lazy generation, then store-and-serve.** The first request for the current
  brief day generates the artifact and persists it; subsequent loads return the
  stored artifact verbatim with no recomputation (AC2, AC3).
* **Past dates are store-only.** Browsing to a prior date returns its stored
  artifact, or — when none was ever stored — a clear empty state rather than a
  live recompute or an error (AC4, AC8).
* **Regenerate.** An explicit Regenerate rebuilds the artifact for the current
  date, refreshes the embedded summary, and advances the generation timestamp
  (AC7).

The artifact embeds the complete served object — window metadata, the structured
brief, and the summary — so a load is a single store read.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import brief_service
from . import brief_summary

# Storage scopes (keys into db.brief_artifacts).
_SCOPE_PROJECT = "project"
_SCOPE_HOME = "home"
_HOME_KEY = ""  # the home roll-up has no project component

_EMPTY_MESSAGE = "No brief available for this date"

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ── config: window anchor ─────────────────────────────────────────────────────

def _anchor_hour() -> int:
    """The configured anchor hour for the daily window (default 6 AM, AC6)."""
    raw = os.environ.get("BRIEF_WINDOW_ANCHOR_HOUR", "").strip()
    try:
        hour = int(raw)
    except (ValueError, TypeError):
        return 6
    if 0 <= hour <= 23:
        return hour
    return 6


# Read once at import; tests re-evaluate via ``_anchor_hour`` when overriding.
ANCHOR_HOUR = _anchor_hour()


def _db():
    """Deferred db import so a patched DB_PATH is honoured at call time."""
    import db  # noqa: PLC0415
    return db


def _now_iso() -> str:
    """Generation timestamp (UTC, second resolution). Seam — patched in tests."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


# ── window + label helpers (AC5, AC6) ─────────────────────────────────────────

def _parse_date(date: str) -> datetime:
    """Parse a ``YYYY-MM-DD`` brief date into a naive datetime at midnight."""
    return datetime.strptime(date, "%Y-%m-%d")


def daily_window(date: str) -> tuple[str, str]:
    """Return ``(start_iso, end_iso)`` for the 24h window of brief ``date``.

    The window for brief date ``D`` is ``[(D-1) anchor, D anchor)`` — a fixed
    24-hour span anchored at the configured hour (AC6).
    """
    end_dt = _parse_date(date).replace(hour=ANCHOR_HOUR)
    start_dt = end_dt - timedelta(days=1)
    return start_dt.strftime("%Y-%m-%dT%H:%M:%S"), end_dt.strftime("%Y-%m-%dT%H:%M:%S")


def _format_anchor(dt: datetime) -> str:
    """Format the window start like ``"Jun 10, 6:00 AM"`` (AC5)."""
    hour12 = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return f"{_MONTHS[dt.month - 1]} {dt.day}, {hour12}:{dt.minute:02d} {ampm}"


def covering_since_label(date: str) -> str:
    """The "covering since" label for brief ``date`` — the window's start (AC5)."""
    start_iso, _ = daily_window(date)
    return _format_anchor(datetime.strptime(start_iso, "%Y-%m-%dT%H:%M:%S"))


def current_brief_date() -> str:
    """Today's brief date. Seam — patched in tests for determinism."""
    return datetime.now(timezone.utc).date().isoformat()


def _normalize_date(date: Optional[str]) -> str:
    return (date or "").strip() or current_brief_date()


# ── generation ────────────────────────────────────────────────────────────────

def _generate_project_artifact(slug: str, date: str) -> dict:
    """Build the full project brief artifact for ``(slug, date)``.

    Assembles the structured brief over the 6 AM-anchored window and generates
    the LLM summary from it (with #840's deterministic fallback). Returns the
    artifact payload (no ``generated_at`` — the store stamps that).
    """
    start, end = daily_window(date)
    brief = brief_service.build_project_brief(slug, date=date, window=(start, end))
    summary = brief_summary.generate_project_summary(brief)
    return {
        "scope": _SCOPE_PROJECT,
        "project": slug,
        "date": date,
        "covering_since": covering_since_label(date),
        "window_start": start,
        "window_end": end,
        "summary": summary["summary"],
        "summary_source": summary["source"],
        "brief": brief,
    }


def _generate_home_artifact(date: str) -> dict:
    """Build the full home roll-up artifact for ``date`` (AC7 parity)."""
    start, end = daily_window(date)
    home = brief_service.build_home_brief(date=date, window=(start, end))

    project_summaries: list[dict] = []
    for p in home.get("projects") or []:
        slug = p.get("project")
        if not slug:
            continue
        try:
            project_summaries.append(brief_summary.generate_project_summary(p))
        except Exception:
            continue
    summary = brief_summary.generate_home_summary(home, project_summaries)

    return {
        "scope": _SCOPE_HOME,
        "project": _HOME_KEY,
        "date": date,
        "covering_since": covering_since_label(date),
        "window_start": start,
        "window_end": end,
        "summary": summary["summary"],
        "summary_source": summary["source"],
        "brief": home,
    }


# ── presentation ──────────────────────────────────────────────────────────────

def _present(payload: dict, generated_at: str) -> dict:
    """Shape a stored artifact into the served response (available=True)."""
    return {
        "scope": payload.get("scope"),
        "project": payload.get("project"),
        "date": payload.get("date"),
        "available": True,
        "covering_since": payload.get("covering_since"),
        "window_start": payload.get("window_start"),
        "window_end": payload.get("window_end"),
        "generated_at": generated_at,
        "summary": payload.get("summary"),
        "summary_source": payload.get("summary_source"),
        "brief": payload.get("brief"),
        "message": None,
    }


def _empty_state(scope: str, project: str, date: str) -> dict:
    """The clear empty state for a date with no stored brief (AC8)."""
    return {
        "scope": scope,
        "project": project,
        "date": date,
        "available": False,
        "covering_since": covering_since_label(date),
        "window_start": daily_window(date)[0],
        "window_end": daily_window(date)[1],
        "generated_at": None,
        "summary": None,
        "summary_source": None,
        "brief": None,
        "message": _EMPTY_MESSAGE,
    }


# ── public API ────────────────────────────────────────────────────────────────

def get_or_create_project_artifact(slug: str, date: Optional[str] = None,
                                   force: bool = False) -> dict:
    """Return the stored daily brief artifact for ``(slug, date)``.

    * ``force`` (Regenerate) rebuilds and re-stores, advancing the generation
      timestamp (AC7).
    * Otherwise a stored artifact is served verbatim (AC3).
    * When nothing is stored: the current brief day is lazily generated and
      stored (AC2); a past date returns the empty state without recomputing
      (AC4, AC8).
    """
    db = _db()
    d = _normalize_date(date)

    if force:
        return _generate_and_store(db, _SCOPE_PROJECT, slug, d,
                                   lambda: _generate_project_artifact(slug, d))

    stored = db.get_brief_artifact(_SCOPE_PROJECT, slug, d)
    if stored is not None and stored["payload"] is not None:
        return _present(stored["payload"], stored["generated_at"])

    if d == current_brief_date():
        return _generate_and_store(db, _SCOPE_PROJECT, slug, d,
                                   lambda: _generate_project_artifact(slug, d))

    return _empty_state(_SCOPE_PROJECT, slug, d)


def get_or_create_home_artifact(date: Optional[str] = None,
                                force: bool = False) -> dict:
    """Return the stored daily home roll-up artifact for ``date`` (AC7 parity)."""
    db = _db()
    d = _normalize_date(date)

    if force:
        return _generate_and_store(db, _SCOPE_HOME, _HOME_KEY, d,
                                   lambda: _generate_home_artifact(d))

    stored = db.get_brief_artifact(_SCOPE_HOME, _HOME_KEY, d)
    if stored is not None and stored["payload"] is not None:
        return _present(stored["payload"], stored["generated_at"])

    if d == current_brief_date():
        return _generate_and_store(db, _SCOPE_HOME, _HOME_KEY, d,
                                   lambda: _generate_home_artifact(d))

    return _empty_state(_SCOPE_HOME, _HOME_KEY, d)


def _generate_and_store(db, scope: str, project: str, date: str, build) -> dict:
    """Generate via ``build``, persist with a fresh timestamp, and present it."""
    payload = build()
    generated_at = db.set_brief_artifact(scope, project, date, payload,
                                         generated_at=_now_iso())
    return _present(payload, generated_at)
