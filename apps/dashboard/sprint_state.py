"""Canonical sprint lifecycle read accessor (issue #1091).

DB is the sole source of truth. This module provides exactly one public
function: current(label) → canonical lifecycle string.

No disk reads, no GitHub label lookups, no fallback logic.
See docs/architecture/sprint-lifecycle.md for the read contract.
"""
from __future__ import annotations

import db as _db


def current(label: str) -> str:
    """Return the canonical lifecycle state for a sprint label.

    Reads the sprints DB row and passes its raw state through
    canonical_lifecycle(). DB is the only source — no plan.json,
    no GitHub label inference, no fallback chain.

    Returns "unknown" if the sprint has no row in the DB.
    """
    row = _db.get_sprint(label)
    return _db.canonical_lifecycle(row["state"] if row else None)
