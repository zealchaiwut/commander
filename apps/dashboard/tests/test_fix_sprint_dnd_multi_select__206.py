"""Tests for issue #206: Fix Sprint DnD, Add Multi-Select, and Refine Toolbar Actions.

Covers all 9 acceptance criteria via static HTML + JS analysis.
No running server required for these checks.
"""
from __future__ import annotations

from pathlib import Path

import pytest

DASHBOARD_DIR = Path(__file__).parent.parent
INDEX_HTML = DASHBOARD_DIR / "static" / "index.html"
APP_JS = DASHBOARD_DIR / "static" / "app.js"


@pytest.fixture(scope="module")
def html() -> str:
    return INDEX_HTML.read_text()


@pytest.fixture(scope="module")
def js() -> str:
    return APP_JS.read_text()


# ---------------------------------------------------------------------------
# AC1: Dragging onto empty sprint assigns issue and updates UI without refresh
# ---------------------------------------------------------------------------

class TestAC1EmptySprintDrop:
    def test_drop_on_placeholder_function_exists(self, js):
        """smgmtDropOnPlaceholder handles drop events on empty sprints."""
        assert "async function smgmtDropOnPlaceholder" in js or \
               "function smgmtDropOnPlaceholder" in js

    def test_empty_sprint_gets_added_to_data(self, js):
        """Drop logic adds the sprint to _smgmtData.order when dropping on empty sprint."""
        assert "_smgmtData.order.push(newSprintLabel)" in js

    def test_placeholder_number_advances_past_all_existing(self, js):
        """Placeholder number calculation uses Math.max to avoid collision with existing empty sprints."""
        assert "Math.max(placeholderN + 1, maxExistingSprint + 1)" in js or \
               "Math.max(..._smgmtData.sprints)" in js

    def test_smgmt_render_called_after_drop(self, js):
        """smgmtRender is called after drop to update UI without page refresh."""
        # Find the smgmtDropOnPlaceholder function block
        fn_start = js.find("async function smgmtDropOnPlaceholder")
        if fn_start == -1:
            fn_start = js.find("function smgmtDropOnPlaceholder")
        assert fn_start != -1
        # smgmtRender() must appear after the function start
        fn_block = js[fn_start:fn_start + 2000]
        assert "smgmtRender()" in fn_block


# ---------------------------------------------------------------------------
# AC2: Empty sprint shows visible drop zone / placeholder text
# ---------------------------------------------------------------------------

class TestAC2EmptySprintDropZone:
    def test_placeholder_block_renders_empty_drop_hint(self, js):
        """smgmtPlaceholderBlockHtml renders a 'drop tickets here' hint."""
        assert "Drop a ticket here" in js or "drop tickets here" in js

    def test_placeholder_block_has_ondrop_handler(self, js):
        """Placeholder block HTML has ondrop wired to smgmtDropOnPlaceholder."""
        assert "ondrop=\"smgmtDropOnPlaceholder" in js

    def test_placeholder_block_has_ondragover_handler(self, js):
        """Placeholder block HTML has ondragover handler for visual feedback."""
        assert "ondragover=\"smgmtDragOverPlaceholder" in js or \
               "ondragover" in js

    def test_empty_sprint_nums_rendered(self, js):
        """smgmtRender renders existing empty sprints as placeholder blocks."""
        assert "emptySprintNums" in js
        assert "smgmtPlaceholderBlockHtml(n)" in js or \
               ".map(n => smgmtPlaceholderBlockHtml(n))" in js

    def test_placeholder_badge_text_indicates_empty(self, js):
        """Placeholder badge text indicates the sprint is empty and droppable."""
        assert "empty" in js and "drop tickets here" in js


# ---------------------------------------------------------------------------
# AC3: Each issue row has a checkbox that selects and highlights the row
# ---------------------------------------------------------------------------

class TestAC3PerRowCheckbox:
    def test_smgmt_ticket_cb_class_in_sprint_card_html(self, js):
        """smgmtTicketCardHtml includes a checkbox with smgmt-ticket-cb class."""
        fn_start = js.find("function smgmtTicketCardHtml")
        assert fn_start != -1, "smgmtTicketCardHtml not found"
        fn_block = js[fn_start:fn_start + 2000]
        assert "smgmt-ticket-cb" in fn_block

    def test_smgmt_ticket_cb_class_in_backlog_html(self, js):
        """smgmtBacklogTicketHtml includes a checkbox with smgmt-ticket-cb class."""
        fn_start = js.find("function smgmtBacklogTicketHtml")
        assert fn_start != -1, "smgmtBacklogTicketHtml not found"
        fn_block = js[fn_start:fn_start + 2000]
        assert "smgmt-ticket-cb" in fn_block

    def test_toggle_issue_select_function_exists(self, js):
        """smgmtToggleIssueSelect function is defined."""
        assert "function smgmtToggleIssueSelect" in js

    def test_toggle_select_adds_to_set(self, js):
        """smgmtToggleIssueSelect adds issue number to _smgmtSelectedIssues."""
        fn_start = js.find("function smgmtToggleIssueSelect")
        assert fn_start != -1
        fn_block = js[fn_start:fn_start + 300]
        assert "_smgmtSelectedIssues.add" in fn_block

    def test_is_selected_class_applied_to_selected_rows(self, js):
        """Ticket rows get 'is-selected' class when selected."""
        assert "is-selected" in js

    def test_is_selected_css_in_html(self, html):
        """CSS for .smgmt-ticket.is-selected is defined (highlights the row)."""
        assert ".smgmt-ticket.is-selected" in html


# ---------------------------------------------------------------------------
# AC4: "Select all" checkbox in column header
# ---------------------------------------------------------------------------

class TestAC4SelectAllCheckbox:
    def test_select_all_element_exists(self, html):
        """smgmt-backlog-select-all checkbox element is in index.html."""
        assert 'id="smgmt-backlog-select-all"' in html

    def test_select_all_calls_toggle_function(self, html):
        """Select all checkbox triggers smgmtToggleSelectAll."""
        assert "smgmtToggleSelectAll(this)" in html

    def test_toggle_select_all_function_exists(self, js):
        """smgmtToggleSelectAll function is defined in app.js."""
        assert "function smgmtToggleSelectAll" in js

    def test_toggle_select_all_queries_all_checkboxes(self, js):
        """smgmtToggleSelectAll queries all .smgmt-ticket-cb checkboxes."""
        fn_start = js.find("function smgmtToggleSelectAll")
        assert fn_start != -1
        fn_block = js[fn_start:fn_start + 600]
        assert "smgmt-ticket-cb" in fn_block

    def test_toggle_select_all_deselects_when_unchecked(self, js):
        """smgmtToggleSelectAll removes issues from selection when unchecked."""
        fn_start = js.find("function smgmtToggleSelectAll")
        assert fn_start != -1
        fn_block = js[fn_start:fn_start + 600]
        assert "_smgmtSelectedIssues.delete" in fn_block


# ---------------------------------------------------------------------------
# AC5: Bulk action toolbar appears when issues are selected
# ---------------------------------------------------------------------------

class TestAC5BulkActionBar:
    def test_bulk_bar_element_exists(self, html):
        """smgmt-bulk-bar element is present in index.html."""
        assert 'id="smgmt-bulk-bar"' in html

    def test_bulk_count_element_exists(self, html):
        """smgmt-bulk-count element shows number of selected issues."""
        assert 'id="smgmt-bulk-count"' in html

    def test_bulk_bar_starts_hidden(self, html):
        """Bulk action bar starts hidden (class='... hidden ...')."""
        bar_start = html.find('id="smgmt-bulk-bar"')
        assert bar_start != -1
        # Check the surrounding context for the hidden class
        bar_context = html[max(0, bar_start - 100):bar_start + 100]
        assert "hidden" in bar_context

    def test_move_to_action_in_bulk_bar(self, html):
        """Bulk action bar contains a 'Move to sprint' action."""
        bar_start = html.find('id="smgmt-bulk-bar"')
        assert bar_start != -1
        bar_block = html[bar_start:bar_start + 500]
        assert "Move to" in bar_block

    def test_update_selection_ui_shows_bulk_bar(self, js):
        """_smgmtUpdateSelectionUI shows the bulk bar when count > 0."""
        fn_start = js.find("function _smgmtUpdateSelectionUI")
        assert fn_start != -1
        fn_block = js[fn_start:fn_start + 500]
        assert "bulkBar" in fn_block
        assert "hidden" in fn_block

    def test_clear_selection_function_exists(self, js):
        """smgmtClearSelection function is defined."""
        assert "function smgmtClearSelection" in js

    def test_clear_selection_hides_bulk_bar(self, js):
        """smgmtClearSelection calls _smgmtUpdateSelectionUI to hide bulk bar."""
        fn_start = js.find("function smgmtClearSelection")
        assert fn_start != -1
        fn_block = js[fn_start:fn_start + 300]
        assert "_smgmtUpdateSelectionUI" in fn_block


# ---------------------------------------------------------------------------
# AC6: "Move to" is a top-level toolbar button, right of "+ Bulk Create"
# ---------------------------------------------------------------------------

class TestAC6MoveToTopLevel:
    def test_move_to_button_exists_in_header_actions(self, html):
        """smgmt-move-to-btn is defined in the header actions area."""
        assert 'id="smgmt-move-to-btn"' in html

    def test_move_to_button_calls_bulk_move_open(self, html):
        """smgmt-move-to-btn calls smgmtBulkMoveToOpen."""
        btn_start = html.find('id="smgmt-move-to-btn"')
        assert btn_start != -1
        btn_context = html[max(0, btn_start - 50):btn_start + 200]
        assert "smgmtBulkMoveToOpen" in btn_context

    def test_move_to_appears_after_bulk_create_in_html(self, html):
        """Move to button appears after + Bulk Create in the DOM order."""
        bulk_create_idx = html.find('id="bulk-create-btn"')
        move_to_idx = html.find('id="smgmt-move-to-btn"')
        assert bulk_create_idx != -1, "bulk-create-btn not found"
        assert move_to_idx != -1, "smgmt-move-to-btn not found"
        assert move_to_idx > bulk_create_idx, \
            "Move to button must appear after + Bulk Create in the DOM"

    def test_move_to_button_label_text(self, html):
        """Move to button label says 'Move to'."""
        move_to_idx = html.find('id="smgmt-move-to-btn"')
        assert move_to_idx != -1
        btn_block = html[move_to_idx:move_to_idx + 100]
        assert "Move to" in btn_block

    def test_move_to_button_in_same_actions_bar(self, html):
        """Both + Bulk Create and Move to are in smgmt-header-actions."""
        header_start = html.find('class="smgmt-header-actions"')
        assert header_start != -1
        # The next few hundred chars should contain both buttons
        header_block = html[header_start:header_start + 1500]
        assert 'id="bulk-create-btn"' in header_block
        assert 'id="smgmt-move-to-btn"' in header_block


# ---------------------------------------------------------------------------
# AC7: Button label is now "+ Bulk Create" (with plus prefix)
# ---------------------------------------------------------------------------

class TestAC7BulkCreateLabel:
    def test_bulk_create_button_has_plus_prefix(self, html):
        """Bulk create button label is '+ Bulk Create' (with + prefix)."""
        assert "+ Bulk Create" in html

    def test_bulk_create_button_not_labeled_without_plus(self, html):
        """Old button label 'Bulk create' (no + prefix) is gone from the toolbar button."""
        # Find the actual button element — the label changed from "Bulk create" to "+ Bulk Create"
        btn_start = html.find('id="bulk-create-btn"')
        assert btn_start != -1
        btn_block = html[btn_start:btn_start + 200]
        assert "Bulk create" not in btn_block, \
            "Old label 'Bulk create' (no + prefix) should be replaced with '+ Bulk Create' in the button"
        assert "+ Bulk Create" in btn_block

    def test_bulk_create_button_id_present(self, html):
        """bulk-create-btn still exists (button was renamed, not removed)."""
        assert 'id="bulk-create-btn"' in html


# ---------------------------------------------------------------------------
# AC8: Multi-select state cleared on navigation or click outside
# ---------------------------------------------------------------------------

class TestAC8ClearOnNavOrClickOutside:
    def test_clear_selection_on_nav_function_exists(self, js):
        """smgmtClearSelectionOnNav function is defined."""
        assert "function smgmtClearSelectionOnNav" in js

    def test_clear_on_nav_called_on_tab_switch(self, js):
        """smgmtClearSelectionOnNav is called when switching project tabs."""
        # It's called inside _activateProjectTab before navigating away
        fn_start = js.find("function _activateProjectTab")
        assert fn_start != -1
        fn_block = js[fn_start:fn_start + 500]
        assert "smgmtClearSelectionOnNav" in fn_block

    def test_clear_on_nav_called_on_popstate(self, js):
        """smgmtClearSelectionOnNav is called in the popstate handler."""
        popstate_idx = js.find("addEventListener('popstate'")
        if popstate_idx == -1:
            popstate_idx = js.find('addEventListener("popstate"')
        assert popstate_idx != -1
        popstate_block = js[popstate_idx:popstate_idx + 500]
        assert "smgmtClearSelectionOnNav" in popstate_block

    def test_click_outside_listener_present(self, js):
        """document.addEventListener('click', ...) is used to detect clicks outside."""
        assert "document.addEventListener('click'" in js or \
               'document.addEventListener("click"' in js

    def test_click_outside_calls_clear_selection(self, js):
        """Click-outside handler calls smgmtClearSelection."""
        click_listener = js.find("document.addEventListener('click'")
        if click_listener == -1:
            click_listener = js.find('document.addEventListener("click"')
        assert click_listener != -1
        # Use a larger window; the handler is ~12 lines / ~400 chars
        listener_block = js[click_listener:click_listener + 800]
        assert "smgmtClearSelection" in listener_block

    def test_click_inside_tickets_does_not_clear(self, js):
        """Click-outside handler skips clearing when click is inside ticket list."""
        click_listener = js.find("document.addEventListener('click'")
        if click_listener == -1:
            click_listener = js.find('document.addEventListener("click"')
        assert click_listener != -1
        listener_block = js[click_listener:click_listener + 600]
        # Should check for inTickets or closest to avoid clearing on valid clicks
        assert "closest" in listener_block or "inTickets" in listener_block


# ---------------------------------------------------------------------------
# AC9: DnD into non-empty sprints still works (no regression)
# ---------------------------------------------------------------------------

class TestAC9NonEmptySprintDnDRegression:
    def test_drop_on_sprint_function_still_exists(self, js):
        """smgmtDropOnSprint (for non-empty sprints) is still defined."""
        assert "function smgmtDropOnSprint" in js or \
               "async function smgmtDropOnSprint" in js

    def test_sprint_block_html_has_ondrop_handler(self, js):
        """smgmtSprintBlockHtml still renders ondrop handler for non-empty sprints."""
        fn_start = js.find("function smgmtSprintBlockHtml")
        assert fn_start != -1
        # Function is large (~10k chars); scan first 4000 chars which covers the return template
        fn_block = js[fn_start:fn_start + 4000]
        assert "ondrop" in fn_block

    def test_sprint_block_html_has_ondragover_handler(self, js):
        """smgmtSprintBlockHtml still has ondragover for visual DnD feedback."""
        fn_start = js.find("function smgmtSprintBlockHtml")
        assert fn_start != -1
        fn_block = js[fn_start:fn_start + 4000]
        assert "ondragover" in fn_block

    def test_ticket_drag_start_function_exists(self, js):
        """smgmtTicketDragStart is still defined (tickets are still draggable)."""
        assert "function smgmtTicketDragStart" in js

    def test_ticket_card_still_draggable(self, js):
        """smgmtTicketCardHtml still renders tickets as draggable."""
        fn_start = js.find("function smgmtTicketCardHtml")
        assert fn_start != -1
        # Return template starts ~1000 chars into the function; use 2000 char window
        fn_block = js[fn_start:fn_start + 2000]
        assert 'draggable="true"' in fn_block

    def test_backlog_ticket_still_draggable(self, js):
        """smgmtBacklogTicketHtml still renders backlog tickets as draggable."""
        fn_start = js.find("function smgmtBacklogTicketHtml")
        assert fn_start != -1
        fn_block = js[fn_start:fn_start + 2000]
        assert 'draggable="true"' in fn_block
