"""Tests for issue #1607: Simplify redundant set expression in _all_sprint_label_names.

The zombie-sprint fix in _all_sprint_label_names returns `live | (mirror_sprint & live)`
when live is non-empty. By set algebra (A ∪ (B ∩ A) == A), this is always equal to `live`.
This test verifies the refactor removes the dead sub-expression while preserving behavior.

Acceptance Criteria:
- AC1: The expression is replaced with `return live` in the `if live:` branch
- AC2: `mirror_sprint` is not computed/referenced inside the `if live:` branch
- AC3: Function produces identical output for all non-empty live sets
- AC4: No other logic is altered
- AC5: All existing tests pass after the change
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "apps" / "dashboard"))
os.environ.setdefault("DB_PATH", str(_REPO_ROOT / "commander.db"))

import github_client as gc  # noqa: E402


def test_simplified_all_sprint_label_names__behavior_unchanged(monkeypatch):
    """AC3: The function produces identical output for all non-empty live label sets.

    Verify that removing the redundant `(mirror_sprint & live)` intersection does not
    change the behavior — the function should still return exactly `live` when live is
    non-empty, regardless of what mirror contains (including labels absent from live).
    """
    # Setup: mirror has labels not in live (stale/deleted), and labels that are in live
    monkeypatch.setattr(gc, "_mirror_labels",
                        lambda r: [{"name": "sprint-55", "color": ""},  # stale, not in live
                                   {"name": "sprint-66", "color": ""},  # stale, not in live
                                   {"name": "sprint-90", "color": ""},  # in live
                                   {"name": "sprint-95", "color": ""}]) # in live
    # Live: only sprint-90 and sprint-95 exist (sprint-55/66 were deleted)
    live_labels = ["sprint-90", "sprint-95"]
    monkeypatch.setattr(gc, "_live_sprint_label_names", lambda r: live_labels)

    result = gc._all_sprint_label_names("owner/repo")

    # Must equal exactly live, not live | (mirror_sprint & live)
    assert result == set(live_labels), (
        f"Expected {set(live_labels)}, got {result} — "
        "function must return live directly when live is non-empty"
    )
    # Verify stale labels are excluded (the whole point of the zombie fix)
    assert "sprint-55" not in result
    assert "sprint-66" not in result


def test_simplified_all_sprint_label_names__fallback_still_works(monkeypatch):
    """AC1/AC4: When live is empty, fallback to mirror still works unchanged.

    Verify that the else/fallback branch (computing mirror_sprint for empty live)
    continues to work correctly — no logic was altered there.
    """
    # Mirror has sprint labels
    monkeypatch.setattr(gc, "_mirror_labels",
                        lambda r: [{"name": "sprint-88", "color": ""},
                                   {"name": "sprint-99", "color": ""}])
    # Live is empty (gh failure or no sprints yet)
    monkeypatch.setattr(gc, "_live_sprint_label_names", lambda r: [])

    result = gc._all_sprint_label_names("owner/repo")

    # Must return mirror_sprint (the fallback behavior) when live is empty
    assert "sprint-88" in result
    assert "sprint-99" in result


def test_simplified_all_sprint_label_names__empty_orphan_union_still_works(monkeypatch):
    """AC4: Empty (orphan) sprint labels from live are still included via union.

    Verify that the union with live still captures empty labels (zero-issue sprints)
    that appear in the live registry but not in the mirror — this was the original
    issue #1C-B fix that must not regress.
    """
    # Mirror: only has sprint-91 (sprint-92 has 0 issues, so no mirror entry)
    monkeypatch.setattr(gc, "_mirror_labels",
                        lambda r: [{"name": "sprint-91", "color": ""}])
    # Live: has both sprint-91 and empty sprint-92
    monkeypatch.setattr(gc, "_live_sprint_label_names",
                        lambda r: ["sprint-91", "sprint-92"])

    result = gc._all_sprint_label_names("owner/repo")

    # Both must be present (union of live, which includes the empty label)
    assert "sprint-91" in result
    assert "sprint-92" in result, (
        "Empty (orphan) sprint label must surface via live union"
    )
