"""Single source of truth for size↔minutes mapping.

SIZE_TO_MINUTES and SIZE_BUCKETS are the canonical constants; helpers
letter_from_minutes / minutes_from_letter derive the missing field from
the one that is present.
"""
from __future__ import annotations

SIZE_BUCKETS: list[str] = ["S", "M", "L", "XL"]

# Canonical minutes per size bucket.  Each value is the representative midpoint
# used for sprint-math and tooltip display.  Kept as a single source so that
# project.html, the estimator, and the live endpoint all agree.
SIZE_TO_MINUTES: dict[str, int] = {
    "S":  7,
    "M": 15,
    "L": 25,
    "XL": 40,
}

# Ascending breakpoints: a ticket with minutes < threshold belongs to the
# corresponding bucket.  Values chosen so that the midpoint of each range maps
# back to the correct letter via letter_from_minutes.
_THRESHOLDS: list[tuple[int, str]] = [
    (11, "S"),   # < 11 → S  (covers 1–10 min)
    (20, "M"),   # < 20 → M  (covers 11–19 min)
    (33, "L"),   # < 33 → L  (covers 20–32 min)
]


def minutes_from_letter(letter: str) -> int:
    """Return the canonical minutes for a size letter.

    Returns 0 for unknown letters so callers can safely do arithmetic.
    """
    return SIZE_TO_MINUTES.get(letter, 0)


def letter_from_minutes(minutes: int) -> str:
    """Return the size letter for a given minute count.

    Uses ascending threshold buckets — anything >= the last threshold is XL.
    """
    for threshold, letter in _THRESHOLDS:
        if minutes < threshold:
            return letter
    return "XL"
