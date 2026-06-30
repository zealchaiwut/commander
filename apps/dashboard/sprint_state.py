"""Canonical sprint lifecycle read accessor (issue #1091).

DB is the sole source of truth. This module provides exactly one public
function: current(label) → canonical lifecycle string.

No disk reads, no GitHub label lookups, no fallback logic.
See docs/architecture/sprint-lifecycle.md for the read contract.
"""
from __future__ import annotations

import db as _db


def current(label: str, project: str | None = None) -> str:
    """Return the canonical lifecycle state for a sprint label.

    Reads the sprints DB row (scoped by project when given) and passes its
    raw state through canonical_lifecycle(). DB is the only source — no
    plan.json, no GitHub label inference, no fallback chain.

    Scoping by project keeps same-label sprints in different repos
    (e.g. sprint-9 in crux vs commander) from reading each other's state.

    Returns "unknown" if the sprint has no row in the DB.
    """
    row = _db.get_sprint(label, project=project)
    return _db.canonical_lifecycle(row["state"] if row else None)
