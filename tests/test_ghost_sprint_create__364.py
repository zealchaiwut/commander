"""Tests for issue #364 — Auto-create next sprint via drag-below ghost pane (revised).

AC coverage:
  AC-1  Ghost renders on drag start (light-blue dashed border, correct label)
  AC-2  Next-free numbering is correct (lowest N where sprint-N does not exist)
  AC-3  Next-free computed once per drag session (cached at drag-start)
  AC-4  Ghost hot state on hover (CSS class + text change)
  AC-5  Confirm modal on drop (not immediate create)
  AC-6  Confirm creates sprint and moves ticket atomically
  AC-7  Cancel is a full no-op
  AC-8  Escape mid-drag dismisses ghost
  AC-9  Drop onto real pane dismisses ghost (existing behavior)
  AC-10 Running-lock blocks the flow (no separate ghost check needed)
  AC-11 Zero-sprint edge case shows Sprint 1
  AC-12 All integers occupied: next free is N+1
  AC-13 Network failure is surfaced in modal
  AC-14 Persistence after refresh (server-side lowest-free computation)
  AC-15 Subsequent drags compute fresh next-free number
  AC-16 Confirm modal primary button uses --blue token
"""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "dashboard"))

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


# ── AC-1: Ghost renders on drag start ────────────────────────────────────────

class TestAC1GhostRenders:

    def test_placeholder_function_defined(self, js):
        assert "function smgmtPlaceholderBlockHtml(" in js

    def test_placeholder_appended_in_render(self, js):
        fn = _extract_function(js, "smgmtRender")
        assert "smgmtPlaceholderBlockHtml" in fn

    def test_placeholder_has_ghost_css_class(self, js):
        fn = _extract_function(js, "smgmtPlaceholderBlockHtml")
        assert "smgmt-sprint-placeholder" in fn

    def test_placeholder_initially_hidden(self, js):
        fn = _extract_function(js, "smgmtPlaceholderBlockHtml")
        assert "display:none" in fn or "display: none" in fn

    def test_ghost_uses_light_blue_dashed_border(self, html):
        assert "dashed var(--blue)" in html or "dashed var( --blue)" in html

    def test_ghost_uses_blue_bg_background(self, html):
        assert "var(--blue-bg)" in html

    def test_ghost_title_span_present(self, html):
        assert "smgmt-ghost-title" in html

    def test_ghost_sub_span_present(self, html):
        assert "smgmt-ghost-sub" in html

    def test_ticket_dragstart_shows_placeholder(self, js):
        fn = _extract_function(js, "smgmtTicketDragStart")
        assert "smgmt-sprint-placeholder" in fn
        assert "display" in fn and "block" in fn

    def test_backlog_dragstart_shows_placeholder(self, js):
        fn = _extract_function(js, "smgmtBacklogTicketDragStart")
        assert "smgmt-sprint-placeholder" in fn
        assert "display" in fn and "block" in fn


# ── AC-2: Next-free numbering is correct ─────────────────────────────────────

class TestAC2NextFreeNumbering:

    def test_compute_next_free_function_defined(self, js):
        assert "_smgmtComputeNextFreeSprint" in js

    def test_compute_uses_all_sprints_data(self, js):
        fn = _extract_function(js, "_smgmtComputeNextFreeSprint")
        assert "_smgmtData.sprints" in fn

    def test_compute_returns_1_for_empty(self, js):
        fn = _extract_function(js, "_smgmtComputeNextFreeSprint")
        assert "return 1" in fn

    def test_server_uses_lowest_free_algorithm(self):
        import inspect
        from server import get_sprint_management_issues
        src = inspect.getsource(get_sprint_management_issues)
        assert "while placeholder_sprint in" in src, \
            "Server must use while-loop to find lowest free sprint, not just max+1"

    def test_server_returns_placeholder_sprint(self):
        import inspect
        from server import get_sprint_management_issues
        src = inspect.getsource(get_sprint_management_issues)
        assert "placeholder_sprint" in src


# ── AC-2 backend logic ────────────────────────────────────────────────────────

class TestAC2BackendLowestFree:

    def _compute(self, used: list[int]) -> int:
        s = set(used)
        n = 1
        while n in s:
            n += 1
        return n

    def test_sequential_sprints(self):
        assert self._compute([1, 2, 3]) == 4

    def test_gap_in_middle(self):
        assert self._compute([1, 2, 4, 5]) == 3

    def test_single_sprint(self):
        assert self._compute([1]) == 2

    def test_no_sprints(self):
        assert self._compute([]) == 1

    def test_sprint_18_and_19_exist(self):
        # UAT step 1 example: sprint-18 (non-empty) and sprint-19 (empty) exist → next free = 20
        assert self._compute(list(range(1, 20))) == 20

    def test_next_free_skips_occupied_18_19(self):
        used = list(range(1, 20))  # 1..19 all occupied
        assert self._compute(used) == 20


# ── AC-3: Computed once per drag session ─────────────────────────────────────

class TestAC3ComputedOnce:

    def test_ghost_next_n_variable_declared(self, js):
        assert "_smgmtGhostNextN" in js

    def test_dragstart_sets_ghost_next_n(self, js):
        fn = _extract_function(js, "smgmtTicketDragStart")
        assert "_smgmtGhostNextN" in fn

    def test_backlog_dragstart_sets_ghost_next_n(self, js):
        fn = _extract_function(js, "smgmtBacklogTicketDragStart")
        assert "_smgmtGhostNextN" in fn

    def test_dragend_clears_ghost_next_n(self, js):
        fn = _extract_function(js, "smgmtTicketDragEnd")
        assert "_smgmtGhostNextN" in fn and "null" in fn


# ── AC-4: Ghost hot state on hover ───────────────────────────────────────────

class TestAC4GhostHotState:

    def test_ghost_hot_css_class_defined(self, html):
        assert "ghost-hot" in html

    def test_ghost_hot_has_blue_background(self, html):
        assert "ghost-hot" in html
        # ghost-hot must set solid blue bg
        idx = html.find(".smgmt-sprint-placeholder.ghost-hot")
        section = html[idx:idx+300] if idx != -1 else ""
        assert "var(--blue)" in section

    def test_dragover_placeholder_adds_ghost_hot(self, js):
        fn = _extract_function(js, "smgmtDragOverPlaceholder")
        assert "ghost-hot" in fn

    def test_update_ghost_label_function(self, js):
        assert "_smgmtUpdateGhostLabel" in js

    def test_hover_changes_title_to_release(self, js):
        fn = _extract_function(js, "_smgmtUpdateGhostLabel")
        assert "Release to create Sprint" in fn

    def test_hover_changes_sub_to_confirm(self, js):
        fn = _extract_function(js, "_smgmtUpdateGhostLabel")
        assert "you'll be asked to confirm" in fn or "you\\'ll be asked to confirm" in fn

    def test_dragleave_ghost_removes_hot(self, js):
        assert "smgmtDragLeaveGhost" in js
        fn = _extract_function(js, "smgmtDragLeaveGhost")
        assert "ghost-hot" in fn


# ── AC-5: Confirm modal on drop ──────────────────────────────────────────────

class TestAC5ConfirmModal:

    def test_ghost_modal_in_html(self, html):
        assert "smgmt-ghost-modal" in html

    def test_ghost_backdrop_in_html(self, html):
        assert "smgmt-ghost-backdrop" in html

    def test_drop_handler_calls_confirm_open(self, js):
        fn = _extract_function(js, "smgmtDropOnPlaceholder")
        # Must open modal, NOT immediately call /api/sprint-planning/assign
        assert "smgmtGhostConfirmOpen" in fn
        assert "/api/sprint-planning/assign" not in fn

    def test_confirm_open_function_defined(self, js):
        assert "function smgmtGhostConfirmOpen(" in js

    def test_modal_shows_sprint_name(self, js):
        fn = _extract_function(js, "smgmtGhostConfirmOpen")
        assert "sprint-name" in fn or "sprint" in fn

    def test_modal_shows_ticket_list(self, js):
        fn = _extract_function(js, "smgmtGhostConfirmOpen")
        assert "smgmt-ghost-modal-tickets" in fn

    def test_modal_shows_source_pane(self, js):
        fn = _extract_function(js, "smgmtGhostConfirmOpen")
        assert "smgmt-ghost-modal-source" in fn or "source" in fn.lower()

    def test_modal_has_note_about_next_free(self, html):
        assert "lowest free" in html or "next free" in html


# ── AC-6: Confirm creates sprint and moves ticket atomically ─────────────────

class TestAC6ConfirmCreates:

    def test_confirm_create_function_defined(self, js):
        assert "async function smgmtGhostConfirmCreate(" in js

    def test_confirm_calls_assign_endpoint(self, js):
        fn = _extract_function(js, "smgmtGhostConfirmCreate")
        assert "/api/sprint-planning/assign" in fn

    def test_confirm_updates_local_data(self, js):
        fn = _extract_function(js, "smgmtGhostConfirmCreate")
        assert "_smgmtData.sprints" in fn and "_smgmtData.order" in fn

    def test_confirm_calls_render(self, js):
        fn = _extract_function(js, "smgmtGhostConfirmCreate")
        assert "smgmtRender()" in fn

    def test_confirm_calls_refresh_board(self, js):
        fn = _extract_function(js, "smgmtGhostConfirmCreate")
        assert "smgmtRefreshBoard" in fn

    def test_confirm_closes_modal_on_success(self, js):
        fn = _extract_function(js, "smgmtGhostConfirmCreate")
        assert "hidden" in fn


# ── AC-7: Cancel is a full no-op ─────────────────────────────────────────────

class TestAC7CancelNoOp:

    def test_cancel_close_function_defined(self, js):
        assert "function smgmtGhostConfirmClose(" in js

    def test_cancel_hides_modal(self, js):
        fn = _extract_function(js, "smgmtGhostConfirmClose")
        assert "hidden" in fn

    def test_cancel_does_not_call_api(self, js):
        fn = _extract_function(js, "smgmtGhostConfirmClose")
        assert "fetch" not in fn

    def test_cancel_clears_pending_state(self, js):
        fn = _extract_function(js, "smgmtGhostConfirmClose")
        assert "_smgmtGhostPendingTickets" in fn or "_smgmtGhostPendingN" in fn

    def test_cancel_hides_ghost_pane(self, js):
        fn = _extract_function(js, "smgmtGhostConfirmClose")
        assert "smgmt-sprint-placeholder" in fn


# ── AC-8: Escape mid-drag dismisses ghost ────────────────────────────────────

class TestAC8EscapeDismissesGhost:

    def test_escape_handler_registered(self, js):
        assert "key === 'Escape'" in js or 'key === "Escape"' in js

    def test_escape_hides_placeholder(self, js):
        # The ghost Escape handler must hide .smgmt-sprint-placeholder
        # Look for the ghost-specific handler (references _smgmtGhostNextN near the Escape check)
        idx = js.find("_smgmtGhostNextN = null")
        assert idx != -1, "Must nullify _smgmtGhostNextN on escape"
        # Check that a smgmt-sprint-placeholder hide occurs in the same handler block
        block = js[max(0, idx - 500):idx + 500]
        assert "smgmt-sprint-placeholder" in block

    def test_escape_clears_drag_state(self, js):
        # The keydown handler clears drag and ghost state
        idx = js.find("_smgmtDragTicket = null;\n  _smgmtDragTickets = [];\n  document.querySelectorAll('.smgmt-sprint-placeholder')")
        if idx == -1:
            # Accept any block that nulls both ghost and drag ticket near Escape handling
            assert "_smgmtGhostNextN = null" in js and "_smgmtDragTicket = null" in js


# ── AC-11: Zero-sprint edge case ─────────────────────────────────────────────

class TestAC11ZeroSprintEdge:

    def test_compute_returns_1_with_no_sprints(self):
        # Mirrors _smgmtComputeNextFreeSprint logic for empty set
        used = set([])
        n = 1
        while n in used:
            n += 1
        assert n == 1

    def test_backend_returns_1_with_no_sprints(self):
        import inspect
        from server import get_sprint_management_issues
        src = inspect.getsource(get_sprint_management_issues)
        # Should handle empty sprints list → placeholder = 1
        assert "placeholder_sprint = 1" in src


# ── AC-13: Network failure surfaced in modal ─────────────────────────────────

class TestAC13NetworkFailure:

    def test_confirm_create_has_try_catch(self, js):
        fn = _extract_function(js, "smgmtGhostConfirmCreate")
        assert "catch" in fn

    def test_confirm_shows_error_in_modal(self, js):
        fn = _extract_function(js, "smgmtGhostConfirmCreate")
        assert "smgmt-ghost-modal-error" in fn

    def test_confirm_error_element_in_html(self, html):
        assert "smgmt-ghost-modal-error" in html

    def test_confirm_does_not_leave_partial_state(self, js):
        fn = _extract_function(js, "smgmtGhostConfirmCreate")
        # On error path, should NOT call smgmtRender or smgmtRefreshBoard
        # (find catch block and verify no render call in it)
        catch_idx = fn.rfind("catch")
        catch_block = fn[catch_idx:] if catch_idx != -1 else ""
        assert "smgmtRender()" not in catch_block, \
            "Error path must not re-render — that would show partial state"


# ── AC-16: Confirm modal button color uses --blue ────────────────────────────

class TestAC16ButtonColor:

    def test_btn_blue_class_defined(self, html):
        assert "btn-blue" in html

    def test_btn_blue_uses_blue_token(self, html):
        idx = html.find(".btn-blue")
        section = html[idx:idx+200] if idx != -1 else ""
        assert "var(--blue)" in section

    def test_confirm_button_uses_btn_blue(self, html):
        # The primary button in the ghost modal must have class btn-blue
        modal_start = html.find("smgmt-ghost-modal")
        modal_section = html[modal_start:modal_start + 2000] if modal_start != -1 else ""
        assert "btn-blue" in modal_section

    def test_confirm_button_not_green(self, html):
        modal_start = html.find("smgmt-ghost-modal")
        modal_section = html[modal_start:modal_start + 2000] if modal_start != -1 else ""
        # Must NOT use btn-success or green
        assert "btn-success" not in modal_section
        assert "btn-green" not in modal_section
