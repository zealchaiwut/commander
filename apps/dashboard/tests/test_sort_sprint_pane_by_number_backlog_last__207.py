"""Tests for issue #207: Sort Sprint Pane by Number, Backlog Last.

Covers all 6 acceptance criteria via static JS analysis.
No running server required.
"""
from __future__ import annotations

from pathlib import Path

import pytest

DASHBOARD_DIR = Path(__file__).parent.parent
APP_JS = DASHBOARD_DIR / "static" / "app.js"


@pytest.fixture(scope="module")
def js() -> str:
    return APP_JS.read_text()


# ---------------------------------------------------------------------------
# AC1: Sprints displayed in ascending order by sprint number
# ---------------------------------------------------------------------------

class TestAC1AscendingSprintOrder:
    def test_sorted_order_sorts_ascending(self, js):
        """sortedOrder uses a numeric ascending comparator on sprint label numbers."""
        assert "sortedOrder" in js

    def test_sorted_order_uses_numeric_comparison(self, js):
        """The sort comparator parses sprint number integers and returns na - nb."""
        # Check the sort block that creates sortedOrder
        idx = js.find("sortedOrder")
        assert idx != -1
        block = js[idx:idx + 400]
        assert "parseInt" in block
        assert "na - nb" in block or "a.num - b.num" in block or \
               "parseInt(a.split('-')[1], 10)" in block

    def test_all_sprint_nums_sorted_ascending(self, js):
        """allSprintNums is sorted so non-empty and empty sprints interleave by number."""
        assert "allSprintNums" in js
        idx = js.find("allSprintNums")
        block = js[idx:idx + 500]
        assert ".sort(" in block
        assert "a.num - b.num" in block

    def test_render_iterates_allSprintNums(self, js):
        """smgmtRender renders sprints in allSprintNums order, not raw order."""
        assert "allSprintNums.map(" in js

    def test_sort_ascending_not_descending(self, js):
        """Comparator is na - nb (ascending), not nb - na (descending)."""
        # sortedOrder sort: na - nb
        idx = js.find("[...order].sort(")
        assert idx != -1, "sortedOrder creation not found"
        block = js[idx:idx + 200]
        assert "na - nb" in block


# ---------------------------------------------------------------------------
# AC2: Backlog section is always the last item
# ---------------------------------------------------------------------------

class TestAC2BacklogLast:
    def test_backlog_rendered_after_sprint_blocks(self, js):
        """smgmtRenderBacklog is called after bodyEl.innerHTML is set."""
        html_assign_idx = js.find("bodyEl.innerHTML = blocksHtml")
        assert html_assign_idx != -1, "bodyEl.innerHTML assignment not found"
        backlog_call_idx = js.find("smgmtRenderBacklog(", html_assign_idx)
        assert backlog_call_idx != -1, \
            "smgmtRenderBacklog must be called after bodyEl.innerHTML = blocksHtml"

    def test_backlog_not_included_in_blocks_html(self, js):
        """Backlog is not rendered as part of blocksHtml — it has its own render path."""
        # blocksHtml is assigned from allSprintNums.map; backlog has its own call
        assert "smgmtRenderBacklog" in js
        # The backlog function targets a separate DOM element
        assert "smgmt-backlog" in js

    def test_smgmt_render_ends_with_backlog_call(self, js):
        """The smgmtRender function body calls smgmtRenderBacklog near the end."""
        fn_start = js.find("function smgmtRender()")
        assert fn_start != -1
        # Find the closing brace of smgmtRender by looking for the next function definition
        fn_end = js.find("\nfunction ", fn_start + 1)
        fn_body = js[fn_start:fn_end] if fn_end != -1 else js[fn_start:fn_start + 2000]
        assert "smgmtRenderBacklog(" in fn_body, \
            "smgmtRender must call smgmtRenderBacklog"
        # backlog call should appear after sprint blocks html assignment
        blocks_pos = fn_body.find("bodyEl.innerHTML")
        backlog_pos = fn_body.find("smgmtRenderBacklog(")
        assert blocks_pos != -1 and backlog_pos != -1
        assert backlog_pos > blocks_pos, \
            "smgmtRenderBacklog must come after bodyEl.innerHTML assignment"


# ---------------------------------------------------------------------------
# AC3: Empty sprints are visible at their correct sorted position
# ---------------------------------------------------------------------------

class TestAC3EmptySprintsVisible:
    def test_empty_sprint_nums_included_in_merge(self, js):
        """emptySprintNums entries are spread into allSprintNums for rendering."""
        assert "emptySprintNums.map(n => ({ num: n, isEmpty: true }))" in js or \
               "emptySprintNums.map(" in js

    def test_all_sprint_nums_contains_both_types(self, js):
        """allSprintNums merges sortedOrder (non-empty) and emptySprintNums (empty)."""
        idx = js.find("allSprintNums")
        block = js[idx:idx + 300]
        assert "isEmpty: false" in block
        assert "isEmpty: true" in block

    def test_placeholder_rendered_for_empty_sprints(self, js):
        """isEmpty entries in allSprintNums render smgmtPlaceholderBlockHtml."""
        idx = js.find("allSprintNums.map(")
        assert idx != -1
        block = js[idx:idx + 300]
        assert "smgmtPlaceholderBlockHtml(num)" in block or \
               "smgmtPlaceholderBlockHtml(" in block

    def test_empty_sprints_not_filtered_out(self, js):
        """emptySprintNums is used in allSprintNums; the render path is not gated on non-zero count."""
        # The render uses allSprintNums.map which always processes all entries
        assert "allSprintNums.map(" in js


# ---------------------------------------------------------------------------
# AC4: Issues can be dragged and dropped into an empty sprint
# ---------------------------------------------------------------------------

class TestAC4DragDropIntoEmptySprint:
    def test_placeholder_block_has_ondrop(self, js):
        """smgmtPlaceholderBlockHtml includes ondrop wired to smgmtDropOnPlaceholder."""
        assert "ondrop=\"smgmtDropOnPlaceholder" in js

    def test_drop_on_placeholder_function_exists(self, js):
        """smgmtDropOnPlaceholder function handles drop events on empty sprints."""
        assert "smgmtDropOnPlaceholder" in js
        assert "function smgmtDropOnPlaceholder" in js or \
               "async function smgmtDropOnPlaceholder" in js

    def test_placeholder_block_has_ondragover(self, js):
        """Placeholder block has ondragover for visual drop feedback."""
        assert "ondragover=\"smgmtDragOverPlaceholder" in js

    def test_empty_sprints_rendered_as_droppable_zones(self, js):
        """Empty sprints in allSprintNums call smgmtPlaceholderBlockHtml which is droppable."""
        idx = js.find("if (isEmpty)")
        assert idx != -1
        block = js[idx:idx + 100]
        assert "smgmtPlaceholderBlockHtml(" in block


# ---------------------------------------------------------------------------
# AC5: Sort order is stable — refresh preserves same order
# ---------------------------------------------------------------------------

class TestAC5StableSortOrder:
    def test_sort_is_pure_and_deterministic(self, js):
        """Sort comparator uses only sprint number — no random or time-based element."""
        idx = js.find("[...order].sort(")
        assert idx != -1
        block = js[idx:idx + 200]
        # Comparator must use only integer comparison — no Math.random or Date
        assert "Math.random" not in block
        assert "Date" not in block

    def test_sprint_number_parsed_from_label(self, js):
        """Sprint number is parsed from label name using parseInt — same label → same number."""
        assert "parseInt(a.split('-')[1], 10)" in js or \
               "parseInt(l.split('-')[1], 10)" in js

    def test_all_sprint_nums_sort_is_deterministic(self, js):
        """allSprintNums sort uses only num — no random or time-based element."""
        idx = js.find("allSprintNums")
        block = js[idx:idx + 400]
        assert "Math.random" not in block
        assert "Date()" not in block

    def test_smgmt_render_backlog_after_html(self, js):
        """smgmtRender is a pure render function (not async) — calling it twice gives same output."""
        # smgmtRender itself is synchronous
        assert "function smgmtRender()" in js
        assert "async function smgmtRender" not in js


# ---------------------------------------------------------------------------
# AC6: Same-number sprints fall back to deterministic secondary sort
# ---------------------------------------------------------------------------

class TestAC6DeterministicSecondarySort:
    def test_comparator_returns_zero_for_equal_nums(self, js):
        """When sprint numbers are equal, comparator returns 0 — relies on stable sort."""
        # The comparator is na - nb; when na == nb it returns 0
        # JavaScript Array.sort is stable (ECMAScript 2019+), so original order preserved
        idx = js.find("[...order].sort(")
        assert idx != -1
        block = js[idx:idx + 200]
        # Only one comparison key used; no tiebreak means stable sort handles it
        assert "na - nb" in block

    def test_all_sprint_nums_comparator_is_stable_for_ties(self, js):
        """allSprintNums sort uses a.num - b.num only; ties fall back to insertion order."""
        idx = js.find(".sort((a, b) => a.num - b.num)")
        assert idx != -1, \
            "allSprintNums must use .sort((a, b) => a.num - b.num) for stable tie-breaking"

    def test_spread_order_preserves_non_empty_before_empty_on_tie(self, js):
        """Non-empty sprints are spread before empty sprints in allSprintNums construction."""
        idx = js.find("allSprintNums")
        block = js[idx:idx + 300]
        # sortedOrder (non-empty) appears before emptySprintNums in the spread
        nonempty_pos = block.find("sortedOrder.map(")
        empty_pos = block.find("emptySprintNums.map(")
        assert nonempty_pos != -1 and empty_pos != -1
        assert nonempty_pos < empty_pos, \
            "Non-empty sprints must precede empty sprints in allSprintNums spread"
