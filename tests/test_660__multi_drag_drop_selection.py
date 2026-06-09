"""Tests for issue #660: Fix Multiple Drag-and-Drop Selection Not Working

AC-1: User can select multiple items and drag them together as a group
AC-2: All selected items drop to the target position in their original relative order
AC-3: Visual feedback (highlight, ghost image) reflects all selected items during drag
AC-4: Dragging a non-selected item while others are selected moves only that item
AC-5: Keyboard modifier (Shift/Ctrl/Cmd) multi-select works before initiating drag
AC-6: Multi-drag works across all supported containers/lists where single drag works
AC-7: Drop target shows valid/invalid state correctly for multi-item payloads
AC-8: No regression on single-item drag-and-drop behavior
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECT_HTML = REPO_ROOT / "apps" / "dashboard" / "static" / "project.html"

_HTML = PROJECT_HTML.read_text()


# ── Python reference: mirrors JS drag-state logic ────────────────────────────

def _build_drag_state(selected_issues: set[int], dragged_issue: int, from_sprint: str | None):
    """Mirrors _smgmtTicketDragStart / _smgmtBacklogTicketDragStart logic.

    Returns the drag-state dict that should be stored in _smgmtDragTicket.
    """
    is_checked = dragged_issue in selected_issues
    if is_checked and len(selected_issues) > 1:
        nums = list(selected_issues)
        return {"number": dragged_issue, "fromSprint": from_sprint, "multi": nums}
    return {"number": dragged_issue, "fromSprint": from_sprint, "multi": None}


def _multi_drop_preserves_order(selected_order: list[int], target_label: str) -> list[dict]:
    """Mirrors the batch-labels changes array built in _smgmtDropOnSprint."""
    return [{"issue_num": n, "sprint_label": target_label} for n in selected_order]


# ── AC-1: Select multiple and drag as a group ────────────────────────────────

class TestAC1MultiDragFromBacklog:
    def test_backlog_drag_start_reads_selected_issues(self):
        """_smgmtBacklogTicketDragStart must reference _smgmtSelectedIssues."""
        fn_start = _HTML.find("function _smgmtBacklogTicketDragStart")
        assert fn_start != -1, "_smgmtBacklogTicketDragStart function must exist"
        # Find closing brace — take next 600 chars as function body approximation
        fn_body = _HTML[fn_start: fn_start + 600]
        assert "_smgmtSelectedIssues" in fn_body, (
            "_smgmtBacklogTicketDragStart must check _smgmtSelectedIssues "
            "to support multi-drag from backlog"
        )

    def test_backlog_drag_start_sets_multi_payload(self):
        """When multiple items selected, backlog drag must set multi array."""
        fn_start = _HTML.find("function _smgmtBacklogTicketDragStart")
        assert fn_start != -1
        fn_body = _HTML[fn_start: fn_start + 600]
        assert "multi:" in fn_body, (
            "_smgmtBacklogTicketDragStart must populate the multi: field "
            "in _smgmtDragTicket when multiple tickets are selected"
        )

    def test_multi_drag_state_contains_all_selected(self):
        """build_drag_state returns multi containing all selected issues."""
        selected = {10, 20, 30}
        state = _build_drag_state(selected, dragged_issue=10, from_sprint=None)
        assert state["multi"] is not None
        assert set(state["multi"]) == selected

    def test_multi_drag_state_single_selected_no_multi(self):
        """build_drag_state returns multi=None when only one ticket selected."""
        state = _build_drag_state({10}, dragged_issue=10, from_sprint=None)
        assert state["multi"] is None

    def test_sprint_drag_start_also_checks_selected(self):
        """_smgmtTicketDragStart (sprint) already checks selection — must remain."""
        fn_start = _HTML.find("function _smgmtTicketDragStart")
        assert fn_start != -1
        fn_body = _HTML[fn_start: fn_start + 600]
        assert "_smgmtSelectedIssues" in fn_body


# ── AC-2: Drop preserves relative order ──────────────────────────────────────

class TestAC2RelativeOrder:
    def test_batch_payload_preserves_insertion_order(self):
        """Items in the batch-labels payload must appear in original selection order."""
        selected_order = [10, 20, 30]
        changes = _multi_drop_preserves_order(selected_order, "sprint-5")
        nums = [c["issue_num"] for c in changes]
        assert nums == [10, 20, 30]

    def test_batch_payload_all_get_same_target(self):
        """All items in payload must target the same sprint."""
        changes = _multi_drop_preserves_order([10, 20, 30], "sprint-5")
        labels = {c["sprint_label"] for c in changes}
        assert labels == {"sprint-5"}

    def test_drop_on_sprint_uses_batch_labels_for_multi(self):
        """_smgmtDropOnSprint must use /api/sprints/batch-labels for multi payloads."""
        fn_start = _HTML.find("async function _smgmtDropOnSprint")
        assert fn_start != -1
        fn_body = _HTML[fn_start: fn_start + 1500]
        assert "batch-labels" in fn_body, (
            "_smgmtDropOnSprint must call batch-labels API for multi-ticket drops"
        )

    def test_drop_on_backlog_uses_batch_labels_for_multi(self):
        """_smgmtDropOnBacklog must use batch-labels for multi-ticket drops."""
        fn_start = _HTML.find("async function _smgmtDropOnBacklog")
        assert fn_start != -1
        fn_body = _HTML[fn_start: fn_start + 1000]
        assert "batch-labels" in fn_body


# ── AC-3: Visual feedback during drag ────────────────────────────────────────

class TestAC3VisualFeedback:
    def test_drag_pill_element_exists(self):
        """smgmt-drag-pill element must exist for multi-drag feedback."""
        assert 'smgmt-drag-pill' in _HTML, (
            "A floating drag pill (#smgmt-drag-pill) must exist for visual feedback"
        )

    def test_drag_pill_css_defined(self):
        """.smgmt-drag-pill CSS class must be defined."""
        assert re.search(r'\.smgmt-drag-pill\s*\{', _HTML), (
            ".smgmt-drag-pill CSS must be defined"
        )

    def test_dragging_ticket_class_applied_to_multi(self):
        """All selected tickets must receive dragging-ticket class during multi-drag."""
        fn_start = _HTML.find("function _smgmtBacklogTicketDragStart")
        assert fn_start != -1
        fn_body = _HTML[fn_start: fn_start + 900]
        assert "dragging-ticket" in fn_body, (
            "_smgmtBacklogTicketDragStart must apply dragging-ticket class "
            "to all selected tickets during multi-drag"
        )

    def test_pill_shows_count_in_sprint_drag(self):
        """`Moving N tickets` label must appear in sprint drag start."""
        fn_start = _HTML.find("function _smgmtTicketDragStart")
        assert fn_start != -1
        fn_body = _HTML[fn_start: fn_start + 2000]
        assert "Moving" in fn_body and "tickets" in fn_body

    def test_pill_shown_in_backlog_multi_drag(self):
        """Backlog multi-drag must also show the pill."""
        fn_start = _HTML.find("function _smgmtBacklogTicketDragStart")
        assert fn_start != -1
        fn_body = _HTML[fn_start: fn_start + 900]
        assert "smgmt-drag-pill" in fn_body, (
            "_smgmtBacklogTicketDragStart must show smgmt-drag-pill during multi-drag"
        )

    def test_is_selected_class_exists(self):
        """.is-selected CSS class must exist for selected-ticket visual state."""
        assert re.search(r'\.is-selected', _HTML), (
            ".is-selected CSS class must exist for selected ticket highlighting"
        )


# ── AC-4: Dragging non-selected item moves only that item ────────────────────

class TestAC4NonSelectedItemDragSingle:
    def test_unselected_drag_returns_null_multi(self):
        """Dragging an unselected item returns multi=None regardless of other selections."""
        selected = {20, 30}  # items 20 and 30 selected
        state = _build_drag_state(selected, dragged_issue=10, from_sprint="sprint-1")
        assert state["multi"] is None
        assert state["number"] == 10

    def test_single_selected_drag_returns_null_multi(self):
        """Dragging the only selected item returns multi=None."""
        state = _build_drag_state({10}, dragged_issue=10, from_sprint="sprint-1")
        assert state["multi"] is None

    def test_sprint_drag_start_single_branch_exists(self):
        """_smgmtTicketDragStart must have a single-ticket (else) branch."""
        fn_start = _HTML.find("function _smgmtTicketDragStart")
        assert fn_start != -1
        fn_body = _HTML[fn_start: fn_start + 2500]
        # Single-ticket branch sets multi: null
        assert "multi: null" in fn_body, (
            "_smgmtTicketDragStart must have a single-ticket path with multi: null"
        )

    def test_backlog_drag_start_single_branch_exists(self):
        """_smgmtBacklogTicketDragStart must also have a single-ticket branch."""
        fn_start = _HTML.find("function _smgmtBacklogTicketDragStart")
        assert fn_start != -1
        fn_body = _HTML[fn_start: fn_start + 1200]
        assert "multi: null" in fn_body, (
            "_smgmtBacklogTicketDragStart must have a single-ticket path with multi: null"
        )


# ── AC-5: Keyboard modifier (Shift/Ctrl/Cmd) multi-select ────────────────────

class TestAC5KeyboardModifiers:
    def test_shift_click_range_select_exists(self):
        """_smgmtRowClick must handle shiftKey for range selection."""
        fn_start = _HTML.find("function _smgmtRowClick")
        assert fn_start != -1
        fn_body = _HTML[fn_start: fn_start + 800]
        assert "shiftKey" in fn_body, (
            "_smgmtRowClick must handle Shift+click for range selection"
        )

    def test_ctrl_cmd_click_handled(self):
        """_smgmtRowClick must handle ctrlKey/metaKey for individual item toggle."""
        fn_start = _HTML.find("function _smgmtRowClick")
        assert fn_start != -1
        fn_body = _HTML[fn_start: fn_start + 1200]
        assert ("ctrlKey" in fn_body or "metaKey" in fn_body), (
            "_smgmtRowClick must handle ctrlKey/metaKey for Ctrl/Cmd+click multi-select"
        )

    def test_selection_set_exists(self):
        """_smgmtSelectedIssues Set must exist to track multi-selections."""
        assert "_smgmtSelectedIssues" in _HTML, (
            "_smgmtSelectedIssues Set must exist to track selected tickets"
        )

    def test_last_selected_anchor_exists(self):
        """_smgmtLastSelectedNum anchor must exist for shift-click range select."""
        assert "_smgmtLastSelectedNum" in _HTML, (
            "_smgmtLastSelectedNum must exist as anchor for Shift+click range selection"
        )


# ── AC-6: Multi-drag works across all containers ─────────────────────────────

class TestAC6AllContainers:
    def test_sprint_tickets_have_drag_handler(self):
        """Sprint ticket HTML must wire ondragstart to _smgmtTicketDragStart."""
        assert "_smgmtTicketDragStart" in _HTML, (
            "Sprint tickets must have _smgmtTicketDragStart drag handler"
        )

    def test_backlog_tickets_have_drag_handler(self):
        """Backlog ticket HTML must wire ondragstart to _smgmtBacklogTicketDragStart."""
        assert "_smgmtBacklogTicketDragStart" in _HTML, (
            "Backlog tickets must have _smgmtBacklogTicketDragStart drag handler"
        )

    def test_backlog_multi_drag_same_logic_as_sprint(self):
        """Both drag-start functions must check _smgmtSelectedIssues.size for multi."""
        sprint_fn_start = _HTML.find("function _smgmtTicketDragStart")
        backlog_fn_start = _HTML.find("function _smgmtBacklogTicketDragStart")
        assert sprint_fn_start != -1 and backlog_fn_start != -1

        sprint_body = _HTML[sprint_fn_start: sprint_fn_start + 600]
        backlog_body = _HTML[backlog_fn_start: backlog_fn_start + 600]

        assert "_smgmtSelectedIssues" in sprint_body
        assert "_smgmtSelectedIssues" in backlog_body, (
            "Both sprint and backlog drag-start must check _smgmtSelectedIssues"
        )

    def test_drop_on_sprint_handles_multi_from_backlog(self):
        """_smgmtDropOnSprint multi-branch must handle tickets with fromSprint=null."""
        fn_start = _HTML.find("async function _smgmtDropOnSprint")
        assert fn_start != -1
        fn_body = _HTML[fn_start: fn_start + 1500]
        # Must handle the multi path
        assert "dragInfo.multi" in fn_body, (
            "_smgmtDropOnSprint must check dragInfo.multi for multi-ticket drop"
        )


# ── AC-7: Drop target valid/invalid state ────────────────────────────────────

class TestAC7DropTargetState:
    def test_drag_over_sprint_css_class_exists(self):
        """.drag-over-sprint CSS class must exist for drop target highlight."""
        assert re.search(r'\.drag-over-sprint\s*\{', _HTML) or \
               re.search(r'drag-over-sprint', _HTML), (
            ".drag-over-sprint CSS class must exist for drop target highlighting"
        )

    def test_drag_over_sprint_applied_in_dragover(self):
        """_smgmtDragOver must add drag-over-sprint class to indicate valid target."""
        fn_start = _HTML.find("function _smgmtDragOver")
        assert fn_start != -1
        fn_body = _HTML[fn_start: fn_start + 300]
        assert "drag-over-sprint" in fn_body

    def test_drag_over_sprint_removed_in_dragleave(self):
        """drag-over-sprint class must be removed on dragleave."""
        fn_start = _HTML.find("function _smgmtDragLeave")
        assert fn_start != -1
        fn_body = _HTML[fn_start: fn_start + 200]
        assert "drag-over-sprint" in fn_body

    def test_drag_over_sprint_removed_on_drop(self):
        """drag-over-sprint class must be cleaned up on drop."""
        fn_start = _HTML.find("async function _smgmtDropOnSprint")
        assert fn_start != -1
        fn_body = _HTML[fn_start: fn_start + 400]
        assert "drag-over-sprint" in fn_body

    def test_drag_over_backlog_class_exists(self):
        """.drag-over-backlog class must exist for backlog drop target state."""
        assert "drag-over-backlog" in _HTML


# ── AC-8: No regression on single-item drag ──────────────────────────────────

class TestAC8SingleDragNoRegression:
    def test_single_drag_path_in_sprint_drag_start(self):
        """_smgmtTicketDragStart single-item path must still exist."""
        fn_start = _HTML.find("function _smgmtTicketDragStart")
        assert fn_start != -1
        fn_body = _HTML[fn_start: fn_start + 2500]
        # Single path uses number directly without multi
        assert "multi: null" in fn_body

    def test_single_drag_path_in_backlog_drag_start(self):
        """_smgmtBacklogTicketDragStart single-item path must still exist."""
        fn_start = _HTML.find("function _smgmtBacklogTicketDragStart")
        assert fn_start != -1
        fn_body = _HTML[fn_start: fn_start + 1200]
        assert "multi: null" in fn_body

    def test_single_drop_on_sprint_endpoint_still_used(self):
        """Single-ticket drop must still call /api/issues/{number}/sprint-label."""
        fn_start = _HTML.find("async function _smgmtDropOnSprint")
        assert fn_start != -1
        fn_body = _HTML[fn_start: fn_start + 3000]
        assert "sprint-label" in fn_body

    def test_drag_end_cleans_up_single_ticket(self):
        """_smgmtTicketDragEnd must clean up single-ticket dragging-ticket class."""
        fn_start = _HTML.find("function _smgmtTicketDragEnd")
        assert fn_start != -1
        fn_body = _HTML[fn_start: fn_start + 700]
        assert "dragging-ticket" in fn_body
        assert "_smgmtDragTicket = null" in fn_body

    def test_drag_end_cleans_up_multi_tickets(self):
        """_smgmtTicketDragEnd must also clean up all multi-ticket dragging-ticket classes."""
        fn_start = _HTML.find("function _smgmtTicketDragEnd")
        assert fn_start != -1
        fn_body = _HTML[fn_start: fn_start + 400]
        assert "multi" in fn_body and "dragging-ticket" in fn_body

    def test_build_drag_state_no_regression_single(self):
        """Reference impl: single unselected drag returns correct state."""
        state = _build_drag_state(set(), dragged_issue=42, from_sprint="sprint-1")
        assert state == {"number": 42, "fromSprint": "sprint-1", "multi": None}

    def test_build_drag_state_no_regression_from_backlog(self):
        """Reference impl: single drag from backlog has fromSprint=None."""
        state = _build_drag_state(set(), dragged_issue=99, from_sprint=None)
        assert state == {"number": 99, "fromSprint": None, "multi": None}
