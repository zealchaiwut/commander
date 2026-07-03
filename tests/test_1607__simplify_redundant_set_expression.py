"""Tests for issue #1607 — simplify redundant set expression in _all_sprint_label_names.

The expression `live | (mirror_sprint & live)` is set-algebra equivalent to `live`
because A ∪ (B ∩ A) == A. The test verifies that removing the dead sub-expression
does not change behavior.

AC1: The simplification returns `live` directly when live is non-empty.
AC2: The empty-live fallback (returning mirror_sprint) is unchanged.
AC3: All existing acceptance criteria for #1355 remain satisfied.
AC4: No new GitHub API calls are introduced.
AC5: All existing tests pass with no modifications to expectations.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "apps" / "dashboard"))
os.environ.setdefault("DB_PATH", str(_REPO_ROOT / "commander.db"))

import github_client as gc  # noqa: E402


# ── AC1: simplified return value equals live when live is non-empty ────────────

class TestSimplifiedReturnLive:
    """Verify that `return live` produces the same result as the old expression."""

    def test_live_non_empty_returns_live_only(self, monkeypatch):
        """When live is non-empty, the function returns live (not live | (mirror & live))."""
        # Mirror has sprint-66, sprint-67, sprint-99 (some not in live).
        monkeypatch.setattr(gc, "_mirror_labels",
                            lambda r: [{"name": "sprint-66", "color": ""},
                                       {"name": "sprint-67", "color": ""},
                                       {"name": "sprint-99", "color": ""}])
        # Live has only sprint-67, sprint-99 (sprint-66 was deleted).
        monkeypatch.setattr(gc, "_live_sprint_label_names",
                            lambda r: ["sprint-67", "sprint-99"])

        result = gc._all_sprint_label_names("owner/repo")

        # Result must include all live labels and nothing from mirror not in live.
        assert result == {"sprint-67", "sprint-99"}, (
            f"with live=['sprint-67', 'sprint-99'], expected result to be "
            f"{{sprint-67, sprint-99}}, got {result}"
        )
        # Verify sprint-66 (in mirror but not live) is excluded.
        assert "sprint-66" not in result

    def test_live_includes_empty_labels(self, monkeypatch):
        """Live labels not in mirror (empty sprint labels) still appear."""
        # Mirror has sprint-90 only.
        monkeypatch.setattr(gc, "_mirror_labels",
                            lambda r: [{"name": "sprint-90", "color": ""}])
        # Live has sprint-90 and also sprint-91 (an empty label with 0 issues).
        monkeypatch.setattr(gc, "_live_sprint_label_names",
                            lambda r: ["sprint-90", "sprint-91"])

        result = gc._all_sprint_label_names("owner/repo")

        assert result == {"sprint-90", "sprint-91"}, (
            f"empty live label sprint-91 must be included, got {result}"
        )

    def test_mixed_live_mirror_live_dominates(self, monkeypatch):
        """Verify the set algebra: any mirror label in live is included;
        any mirror label not in live is excluded (even if redundantly)."""
        # Scenario: live=[A,B,C] mirror=[B,C,D,E]
        # Old formula: live | (mirror & live) = {A,B,C} | {B,C} = {A,B,C}
        # New formula: live = {A,B,C}
        # They must be equal.
        monkeypatch.setattr(gc, "_mirror_labels",
                            lambda r: [{"name": "sprint-2", "color": ""},  # B
                                       {"name": "sprint-3", "color": ""},  # C
                                       {"name": "sprint-4", "color": ""},  # D
                                       {"name": "sprint-5", "color": ""}])  # E
        monkeypatch.setattr(gc, "_live_sprint_label_names",
                            lambda r: ["sprint-1", "sprint-2", "sprint-3"])  # A,B,C

        result = gc._all_sprint_label_names("owner/repo")

        assert result == {"sprint-1", "sprint-2", "sprint-3"}
        # D and E must not appear.
        assert "sprint-4" not in result
        assert "sprint-5" not in result


# ── AC2: empty-live fallback is unchanged ─────────────────────────────────────

class TestEmptyLiveFallback:
    """Verify that when live is empty, mirror is used as fallback (unchanged)."""

    def test_empty_live_falls_back_to_mirror(self, monkeypatch):
        """When _live_sprint_label_names returns [], mirror is used unfiltered."""
        # Mirror has sprint-66, sprint-90.
        monkeypatch.setattr(gc, "_mirror_labels",
                            lambda r: [{"name": "sprint-66", "color": ""},
                                       {"name": "sprint-90", "color": ""}])
        # Live is empty (GitHub failure or no sprints yet).
        monkeypatch.setattr(gc, "_live_sprint_label_names", lambda r: [])

        result = gc._all_sprint_label_names("owner/repo")

        # Both mirror labels must appear.
        assert result == {"sprint-66", "sprint-90"}

    def test_no_mirror_and_empty_live(self, monkeypatch):
        """When both mirror and live are empty/None, result is empty."""
        # Mirror is None (DB unavailable).
        monkeypatch.setattr(gc, "_mirror_labels", lambda r: None)
        # Live is empty.
        monkeypatch.setattr(gc, "_live_sprint_label_names", lambda r: [])

        result = gc._all_sprint_label_names("owner/repo")

        assert result == set()


# ── AC3: zombie sprint fix remains intact ─────────────────────────────────────

class TestZombieSprintFixPreserved:
    """Verify that the zombie-sprint fix from #1355 continues to work."""

    def test_zombie_label_still_filtered_with_simplified_expression(self, monkeypatch):
        """Deleted label (in mirror, absent from live) is still filtered."""
        # Stale mirror has deleted sprint-66.
        monkeypatch.setattr(gc, "_mirror_labels",
                            lambda r: [{"name": "sprint-66", "color": ""},
                                       {"name": "sprint-67", "color": ""}])
        # Live: sprint-66 deleted, only sprint-67 exists.
        monkeypatch.setattr(gc, "_live_sprint_label_names",
                            lambda r: ["sprint-67"])

        result = gc._all_sprint_label_names("owner/repo")

        # sprint-66 must NOT appear.
        assert 66 not in {int(n.split("-")[1]) for n in result if n.startswith("sprint-")}
        # sprint-67 must appear.
        assert 67 in {int(n.split("-")[1]) for n in result if n.startswith("sprint-")}

    def test_multiple_zombies_all_filtered(self, monkeypatch):
        """Multiple deleted labels are all suppressed."""
        # Mirror has old deleted sprints.
        stale = [{"name": f"sprint-{n}", "color": ""} for n in (50, 60, 70, 99)]
        monkeypatch.setattr(gc, "_mirror_labels", lambda r: stale)
        # Only sprint-99 is live now.
        monkeypatch.setattr(gc, "_live_sprint_label_names", lambda r: ["sprint-99"])

        result = gc._all_sprint_label_names("owner/repo")

        nums = {int(n.split("-")[1]) for n in result if n.startswith("sprint-")}
        assert nums == {99}


# ── AC4: no extra GitHub API calls ────────────────────────────────────────────

class TestQuotaUnchanged:
    """Verify no new GitHub API calls are introduced."""

    def test_no_extra_json_calls(self, monkeypatch):
        """_all_sprint_label_names must not make unexpected _json calls."""
        extra_calls = []

        original_json = gc._json

        def tracking_json(*args, **kwargs):
            extra_calls.append(args)
            return original_json(*args, **kwargs)

        monkeypatch.setattr(gc, "_mirror_labels",
                            lambda r: [{"name": "sprint-90", "color": ""}])
        monkeypatch.setattr(gc, "_live_sprint_label_names", lambda r: ["sprint-90"])
        monkeypatch.setattr(gc, "_json", tracking_json)

        gc._all_sprint_label_names("owner/repo")

        assert extra_calls == [], (
            f"_all_sprint_label_names must not call _json; got {extra_calls}"
        )
