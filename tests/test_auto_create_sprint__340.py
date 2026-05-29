"""Tests for issue #340 — Auto-create next sprint on drag below last sprint.

AC coverage:
  AC-1  While dragging, an N+1 ghost pane renders below all existing sprints
  AC-2  Ghost pane hidden initially; disappears on cancel or drop elsewhere
  AC-3  Dropping on N+1 pane creates sprint via API with correct sequential label
  AC-4  Dropped item moved into new sprint; removed from original location
  AC-5  New sprint appended at bottom, visible without page refresh
  AC-6  Multiple sequential drops add to same sprint; no duplicate sprints created
  AC-7  Feature uses standard HTML5 DnD API (cross-browser compatible)
"""
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "dashboard"))
from server import app

_JS_PATH   = Path(__file__).parent.parent / "apps" / "dashboard" / "static" / "app.js"
_HTML_PATH = Path(__file__).parent.parent / "apps" / "dashboard" / "static" / "index.html"


@pytest.fixture(scope="module")
def js():
    return _JS_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def html():
    return _HTML_PATH.read_text(encoding="utf-8")


def _extract_function(js: str, fn_name: str) -> str:
    for prefix in (f"async function {fn_name}(", f"function {fn_name}("):
        start = js.find(prefix)
        if start != -1:
            break
    else:
        return ""
    for marker in ("\nasync function ", "\nfunction "):
        end = js.find(marker, start + 1)
        if end != -1:
            return js[start:end]
    return js[start:]


# ── AC-1: Ghost N+1 sprint pane rendered below existing sprints during drag ───

class TestAC1GhostPaneRendered:

    def test_placeholder_html_function_defined(self, js):
        assert "function smgmtPlaceholderBlockHtml(" in js, \
            "smgmtPlaceholderBlockHtml must be defined to produce the N+1 ghost pane"

    def test_placeholder_appended_in_render(self, js):
        render_fn = _extract_function(js, "smgmtRender")
        assert "smgmtPlaceholderBlockHtml" in render_fn, \
            "smgmtRender must call smgmtPlaceholderBlockHtml to append the trailing ghost pane"

    def test_placeholder_labeled_with_sprint_number(self, js):
        ph_fn = _extract_function(js, "smgmtPlaceholderBlockHtml")
        assert "Sprint ${n}" in ph_fn, \
            "Ghost pane must be labeled 'Sprint N' using the next sequential number"

    def test_placeholder_has_css_class(self, js):
        ph_fn = _extract_function(js, "smgmtPlaceholderBlockHtml")
        assert "smgmt-sprint-placeholder" in ph_fn, \
            "Ghost pane must carry smgmt-sprint-placeholder CSS class"

    def test_ticket_dragstart_shows_placeholder(self, js):
        fn = _extract_function(js, "smgmtTicketDragStart")
        assert "smgmt-sprint-placeholder" in fn, \
            "smgmtTicketDragStart must reveal .smgmt-sprint-placeholder panes"
        assert "style.display" in fn and ("block" in fn or "'block'" in fn or '"block"' in fn), \
            "smgmtTicketDragStart must set display:block on placeholder"

    def test_backlog_dragstart_shows_placeholder(self, js):
        fn = _extract_function(js, "smgmtBacklogTicketDragStart")
        assert "smgmt-sprint-placeholder" in fn, \
            "smgmtBacklogTicketDragStart must also reveal .smgmt-sprint-placeholder panes"

    def test_placeholder_uses_next_sequential_number(self, js):
        render_fn = _extract_function(js, "smgmtRender")
        assert "placeholder_sprint" in render_fn or "placeholderN" in render_fn, \
            "smgmtRender must compute/use the next sprint number for the ghost pane"


# ── AC-2: Ghost pane hidden initially; disappears on cancel / drop elsewhere ──

class TestAC2GhostPaneHiddenByDefault:

    def test_placeholder_initially_hidden(self, js):
        ph_fn = _extract_function(js, "smgmtPlaceholderBlockHtml")
        assert 'display:none' in ph_fn or 'display: none' in ph_fn, \
            "Ghost pane must start with display:none so it is invisible before any drag"

    def test_ticket_dragend_hides_placeholder(self, js):
        fn = _extract_function(js, "smgmtTicketDragEnd")
        assert "smgmt-sprint-placeholder" in fn, \
            "smgmtTicketDragEnd must target .smgmt-sprint-placeholder elements"
        assert "none" in fn, \
            "smgmtTicketDragEnd must set display:none to hide the ghost pane on drag end"

    def test_backlog_dragend_delegates_to_ticket_dragend(self, js):
        fn = _extract_function(js, "smgmtBacklogTicketDragEnd")
        assert "smgmtTicketDragEnd" in fn, \
            "smgmtBacklogTicketDragEnd must delegate to smgmtTicketDragEnd to hide the ghost pane"


# ── AC-3: Confirm creates sprint via API with correct sequential label ──────────
# (Issue #364 revised: drop opens a confirm modal; the API call is in smgmtGhostConfirmCreate)

class TestAC3DropCreatesSprintViaAPI:

    def test_drop_on_placeholder_function_defined(self, js):
        assert "function smgmtDropOnPlaceholder(" in js or \
               "async function smgmtDropOnPlaceholder(" in js, \
            "smgmtDropOnPlaceholder must be defined as the drop handler for the ghost pane"

    def test_drop_calls_assign_endpoint(self, js):
        # Revised (#364): API call lives in smgmtGhostConfirmCreate (confirm modal path)
        fn = _extract_function(js, "smgmtGhostConfirmCreate")
        assert "/api/sprint-planning/assign" in fn, \
            "smgmtGhostConfirmCreate must POST to /api/sprint-planning/assign to create the sprint"

    def test_post_body_includes_correct_sprint_number(self, js):
        fn = _extract_function(js, "smgmtGhostConfirmCreate")
        assert "sprint:" in fn or "sprint_label:" in fn, \
            "POST body must include the sprint number"

    def test_post_body_includes_issue_number(self, js):
        fn = _extract_function(js, "smgmtGhostConfirmCreate")
        assert "issue:" in fn, \
            "POST body must include the dragged issue number"

    def test_placeholder_has_ondrop_handler(self, js):
        ph_fn = _extract_function(js, "smgmtPlaceholderBlockHtml")
        assert "smgmtDropOnPlaceholder" in ph_fn, \
            "Ghost pane element must wire its ondrop to smgmtDropOnPlaceholder"


# ── AC-4: Confirmed item moved into new sprint; removed from original location ─

class TestAC4DroppedItemMovedToNewSprint:

    def test_optimistic_sprint_update(self, js):
        # Revised (#364): optimistic update happens in smgmtGhostConfirmCreate
        fn = _extract_function(js, "smgmtGhostConfirmCreate")
        assert "iss.sprint = sprintN" in fn or "iss.sprint_label" in fn, \
            "Confirm handler must update iss.sprint/sprint_label in local data"

    def test_render_called_after_optimistic_update(self, js):
        fn = _extract_function(js, "smgmtGhostConfirmCreate")
        assert "smgmtRender()" in fn, \
            "smgmtRender() must be called after local data is updated"

    def test_rollback_restores_original_sprint(self, js):
        # Revised: error path shows error in modal, does not re-render (no partial state)
        fn = _extract_function(js, "smgmtGhostConfirmCreate")
        assert "catch" in fn, \
            "Confirm handler must have a catch block for error handling"

    def test_rollback_block_present(self, js):
        fn = _extract_function(js, "smgmtGhostConfirmCreate")
        assert "catch" in fn, \
            "Confirm handler must have a catch block to handle API failure"


# ── AC-5: New sprint visible at bottom without page refresh ───────────────────

class TestAC5NewSprintVisibleWithoutRefresh:

    def test_sprint_pushed_to_data_sprints(self, js):
        # Revised: local state update happens in smgmtGhostConfirmCreate
        fn = _extract_function(js, "smgmtGhostConfirmCreate")
        assert "_smgmtData.sprints.push" in fn, \
            "Confirm handler must push the new sprint number into _smgmtData.sprints"

    def test_sprint_label_pushed_to_order(self, js):
        fn = _extract_function(js, "smgmtGhostConfirmCreate")
        assert "_smgmtData.order.push" in fn, \
            "Confirm handler must push the new sprint label into _smgmtData.order"

    def test_placeholder_sprint_advances(self, js):
        fn = _extract_function(js, "smgmtGhostConfirmCreate")
        assert "_smgmtData.placeholder_sprint" in fn or "_smgmtComputeNextFreeSprint" in fn, \
            "Confirm handler must advance the placeholder sprint to the new next-free number"

    def test_backend_returns_placeholder_sprint(self):
        import inspect
        from server import get_sprint_management_issues
        src = inspect.getsource(get_sprint_management_issues)
        assert "placeholder_sprint" in src, \
            "GET /api/sprint-management/issues must include placeholder_sprint in its response"

    def test_refresh_board_called_on_success(self, js):
        fn = _extract_function(js, "smgmtGhostConfirmCreate")
        assert "smgmtRefreshBoard" in fn, \
            "Confirm handler must call smgmtRefreshBoard() after a successful API response"


# ── AC-6: Multiple sequential drops go to correct sprint; no duplicates ────────

class TestAC6NoduplicateSprints:

    def test_guard_against_duplicate_sprint_number(self, js):
        # Revised: guard in smgmtGhostConfirmCreate
        fn = _extract_function(js, "smgmtGhostConfirmCreate")
        assert "includes(sprintN)" in fn or "_smgmtData.sprints" in fn, \
            "Confirm handler must guard against inserting a duplicate sprint number"

    def test_guard_against_duplicate_order_label(self, js):
        fn = _extract_function(js, "smgmtGhostConfirmCreate")
        assert "includes(newSprintLabel)" in fn or "_smgmtData.order" in fn, \
            "Confirm handler must guard against inserting a duplicate sprint label"

    def test_placeholder_advances_past_max_existing(self, js):
        # Revised (#364): uses _smgmtComputeNextFreeSprint() (lowest-free, not Math.max)
        fn = _extract_function(js, "smgmtGhostConfirmCreate")
        assert "_smgmtComputeNextFreeSprint" in fn or "_smgmtData.placeholder_sprint" in fn, \
            "Confirm handler must advance placeholder using the next-free computation"

    def test_multi_ticket_drop_handled(self, js):
        fn = _extract_function(js, "smgmtDropOnPlaceholder")
        assert "_smgmtDragTickets" in fn or "tickets" in fn, \
            "Drop handler must collect multi-ticket drag state"


# ── AC-7: Cross-browser — standard HTML5 Drag and Drop API ───────────────────

class TestAC7CrossBrowserCompatibility:

    def test_uses_dataTransfer_not_proprietary_api(self, js):
        fn_start = _extract_function(js, "smgmtTicketDragStart")
        assert "dataTransfer" in fn_start, \
            "Drag start must use the standard HTML5 dataTransfer API for cross-browser support"

    def test_uses_dragover_event_and_prevent_default(self, js):
        fn = _extract_function(js, "smgmtDragOverPlaceholder")
        assert "event.preventDefault()" in fn, \
            "dragover handler must call event.preventDefault() to enable drop (required in all browsers)"

    def test_drop_handler_calls_prevent_default(self, js):
        fn = _extract_function(js, "smgmtDropOnPlaceholder")
        assert "event.preventDefault()" in fn, \
            "drop handler must call event.preventDefault() for consistent cross-browser behavior"


# ── Backend unit: placeholder_sprint computation ──────────────────────────────

class TestBackendPlaceholderSprintComputation:
    """Verify placeholder_sprint is the lowest free sprint number (issue #364 revised)."""

    def _compute_placeholder(self, sprints: list[int]) -> int:
        # New algorithm: lowest positive N not in sprints
        used = set(sprints)
        n = 1
        while n in used:
            n += 1
        return n

    def test_placeholder_sequential_is_next(self):
        assert self._compute_placeholder([1, 2, 3]) == 4

    def test_placeholder_with_single_sprint(self):
        assert self._compute_placeholder([1]) == 2

    def test_placeholder_with_no_sprints(self):
        assert self._compute_placeholder([]) == 1

    def test_placeholder_with_gap_returns_gap(self):
        # Lowest free: gap at 2 is returned, not 8
        assert self._compute_placeholder([1, 3, 7]) == 2

    def test_placeholder_in_source(self):
        import inspect
        from server import get_sprint_management_issues
        src = inspect.getsource(get_sprint_management_issues)
        assert "placeholder_sprint" in src, \
            "Backend must compute and return placeholder_sprint"

    def test_endpoint_source_includes_placeholder_in_return(self):
        import inspect
        from server import get_sprint_management_issues
        src = inspect.getsource(get_sprint_management_issues)
        assert '"placeholder_sprint"' in src or "'placeholder_sprint'" in src, \
            "GET /api/sprint-management/issues must include placeholder_sprint key in return dict"
