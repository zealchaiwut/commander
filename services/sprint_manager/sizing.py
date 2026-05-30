"""Canonical size → minutes mapping for the Commander estimator.

This mapping is intentionally hardcoded. Per-project overrides are out of scope
(see issue #386 Out of Scope). To change the values, edit this file — it is the
single source of truth for all size→minutes conversions in the codebase.

Sizes represent agent effort time, not human developer time:
  S  — trivial, single-file change          (~5 min)
  M  — multi-file or single-layer change    (~15 min)
  L  — multi-layer or new schema            (~30 min)
  XL — cross-cutting or uncertain scope     (~60 min)
"""
from __future__ import annotations

SIZE_TO_MINUTES: dict[str, int] = {"S": 5, "M": 15, "L": 30, "XL": 60}

# Ordered pairs (minutes_threshold, size_letter) for reverse lookup.
# letter_from_minutes() assigns the letter whose threshold the value exceeds last.
SIZE_BUCKETS: list[tuple[int, str]] = [(5, "S"), (15, "M"), (30, "L"), (60, "XL")]


def minutes_from_letter(size: str) -> int:
    """Return canonical minutes for a size letter; 0 for unknown sizes."""
    return SIZE_TO_MINUTES.get(size, 0)


def letter_from_minutes(minutes: int) -> str:
    """Derive a size letter from a minutes value using SIZE_BUCKETS thresholds.

    Returns the largest bucket whose threshold is <= minutes, or "S" when
    minutes is below the first threshold.
    """
    result = "S"
    for threshold, letter in SIZE_BUCKETS:
        if minutes >= threshold:
            result = letter
    return result
