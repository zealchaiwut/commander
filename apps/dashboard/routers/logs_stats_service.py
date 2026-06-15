"""Service logic for the Logs-tab per-ticket stats strip (issue #858).

Aggregates the durable ``agent_runs`` rows for one sprint into one row per
ticket carrying the coder duration, the tester duration, the combined token
total, and — for failed tickets — the failure class and a one-line message.
The failure class/message are read from the ``last-failure-<issue>.json``
sidecar the sprint manager writes (and deletes on success), so the strip
surfaces the same failure metadata the run-level batch does, without opening a
log file (AC4/AC6).

Everything is computed directly from the rows so the rendered numbers reconcile
with them. Durations and token totals degrade to ``None`` when never recorded so
the frontend can render a dash rather than a zero or an error (AC5). Read-only
and local — no GitHub call — so the strip renders instantly on run expand.

New endpoints belong in ``routers/``, never in ``server.py``
(COMMANDER_GATE_MONOLITH); this is the sibling logic module for the route
mounted on the ``/api/logs`` (log_search) router.
"""
from __future__ import annotations

import json
import logging
import sys as _sys
from pathlib import Path
from typing import Any, Optional

_log = logging.getLogger(__name__)

# apps/dashboard is on sys.path so ``import db`` resolves (mirrors the other
# router service modules and the server bootstrap).
_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
if str(_DASHBOARD_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_DASHBOARD_ROOT))

# Outcomes that mean the dispatch did not succeed (mirrors the values
# record_agent_finish stores: coder "failed"/"timeout", tester "fail").
FAILING_OUTCOMES = {"failed", "fail", "timeout", "error", "crashed", "rejected"}

# When no failure sidecar survives, derive a class from the failing stage so the
# row still names what broke (AC4). Keyed by (agent, outcome) → display class.
_DERIVED_CLASS = {
    ("coder", "failed"): "CODER_FAILED",
    ("coder", "timeout"): "CODER_TIMEOUT",
    ("tester", "fail"): "TESTER_REJECTED",
    ("tester", "failed"): "TESTER_REJECTED",
}

def _get_projects_base() -> Path:
    """Return the projects base directory from COMMANDER_BASE env var or ~/dev fallback.

    Logs the resolved path at DEBUG level so operators can confirm which root
    was picked (AC4). If COMMANDER_BASE is set but points to a non-existent
    path, emits a WARNING and falls back to ~/dev (AC5).
    """
    import os  # noqa: PLC0415
    default = Path.home() / "dev"
    env_val = os.environ.get("COMMANDER_BASE")
    if env_val:
        candidate = Path(env_val)
        if not candidate.exists():
            _log.warning(
                "COMMANDER_BASE=%s does not exist; falling back to %s",
                candidate,
                default,
            )
            _log.debug("projects_base resolved: %s (fallback)", default)
            return default
        _log.debug("projects_base resolved: %s (from COMMANDER_BASE)", candidate)
        return candidate
    _log.debug("projects_base resolved: %s (default)", default)
    return default


def _db():
    """Deferred import of the db module (honours a patched DB_PATH at call time)."""
    import db  # noqa: PLC0415
    return db


def _int_or_none(value: Any) -> Optional[int]:
    """Coerce a stored numeric to int, preserving None (a real NULL) and 0."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _runtime_dirs_for_project(project: Optional[str]) -> list[Path]:
    """Candidate ``.commander/runtime`` dirs that may hold failure sidecars.

    Covers both layouts the sprint manager writes from: the project root and the
    coder clone (nested ``<root>/coder`` and flat ``<slug>-coder``).
    """
    if not project:
        return []
    slug = project.split("/")[-1] if "/" in project else project
    root = _get_projects_base() / slug
    candidates = [
        root / ".commander" / "runtime",
        root / "coder" / ".commander" / "runtime",
        root.parent / f"{slug}-coder" / ".commander" / "runtime",
    ]
    return candidates


def _read_failure_sidecar(issue: int, runtime_dirs: list[Path]) -> Optional[dict]:
    """Return the failure sidecar payload for an issue, or None when absent."""
    for d in runtime_dirs:
        sc = d / f"last-failure-{issue}.json"
        try:
            if sc.exists():
                return json.loads(sc.read_text(encoding="utf-8"))
        except Exception:
            continue
    return None


def _failure_message(payload: dict) -> Optional[str]:
    """One-line message for the row: prefer the summary, fall back to detail."""
    summary = (payload.get("summary") or "").strip()
    if summary:
        return summary
    detail = (payload.get("detail") or "").strip()
    if detail:
        first = detail.splitlines()[0].strip()
        return first or None
    return None


def ticket_stats(
    sprint_label: str,
    project: Optional[str] = None,
    runtime_dirs: Optional[list[Path]] = None,
) -> dict[str, Any]:
    """Aggregate one sprint's ``agent_runs`` rows into per-ticket Logs-tab stats.

    Returns ``{"sprint_label": ..., "tickets": [...]}`` where each ticket item
    carries ``coder_seconds``, ``tester_seconds``, ``total_tokens`` (any of which
    may be ``None``), a ``failed`` flag, and ``failure_class`` / ``failure_message``
    (both ``None`` for passing tickets).
    """
    try:
        rows = _db().agent_runs_for_sprint(sprint_label)
    except Exception:
        rows = []

    if runtime_dirs is None:
        runtime_dirs = _runtime_dirs_for_project(project)

    # issue_number → accumulator. Durations/tokens track a "seen a real value"
    # flag so an all-NULL ticket stays None rather than collapsing to 0 (AC5).
    order: list[int] = []
    acc: dict[int, dict] = {}

    for r in rows:
        issue = _int_or_none(r.get("issue_number")) or 0
        if issue not in acc:
            order.append(issue)
            acc[issue] = {
                "coder": None, "tester": None, "tokens": None,
                "fail_stage": None,  # (agent, outcome) of the latest failing run
            }
        a = acc[issue]
        agent = (r.get("agent") or "").lower()
        outcome = (r.get("outcome") or "").lower()

        dur = _int_or_none(r.get("duration_seconds"))
        if dur is not None and agent in ("coder", "tester"):
            a[agent] = (a[agent] or 0) + max(0, dur)

        tok = _int_or_none(r.get("total_tokens"))
        if tok is not None:
            a["tokens"] = (a["tokens"] or 0) + tok

        if outcome in FAILING_OUTCOMES:
            a["fail_stage"] = (agent, outcome)

    tickets = []
    for issue in order:
        a = acc[issue]
        sidecar = _read_failure_sidecar(issue, runtime_dirs)
        failed = bool(sidecar) or a["fail_stage"] is not None

        failure_class = None
        failure_message = None
        if failed:
            if sidecar and sidecar.get("failure_class"):
                failure_class = sidecar.get("failure_class")
                failure_message = _failure_message(sidecar)
            elif a["fail_stage"]:
                agent, outcome = a["fail_stage"]
                failure_class = _DERIVED_CLASS.get((agent, outcome), outcome.upper())

        tickets.append({
            "issue_number": issue,
            "coder_seconds": a["coder"],
            "tester_seconds": a["tester"],
            "total_tokens": a["tokens"],
            "failed": failed,
            "failure_class": failure_class,
            "failure_message": failure_message,
        })

    tickets.sort(key=lambda t: t["issue_number"])
    return {"sprint_label": sprint_label, "tickets": tickets}
