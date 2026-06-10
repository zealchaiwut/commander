"""Canonical size → minutes mapping for the Commander estimator.

This is the SINGLE source of truth for every size→minutes conversion in the
codebase. `estimation_config.DEFAULT_ESTIMATION_CFG` and
`mis_sizing.CANONICAL_MINUTES` both import this map rather than redeclaring it,
so there is exactly one literal map (issue #766). To change the values, edit
this file.

Semantic (issue #766): a size's minutes is the FULL PIPELINE wall-clock time
for the ticket — coder + tester end-to-end (in-progress → UAT) — not isolated
agent effort and not human developer time.

  S  — trivial, single-file change          (~5 min)
  M  — multi-file or single-layer change    (~15 min)
  L  — multi-layer or new schema            (~30 min)
  XL — cross-cutting or uncertain scope     (>=90 min)
"""
from __future__ import annotations

SIZE_TO_MINUTES: dict[str, int] = {
    "S": 5,
    "M": 15,
    "L": 30,
    # XL raised 60→90 (issue #766): observed actuals for cross-cutting tickets
    # routinely exceed 60 min of full-pipeline wall-clock, so 60 understated XL.
    "XL": 90,
}

# Ordered pairs (minutes_threshold, size_letter) for reverse lookup.
# letter_from_minutes() assigns the letter whose threshold the value exceeds last.
SIZE_BUCKETS: list[tuple[int, str]] = [(5, "S"), (15, "M"), (30, "L"), (90, "XL")]


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
