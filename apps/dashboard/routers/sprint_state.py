"""Canonical sprint lifecycle read accessor (issue #1091).

``sprint_state.current(label)`` is the sole sanctioned way to read a sprint's
lifecycle state per ``docs/architecture/sprint-lifecycle.md``.

Contract:
- Zero disk reads (no plan.json, no filesystem access of any kind)
- Zero GitHub label lookups
- Zero fallback logic — DB is the only source
- Returns ``None`` when the sprint has no row in the ``sprints`` table
"""
from __future__ import annotations


def current(label: str, project: str | None = None) -> str | None:
    """Return canonical lifecycle state for sprint *label* from DB only.

    Performs zero disk reads and zero GitHub lookups.
    Returns None if the sprint is not in the sprints table.

    Pass *project* (owner/repo) to scope the lookup — sprint labels are unique
    only per repo, so an unscoped read can return another project's sprint.
    """
    import db  # noqa: PLC0415 — deferred to respect patched DB_PATH at call time
    row = db.get_sprint(label, project=project or None)
    if row is None:
        return None
    return db.canonical_lifecycle(row.get("state"))
