"""Service logic for XL split suggestions (issue #1424).

Handles per-ticket per-sprint dismiss persistence and the classification logic
that decides whether a ticket qualifies for a split suggestion. No FastAPI
imports — keeps this module independently testable.

Dismiss storage: <project_root>/.commander/xl-dismissed-<sprint_label>.json
Format: a JSON array of integer issue numbers.
"""
from __future__ import annotations

import json
from pathlib import Path


def _xl_dismiss_path(project_root: Path, sprint_label: str) -> Path:
    commander = project_root / ".commander"
    return commander / f"xl-dismissed-{sprint_label}.json"


def load_xl_dismissed(project_root: Path, sprint_label: str) -> set[int]:
    """Return the set of issue numbers dismissed for this sprint."""
    path = _xl_dismiss_path(project_root, sprint_label)
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {int(n) for n in data}
    except (json.JSONDecodeError, OSError, ValueError):
        return set()


def save_xl_dismissed(project_root: Path, sprint_label: str, dismissed: set[int]) -> None:
    """Persist the full set of dismissed issue numbers for this sprint."""
    path = _xl_dismiss_path(project_root, sprint_label)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(dismissed)), encoding="utf-8")


def is_xl_suggestion(size: str | None, estimated_minutes: int, threshold: int) -> bool:
    """Return True when a ticket qualifies for a split suggestion.

    A ticket qualifies when it is sized XL OR when its estimated_minutes
    meets or exceeds the per-project threshold.
    """
    if size == "XL":
        return True
    return estimated_minutes >= threshold


def compute_minutes_saved(xl_minutes: list[int]) -> int:
    """Estimate wall-clock minutes saved by splitting all flagged tickets.

    Assumes each XL ticket is split into two equal parallel sub-tickets.
    The effective time is halved; savings per ticket = minutes // 2.
    Total savings = sum of per-ticket savings.
    """
    return sum(m // 2 for m in xl_minutes)
