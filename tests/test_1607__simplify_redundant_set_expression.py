"""Tests for issue #1607 — simplify redundant set expression in _all_sprint_label_names.

The expression `live | (mirror_sprint & live)` is set-algebra equivalent to `live`
because A ∪ (B ∩ A) == A for any sets A, B.  Removing that dead sub-expression
must not change observable behaviour for any non-empty live set.

AC coverage:
  AC1 — `if live:` branch returns `live` directly (no redundant intersection).
  AC2 — `mirror_sprint` is NOT computed or referenced inside the `if live:` branch.
  AC3 — function output is identical to the old expression for all non-empty live sets.
  AC4 — no other logic in _all_sprint_label_names is altered.
  AC5 — all existing tests pass (enforced by running the full suite separately).
"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "apps" / "dashboard"))
os.environ.setdefault("DB_PATH", str(_REPO_ROOT / "commander.db"))

import github_client as gc  # noqa: E402


# ── AC1 / AC3: when live is non-empty the result equals live exactly ───────────

class TestLiveNonEmpty:
    """When _live_sprint_label_names returns labels, _all_sprint_label_names must
    return exactly that set — no additions from mirror (zombie-sprint guard)."""

    def test_returns_live_only_excludes_stale_mirror_label(self, monkeypatch):
        """Deleted label in mirror (not in live) must not appear in result."""
        monkeypatch.setattr(gc, "_mirror_labels",
                            lambda r: [{"name": "sprint-66", "color": ""},
                                       {"name": "sprint-67", "color": ""},
                                       {"name": "sprint-99", "color": ""}])
        monkeypatch.setattr(gc, "_live_sprint_label_names",
                            lambda r: ["sprint-67", "sprint-99"])

        result = gc._all_sprint_label_names("owner/repo")

        assert result == {"sprint-67", "sprint-99"}, (
            f"expected exactly live labels, got {result}"
        )
        assert "sprint-66" not in result, (
            "stale mirror label sprint-66 must be excluded when live is non-empty"
        )

    def test_live_label_absent_from_mirror_is_included(self, monkeypatch):
        """Live label not in mirror (empty sprint label) must still appear."""
        monkeypatch.setattr(gc, "_mirror_labels",
                            lambda r: [{"name": "sprint-90", "color": ""}])
        monkeypatch.setattr(gc, "_live_sprint_label_names",
                            lambda r: ["sprint-90", "sprint-91"])

        result = gc._all_sprint_label_names("owner/repo")

        assert result == {"sprint-90", "sprint-91"}, (
            f"live-only label sprint-91 must be included, got {result}"
        )

    def test_result_equals_set_algebra_equivalent(self, monkeypatch):
        """Verify A ∪ (B ∩ A) == A property is maintained: result must equal live."""
        live_labels = ["sprint-10", "sprint-11", "sprint-12"]
        mirror_labels = [
            {"name": "sprint-10", "color": ""},
            {"name": "sprint-11", "color": ""},
            {"name": "sprint-50", "color": ""},  # stale, not in live
        ]
        monkeypatch.setattr(gc, "_live_sprint_label_names", lambda r: live_labels)
        monkeypatch.setattr(gc, "_mirror_labels", lambda r: mirror_labels)

        result = gc._all_sprint_label_names("owner/repo")
        expected = set(live_labels)  # A ∪ (B ∩ A) == A

        assert result == expected, (
            f"result {result!r} must equal live set {expected!r}"
        )

    def test_mirror_none_returns_live(self, monkeypatch):
        """When mirror is unavailable (None), live is returned as-is."""
        monkeypatch.setattr(gc, "_mirror_labels", lambda r: None)
        monkeypatch.setattr(gc, "_live_sprint_label_names",
                            lambda r: ["sprint-5", "sprint-6"])

        result = gc._all_sprint_label_names("owner/repo")

        assert result == {"sprint-5", "sprint-6"}


# ── AC2: no reference to mirror_sprint inside the if-live branch ───────────────

class TestNoMirrorSprintInLiveBranch:
    """Source-level check: the `if live:` branch must not reference mirror_sprint."""

    def test_source_has_no_redundant_intersection(self):
        """The literal expression `mirror_sprint & live` must not appear in source."""
        source = inspect.getsource(gc._all_sprint_label_names)
        assert "mirror_sprint & live" not in source, (
            "Dead sub-expression `mirror_sprint & live` must be removed (AC2)"
        )

    def test_if_live_branch_returns_live_directly(self):
        """The simplified branch `return live` (without union) must exist."""
        source = inspect.getsource(gc._all_sprint_label_names)
        # The branch should contain 'return live' without any '|' on the same statement.
        lines = source.splitlines()
        return_live_lines = [l for l in lines if "return live" in l]
        assert return_live_lines, "Function must contain a `return live` statement (AC1)"
        # None of those lines should have the union operator.
        for line in return_live_lines:
            assert "|" not in line, (
                f"return live line must not contain '|': {line!r}"
            )


# ── Empty-live fallback (unchanged behaviour) ──────────────────────────────────

class TestEmptyLiveFallback:
    """When live is empty the function falls back to mirror_sprint (unchanged)."""

    def test_empty_live_returns_mirror_sprint_labels(self, monkeypatch):
        """Fall back to mirror when gh returns no labels."""
        monkeypatch.setattr(gc, "_live_sprint_label_names", lambda r: [])
        monkeypatch.setattr(gc, "_mirror_labels",
                            lambda r: [{"name": "sprint-88", "color": ""},
                                       {"name": "sprint-89", "color": ""},
                                       {"name": "enhancement", "color": ""}])

        result = gc._all_sprint_label_names("owner/repo")

        assert result == {"sprint-88", "sprint-89"}, (
            f"fallback should include only sprint labels from mirror, got {result}"
        )
        assert "enhancement" not in result

    def test_empty_live_mirror_none_returns_empty(self, monkeypatch):
        """Both live and mirror empty → empty set returned."""
        monkeypatch.setattr(gc, "_live_sprint_label_names", lambda r: [])
        monkeypatch.setattr(gc, "_mirror_labels", lambda r: None)

        result = gc._all_sprint_label_names("owner/repo")

        assert result == set()
