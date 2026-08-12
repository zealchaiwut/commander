"""Tests for issue #675: Support Sprint Rerun as Independent Sub-Sprint.

AC-1 tests retained: _next_sprint_sublabel, _SPRINT_LABEL_RE, _sprint_label_sort_key.
AC-2 through AC-8 tests removed in issue #2250 — they tested the
POST /api/sprints/{label}/rerun endpoint which was deleted with sprint_run.py.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "dashboard"))
from server import (
    _next_sprint_sublabel,
    _SPRINT_LABEL_RE,
    _sprint_label_sort_key,
)


# ── AC-1: sub-sprint number auto-increments ──────────────────────────────────

class TestSubsprintAutoIncrement:
    """_next_sprint_sublabel increments correctly for both plain and dotted labels."""

    def test_plain_to_dot_one(self):
        # 51 → 51.1 when sprint-51.1 does not yet exist
        assert _next_sprint_sublabel("sprint-51", set()) == "sprint-51.1"

    def test_dot_one_to_dot_two(self):
        # 51.1 → 51.2 (flat sibling, not nested)
        assert _next_sprint_sublabel("sprint-51.1", set()) == "sprint-51.2"

    def test_skips_occupied_suffix(self):
        # If sprint-51.1 already exists, next is sprint-51.2
        assert _next_sprint_sublabel("sprint-51", {"sprint-51.1"}) == "sprint-51.2"

    def test_dotted_sibling_increments(self):
        # Running rerun on sprint-51.2 yields sprint-51.3
        assert _next_sprint_sublabel("sprint-51.2", set()) == "sprint-51.3"

    def test_invalid_label_raises(self):
        with pytest.raises(ValueError):
            _next_sprint_sublabel("not-a-sprint", set())


# ── AC-1: SPRINT_LABEL_RE rejects double-dotted labels ───────────────────────

class TestSprintLabelRERejectsTwoLevels:
    """sprint-15.1.1 must be rejected by _SPRINT_LABEL_RE (one dotted level max)."""

    def test_rejects_two_level_dotted(self):
        assert not _SPRINT_LABEL_RE.match("sprint-15.1.1")

    def test_rejects_three_level_dotted(self):
        assert not _SPRINT_LABEL_RE.match("sprint-15.1.2.3")

    def test_accepts_plain(self):
        assert _SPRINT_LABEL_RE.match("sprint-15")

    def test_accepts_one_level_dotted(self):
        assert _SPRINT_LABEL_RE.match("sprint-15.1")


# ── AC-1: sort key returns two-component tuple ────────────────────────────────

class TestSprintLabelSortKeyTwoTuple:
    """_sprint_label_sort_key must return (N, 0) for plain, (N, M) for dotted."""

    def test_plain_returns_zero_suffix(self):
        assert _sprint_label_sort_key("sprint-51") == (51, 0)

    def test_dotted_returns_suffix(self):
        assert _sprint_label_sort_key("sprint-51.1") == (51, 1)
        assert _sprint_label_sort_key("sprint-51.2") == (51, 2)
