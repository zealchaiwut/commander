"""Tests for issue #360: sprint re-run creates sub-label with unfinished tickets.

AC coverage retained (pure-Python helpers, still present after issue #2250 deleted
the POST /api/sprints/{label}/rerun endpoint):
- AC-1:  _next_sprint_sublabel increments correctly
- AC-2:  _SPRINT_LABEL_RE accepts both plain and dotted labels
- AC-5/6: _sprint_label_sort_key returns correct tuple
- AC-7:  _load_sprint_order handles dotted sub-labels
- AC-11/12: Frontend JS helpers (sprintLabelCompare, sprintLabelDisplay, smgmtRender)

TestRerunAllUatNoop, TestRerun409WhenRunning, TestRerunSubLabel, TestRerunTicketRouting
removed in issue #2250 — they tested POST /api/sprints/{label}/rerun which was deleted
with sprint_run.py.
"""
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "dashboard"))
from server import (
    _next_sprint_sublabel,
    _sprint_label_sort_key,
    _load_sprint_order,
    _SPRINT_LABEL_RE,
)

REPO_ROOT = Path(__file__).parent.parent
APP_JS = REPO_ROOT / "apps" / "dashboard" / "static" / "app.js"


# ── AC-1/5/6: _next_sprint_sublabel ──────────────────────────────────────────

class TestNextSprintSublabel:
    """_next_sprint_sublabel() must increment suffix correctly."""

    def test_plain_sprint_produces_dot_one(self):
        assert _next_sprint_sublabel("sprint-15", set()) == "sprint-15.1"

    def test_plain_sprint_one(self):
        assert _next_sprint_sublabel("sprint-1", set()) == "sprint-1.1"

    def test_dot_one_produces_dot_two(self):
        assert _next_sprint_sublabel("sprint-15.1", set()) == "sprint-15.2"

    def test_dot_three_produces_dot_four(self):
        assert _next_sprint_sublabel("sprint-15.3", set()) == "sprint-15.4"

    def test_sprint_n_five_produces_n_six(self):
        # AC-6: sprint-N.5 → sprint-N.6
        assert _next_sprint_sublabel("sprint-25.5", set()) == "sprint-25.6"

    def test_different_base_number(self):
        assert _next_sprint_sublabel("sprint-100.9", set()) == "sprint-100.10"

    def test_invalid_label_raises(self):
        with pytest.raises(ValueError):
            _next_sprint_sublabel("not-a-sprint", set())
        with pytest.raises(ValueError):
            _next_sprint_sublabel("sprint-abc", set())

    def test_reuses_draft_child_plan_over_existing_label(self, tmp_path):
        """sprint-73.2 with draft sprint-73.3 plan → sprint-73.3, not sprint-73.4."""
        sprints_dir = tmp_path / ".commander" / "sprints"
        sprints_dir.mkdir(parents=True)
        (sprints_dir / "sprint-73.3-plan.json").write_text(
            json.dumps({
                "state": "draft",
                "parent": "sprint-73.2",
                "tickets": [928, 929, 932],
            }),
            encoding="utf-8",
        )
        existing = {"sprint-73.3", "sprint-73.2"}
        assert _next_sprint_sublabel("sprint-73.2", existing, tmp_path) == "sprint-73.3"

    def test_skips_finished_child_plan(self, tmp_path):
        sprints_dir = tmp_path / ".commander" / "sprints"
        sprints_dir.mkdir(parents=True)
        (sprints_dir / "sprint-15.1-plan.json").write_text(
            json.dumps({"state": "needs_rework", "parent": "sprint-15", "tickets": [1]}),
            encoding="utf-8",
        )
        existing = {"sprint-15.1"}
        assert _next_sprint_sublabel("sprint-15", existing, tmp_path) == "sprint-15.2"


# ── AC-2: _SPRINT_LABEL_RE accepts dotted labels ─────────────────────────────

class TestSprintLabelRE:
    """_SPRINT_LABEL_RE must accept both plain and dotted labels."""

    def test_plain_sprint_matches(self):
        assert _SPRINT_LABEL_RE.match("sprint-15")
        assert _SPRINT_LABEL_RE.match("sprint-1")
        assert _SPRINT_LABEL_RE.match("sprint-100")

    def test_dotted_sub_label_matches(self):
        assert _SPRINT_LABEL_RE.match("sprint-15.1")
        assert _SPRINT_LABEL_RE.match("sprint-15.10")
        assert _SPRINT_LABEL_RE.match("sprint-1.2")

    def test_invalid_labels_rejected(self):
        assert not _SPRINT_LABEL_RE.match("sprint-abc")
        assert not _SPRINT_LABEL_RE.match("sprint-15.abc")
        assert not _SPRINT_LABEL_RE.match("sprint_15")
        assert not _SPRINT_LABEL_RE.match("Sprint-15")
        assert not _SPRINT_LABEL_RE.match("sprint-15.1.1")


# ── AC-11: _sprint_label_sort_key and natural ordering ───────────────────────

class TestSprintLabelSortKey:
    def test_plain_sprint_sort_key(self):
        assert _sprint_label_sort_key("sprint-15") == (15, 0)
        assert _sprint_label_sort_key("sprint-5") == (5, 0)

    def test_dotted_sprint_sort_key(self):
        assert _sprint_label_sort_key("sprint-15.1") == (15, 1)
        assert _sprint_label_sort_key("sprint-15.10") == (15, 10)

    def test_natural_sort_order(self):
        labels = ["sprint-15.2", "sprint-16", "sprint-15", "sprint-15.1"]
        sorted_labels = sorted(labels, key=_sprint_label_sort_key)
        assert sorted_labels == ["sprint-15", "sprint-15.1", "sprint-15.2", "sprint-16"]

    def test_sprint_n_before_sprint_n_dot_x(self):
        # sprint-N (suffix=0) comes before sprint-N.1 (suffix=1)
        assert _sprint_label_sort_key("sprint-15") < _sprint_label_sort_key("sprint-15.1")
        assert _sprint_label_sort_key("sprint-15.1") < _sprint_label_sort_key("sprint-15.2")
        assert _sprint_label_sort_key("sprint-15.2") < _sprint_label_sort_key("sprint-16")


# ── AC-7: _load_sprint_order handles dotted sub-labels ───────────────────────

class TestLoadSprintOrder:
    def test_sub_labels_included_in_order(self, tmp_path):
        all_labels = ["sprint-15", "sprint-15.1", "sprint-16"]
        result = _load_sprint_order(tmp_path, all_labels)
        assert "sprint-15" in result
        assert "sprint-15.1" in result
        assert "sprint-16" in result

    def test_sub_labels_sorted_naturally(self, tmp_path):
        all_labels = ["sprint-15.2", "sprint-15", "sprint-15.1"]
        result = _load_sprint_order(tmp_path, all_labels)
        # No saved order, so appended in natural sort order
        assert result == ["sprint-15", "sprint-15.1", "sprint-15.2"]

    def test_saved_order_respected(self, tmp_path):
        order_path = tmp_path / ".commander" / "sprint-order.json"
        order_path.parent.mkdir(parents=True)
        order_path.write_text(json.dumps(["sprint-15.1", "sprint-15"]))
        all_labels = ["sprint-15", "sprint-15.1"]
        result = _load_sprint_order(tmp_path, all_labels)
        assert result == ["sprint-15.1", "sprint-15"]  # saved order honored


# ── Frontend JS tests ─────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def js():
    return APP_JS.read_text(encoding="utf-8")


class TestFrontendHelpers:
    """sprintLabelCompare and sprintLabelDisplay must be defined correctly."""

    def test_sprint_label_compare_defined(self, js):
        assert "function sprintLabelCompare" in js

    def test_sprint_label_display_defined(self, js):
        assert "function sprintLabelDisplay" in js

    def test_display_returns_dotted_name(self, js):
        fn_match = re.search(
            r"function sprintLabelDisplay\(label\)\s*\{(.*?)\n\}",
            js, re.DOTALL
        )
        assert fn_match, "sprintLabelDisplay not found"
        body = fn_match.group(1)
        # Must handle both plain and dotted forms
        assert "m[2]" in body, "Display function must handle dotted sub-labels (m[2] check)"

    def test_compare_parses_suffix(self, js):
        fn_match = re.search(
            r"function sprintLabelCompare\(a, b\)\s*\{(.*?)\n\}",
            js, re.DOTALL
        )
        assert fn_match, "sprintLabelCompare not found"
        body = fn_match.group(1)
        assert "parseInt" in body, "Compare must parse integers"


class TestFrontendSprintRendering:
    """smgmtRender must use allSprintEntries with label-based sorting."""

    def test_all_sprint_entries_used(self, js):
        assert "allSprintEntries" in js, "allSprintEntries must be used in smgmtRender"

    def test_all_sprint_nums_removed(self, js):
        assert "const allSprintNums" not in js, \
            "allSprintNums must be replaced by allSprintEntries"

    def test_sprint_label_compare_used_in_sort(self, js):
        # smgmtRender must sort using sprintLabelCompare
        render_idx = js.find("function smgmtRender()")
        assert render_idx >= 0, "smgmtRender not found"
        render_body = js[render_idx:render_idx + 5000]
        assert "sprintLabelCompare" in render_body, \
            "smgmtRender must use sprintLabelCompare for sorting"

    def test_sprint_label_field_used_for_grouping(self, js):
        assert "iss.sprint_label" in js, "Issue grouping must use sprint_label field from API"

    def test_sprint_label_display_used_in_block(self, js):
        fn_idx = js.find("function smgmtSprintBlockHtml(")
        assert fn_idx >= 0, "smgmtSprintBlockHtml not found"
        fn_body = js[fn_idx:fn_idx + 3000]
        assert "sprintLabelDisplay" in fn_body, \
            "smgmtSprintBlockHtml must use sprintLabelDisplay for header text"

    def test_id_sanitization_handles_dots(self, js):
        # IDs should replace both hyphens and dots to avoid CSS selector issues
        assert r"replace(/\./g, '_')" in js, \
            "Dot characters in labels must be sanitized for HTML IDs"


class TestFrontendDragDrop:
    """Drag-drop and bulk-move must handle dotted sub-labels correctly."""

    def test_drop_uses_sprint_label_for_dotted(self, js):
        assert "isDotted" in js, \
            "Drag-drop must detect and specially handle dotted sub-labels"

    def test_api_body_uses_sprint_label_field(self, js):
        assert "sprint_label: targetSprintLabel" in js, \
            "Drop handler API call must use sprint_label field for dotted labels"

    def test_bulk_move_uses_sprint_label(self, js):
        assert "isDottedDest" in js, \
            "Bulk-move must detect dotted destinations"
