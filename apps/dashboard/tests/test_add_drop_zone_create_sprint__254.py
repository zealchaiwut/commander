"""Tests for issue #254: Add drop zone below last sprint to create next sprint.

AC1 - A dashed create-zone drop target is visible below the lowest sprint pane
      when a drag is in progress
AC2 - The proposed sprint number is the lowest sprint-NN that does not already exist
      (i.e. max existing + 1)
AC3 - Dropping tickets shows a confirm prompt: "Create sprint-N and move N ticket(s)
      into it?" with Confirm and Cancel actions
AC4 - On confirm: sprint-NN label created on GitHub, new sprint pane created locally,
      tickets get sprint-NN label and prior sprint label stripped
AC5 - The new sprint pane renders in the correct numeric order
AC6 - The new sprint pane can be empty-capable (consistent with existing behaviour)
AC7 - The drop zone is disabled (not rendered or non-interactive) while any sprint is
      in a running state
AC8 - Cancelling the confirm prompt leaves all dragged tickets unchanged
AC9 - The GitHub label verifiably exists on the repository after confirming
"""
from __future__ import annotations

from pathlib import Path

import pytest

DASHBOARD_DIR = Path(__file__).parent.parent
INDEX_HTML    = DASHBOARD_DIR / "static" / "index.html"
APP_JS        = DASHBOARD_DIR / "static" / "app.js"


@pytest.fixture(scope="module")
def html() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def js() -> str:
    return APP_JS.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# AC1: Dashed create-zone drop target visible during drag
# ---------------------------------------------------------------------------

class TestAC1CreateZoneElement:
    def test_create_zone_element_exists(self, html):
        """HTML: smgmt-create-zone element is present in index.html."""
        assert 'id="smgmt-create-zone"' in html

    def test_create_zone_starts_hidden(self, html):
        """HTML: create-zone starts with the hidden class (not visible at rest)."""
        idx = html.find('id="smgmt-create-zone"')
        assert idx != -1
        block = html[max(0, idx - 100):idx + 200]
        assert 'hidden' in block

    def test_create_zone_has_drag_handlers(self, html):
        """HTML: create-zone has ondragover, ondragleave, ondrop handlers."""
        idx = html.find('id="smgmt-create-zone"')
        assert idx != -1
        block = html[idx:idx + 300]
        assert 'ondragover' in block
        assert 'ondragleave' in block
        assert 'ondrop' in block

    def test_create_zone_label_element(self, html):
        """HTML: smgmt-create-zone-label span exists for dynamic sprint number text."""
        assert 'id="smgmt-create-zone-label"' in html

    def test_create_zone_css_dashed_border(self, html):
        """CSS: .smgmt-create-zone uses a dashed border style."""
        assert '.smgmt-create-zone' in html
        idx = html.find('.smgmt-create-zone')
        block = html[idx:idx + 300]
        assert 'dashed' in block

    def test_create_zone_css_drag_over_highlight(self, html):
        """CSS: .smgmt-create-zone.drag-over highlights the zone in blue."""
        assert '.smgmt-create-zone.drag-over' in html

    def test_create_zone_positioned_below_sprints(self, html):
        """HTML: create-zone appears after smgmt-body (below sprint blocks)."""
        body_idx = html.find('id="smgmt-body"')
        zone_idx = html.find('id="smgmt-create-zone"')
        assert body_idx != -1 and zone_idx != -1
        assert zone_idx > body_idx

    def test_drag_start_shows_create_zone(self, js):
        """JS: smgmtTicketDragStart shows the create-zone when board is not locked."""
        assert 'smgmt-create-zone' in js
        # The drag start function should remove 'hidden' from the zone
        drag_start_idx = js.find('function smgmtTicketDragStart')
        assert drag_start_idx != -1
        block = js[drag_start_idx:drag_start_idx + 1500]
        assert 'smgmt-create-zone' in block
        assert 'hidden' in block

    def test_drag_end_hides_create_zone(self, js):
        """JS: smgmtTicketDragEnd hides the create-zone."""
        drag_end_idx = js.find('function smgmtTicketDragEnd')
        assert drag_end_idx != -1
        block = js[drag_end_idx:drag_end_idx + 900]
        assert 'smgmt-create-zone' in block
        assert 'hidden' in block


# ---------------------------------------------------------------------------
# AC2: Proposed sprint number is lowest sprint-NN not already existing
# ---------------------------------------------------------------------------

class TestAC2NextSprintNumber:
    def test_create_zone_next_n_variable(self, js):
        """JS: _smgmtCreateZoneNextN state variable tracks the next sprint number."""
        assert '_smgmtCreateZoneNextN' in js

    def test_next_n_uses_max_plus_one(self, js):
        """JS: next sprint number is computed as Math.max(...existing) + 1."""
        # The create zone computation uses max of existing sprint numbers + 1
        assert 'Math.max(' in js
        # Should reference _smgmtData.sprints for existing sprint numbers
        drag_start_idx = js.find('function smgmtTicketDragStart')
        assert drag_start_idx != -1
        block = js[drag_start_idx:drag_start_idx + 1500]
        assert '_smgmtData.sprints' in block or '_smgmtCreateZoneNextN' in block

    def test_next_n_assigned_to_global(self, js):
        """JS: _smgmtCreateZoneNextN is set during drag start."""
        drag_start_idx = js.find('function smgmtTicketDragStart')
        assert drag_start_idx != -1
        block = js[drag_start_idx:drag_start_idx + 1500]
        assert '_smgmtCreateZoneNextN' in block

    def test_label_text_includes_sprint_number(self, js):
        """JS: create-zone label shows 'sprint-N' with the computed number."""
        assert 'smgmt-create-zone-label' in js
        assert 'sprint-' in js


# ---------------------------------------------------------------------------
# AC3: Confirm prompt on drop
# ---------------------------------------------------------------------------

class TestAC3ConfirmPrompt:
    def test_create_sprint_modal_exists(self, html):
        """HTML: smgmt-create-sprint-modal element is present."""
        assert 'id="smgmt-create-sprint-modal"' in html

    def test_create_sprint_modal_has_confirm_button(self, html):
        """HTML: confirm modal has a Confirm button."""
        idx = html.find('id="smgmt-create-sprint-modal"')
        assert idx != -1
        block = html[idx:idx + 800]
        assert 'smgmtCreateSprintConfirm' in block

    def test_create_sprint_modal_has_cancel_button(self, html):
        """HTML: confirm modal has a Cancel button."""
        idx = html.find('id="smgmt-create-sprint-modal"')
        assert idx != -1
        block = html[idx:idx + 600]
        assert 'smgmtCreateSprintCancel' in block

    def test_create_sprint_msg_element(self, html):
        """HTML: smgmt-create-sprint-msg element holds the confirm message text."""
        assert 'id="smgmt-create-sprint-msg"' in html

    def test_create_sprint_backdrop_exists(self, html):
        """HTML: modal backdrop exists for the create sprint modal."""
        assert 'id="smgmt-create-sprint-backdrop"' in html

    def test_show_create_sprint_modal_function(self, js):
        """JS: _smgmtShowCreateSprintModal function is defined."""
        assert 'function _smgmtShowCreateSprintModal' in js

    def test_confirm_message_format(self, js):
        """JS: confirm message includes 'Create sprint-' and 'ticket' text."""
        modal_fn_idx = js.find('function _smgmtShowCreateSprintModal')
        assert modal_fn_idx != -1
        block = js[modal_fn_idx:modal_fn_idx + 500]
        assert 'Create sprint-' in block
        assert 'ticket' in block

    def test_confirm_message_pluralises_tickets(self, js):
        """JS: message pluralises 'ticket(s)' based on count via ternary."""
        modal_fn_idx = js.find('function _smgmtShowCreateSprintModal')
        assert modal_fn_idx != -1
        block = js[modal_fn_idx:modal_fn_idx + 500]
        # Should have ternary for singular/plural: "ticket" + (n !== 1 ? 's' : '')
        assert "ticket" in block
        assert "!= 1" in block or "!== 1" in block or "? 's'" in block

    def test_drop_on_create_zone_calls_modal(self, js):
        """JS: smgmtDropOnCreateZone calls _smgmtShowCreateSprintModal."""
        drop_fn_idx = js.find('async function smgmtDropOnCreateZone')
        assert drop_fn_idx != -1
        block = js[drop_fn_idx:drop_fn_idx + 900]
        assert '_smgmtShowCreateSprintModal' in block


# ---------------------------------------------------------------------------
# AC4: On confirm — label created, pane added, labels updated
# ---------------------------------------------------------------------------

class TestAC4ConfirmAction:
    def test_create_sprint_confirm_function(self, js):
        """JS: smgmtCreateSprintConfirm async function is defined."""
        assert 'async function smgmtCreateSprintConfirm' in js

    def test_confirm_calls_sprint_planning_assign(self, js):
        """JS: on confirm, calls /api/sprint-planning/assign for each ticket."""
        confirm_idx = js.find('async function smgmtCreateSprintConfirm')
        assert confirm_idx != -1
        block = js[confirm_idx:confirm_idx + 1500]
        assert '/api/sprint-planning/assign' in block

    def test_confirm_adds_sprint_to_data(self, js):
        """JS: on confirm, new sprint is added to _smgmtData.sprints and .order."""
        confirm_idx = js.find('async function smgmtCreateSprintConfirm')
        assert confirm_idx != -1
        block = js[confirm_idx:confirm_idx + 1500]
        assert '_smgmtData.sprints' in block
        assert '_smgmtData.order' in block

    def test_confirm_updates_issue_sprint(self, js):
        """JS: on confirm, ticket issue.sprint is updated to the new sprint number."""
        confirm_idx = js.find('async function smgmtCreateSprintConfirm')
        assert confirm_idx != -1
        block = js[confirm_idx:confirm_idx + 1500]
        assert '.sprint = nextN' in block

    def test_confirm_refreshes_board(self, js):
        """JS: on confirm, smgmtRefreshBoard is called to sync with GitHub."""
        confirm_idx = js.find('async function smgmtCreateSprintConfirm')
        assert confirm_idx != -1
        block = js[confirm_idx:confirm_idx + 1500]
        assert 'smgmtRefreshBoard' in block

    def test_confirm_clears_selection(self, js):
        """JS: on confirm, multi-select selection is cleared."""
        confirm_idx = js.find('async function smgmtCreateSprintConfirm')
        assert confirm_idx != -1
        block = js[confirm_idx:confirm_idx + 1500]
        assert '_smgmtSelectedIssues.clear' in block

    def test_confirm_handles_multi_ticket(self, js):
        """JS: confirm loops over all ticketNums, not just the first ticket."""
        confirm_idx = js.find('async function smgmtCreateSprintConfirm')
        assert confirm_idx != -1
        block = js[confirm_idx:confirm_idx + 1500]
        assert 'ticketNums' in block
        assert 'for (' in block or 'forEach' in block

    def test_confirm_rollback_on_error(self, js):
        """JS: on API error, optimistic changes are rolled back."""
        confirm_idx = js.find('async function smgmtCreateSprintConfirm')
        assert confirm_idx != -1
        block = js[confirm_idx:confirm_idx + 2000]
        assert 'catch' in block
        assert 'Rollback' in block or 'rollback' in block.lower()


# ---------------------------------------------------------------------------
# AC5: New sprint pane renders in correct numeric order
# ---------------------------------------------------------------------------

class TestAC5SprintOrder:
    def test_confirm_sorts_sprints_array(self, js):
        """JS: after adding new sprint, sprints array is sorted numerically."""
        confirm_idx = js.find('async function smgmtCreateSprintConfirm')
        assert confirm_idx != -1
        block = js[confirm_idx:confirm_idx + 1000]
        assert '.sort(' in block

    def test_confirm_updates_placeholder(self, js):
        """JS: placeholder_sprint is updated to max + 1 after creating new sprint."""
        confirm_idx = js.find('async function smgmtCreateSprintConfirm')
        assert confirm_idx != -1
        block = js[confirm_idx:confirm_idx + 1500]
        assert 'placeholder_sprint' in block


# ---------------------------------------------------------------------------
# AC7: Drop zone disabled while any sprint is running
# ---------------------------------------------------------------------------

class TestAC7RunningStateDisable:
    def test_drop_on_create_zone_checks_running(self, js):
        """JS: smgmtDropOnCreateZone guards against running sprints."""
        drop_fn_idx = js.find('async function smgmtDropOnCreateZone')
        assert drop_fn_idx != -1
        block = js[drop_fn_idx:drop_fn_idx + 500]
        assert '_smgmtAllRunning' in block

    def test_drop_returns_early_if_running(self, js):
        """JS: smgmtDropOnCreateZone returns early when any sprint is running."""
        drop_fn_idx = js.find('async function smgmtDropOnCreateZone')
        assert drop_fn_idx != -1
        block = js[drop_fn_idx:drop_fn_idx + 500]
        # Should have a guard that checks running state and returns
        assert 'anyRunning' in block or '_smgmtAllRunning' in block
        assert 'return' in block

    def test_drag_start_skips_create_zone_when_running(self, js):
        """JS: smgmtTicketDragStart does not show create-zone when board is locked."""
        drag_start_idx = js.find('function smgmtTicketDragStart')
        assert drag_start_idx != -1
        block = js[drag_start_idx:drag_start_idx + 1000]
        # Should check running state before showing create-zone
        assert '_smgmtAllRunning' in block or '_czAnyRunning' in block

    def test_apply_run_state_hides_create_zone(self, js):
        """JS: smgmtApplyRunState hides the create-zone while any sprint is running."""
        # The create-zone hide code is in Pass 3 of smgmtApplyRunState (deep in the fn)
        # Search the whole JS for the specific hide pattern
        assert 'createZone254' in js or (
            'smgmt-create-zone' in js and 'smgmtApplyRunState' in js
        )
        # Verify the hide is in the same block as the running state lock logic
        hide_idx = js.find("Hide create-zone while board is locked")
        assert hide_idx != -1, "Expected 'Hide create-zone while board is locked' comment"
        block = js[hide_idx:hide_idx + 200]
        assert 'smgmt-create-zone' in block
        assert 'hidden' in block


# ---------------------------------------------------------------------------
# AC8: Cancel leaves tickets unchanged
# ---------------------------------------------------------------------------

class TestAC8CancelAction:
    def test_cancel_function_defined(self, js):
        """JS: smgmtCreateSprintCancel function is defined."""
        assert 'function smgmtCreateSprintCancel' in js

    def test_cancel_only_hides_modal(self, js):
        """JS: smgmtCreateSprintCancel only hides the modal — no API calls."""
        cancel_idx = js.find('function smgmtCreateSprintCancel')
        assert cancel_idx != -1
        # Find the end of the cancel function (next function or closing brace)
        block = js[cancel_idx:cancel_idx + 400]
        assert 'hidden' in block
        # Should NOT call fetch or sprint-planning API
        assert 'fetch' not in block
        assert 'sprint-planning' not in block

    def test_cancel_hides_backdrop(self, js):
        """JS: smgmtCreateSprintCancel hides the modal backdrop."""
        cancel_idx = js.find('function smgmtCreateSprintCancel')
        assert cancel_idx != -1
        block = js[cancel_idx:cancel_idx + 400]
        assert 'smgmt-create-sprint-backdrop' in block


# ---------------------------------------------------------------------------
# AC9: GitHub label exists after confirm (API call verifiable)
# ---------------------------------------------------------------------------

class TestAC9GitHubLabelCreated:
    def test_assign_api_creates_label(self, js):
        """JS: the /api/sprint-planning/assign endpoint is called on confirm,
        which creates the sprint label on GitHub if it doesn't exist."""
        confirm_idx = js.find('async function smgmtCreateSprintConfirm')
        assert confirm_idx != -1
        block = js[confirm_idx:confirm_idx + 1500]
        assert '/api/sprint-planning/assign' in block

    def test_assign_api_passes_sprint_number(self, js):
        """JS: the API call includes the sprint number in the request body."""
        confirm_idx = js.find('async function smgmtCreateSprintConfirm')
        assert confirm_idx != -1
        block = js[confirm_idx:confirm_idx + 1500]
        assert 'sprint: nextN' in block

    def test_multi_ticket_move_handles_selected(self, js):
        """JS: when selected issues exist and dragged ticket is in selection,
        all selected tickets are moved to the new sprint."""
        drop_fn_idx = js.find('async function smgmtDropOnCreateZone')
        assert drop_fn_idx != -1
        block = js[drop_fn_idx:drop_fn_idx + 1000]
        assert '_smgmtSelectedIssues' in block
        assert 'ticketsToMove' in block
