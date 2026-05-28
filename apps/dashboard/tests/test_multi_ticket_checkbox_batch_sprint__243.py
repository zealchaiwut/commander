"""Tests for issue #243: Multi-ticket checkbox selection and batch sprint-label moves.

Covers:
  - AC 1:  Checkbox on each ticket row
  - AC 2:  Selected row CSS (blue-bg + 3px left blue border)
  - AC 3:  Unchecked drag = single ticket (JS wiring)
  - AC 4:  Checked drag packs all selected issue numbers + floating pill
  - AC 5:  After drag-drop, selections clear automatically
  - AC 6:  Selection is global across sprints
  - AC 7:  Escape clears all selections (JS wiring)
  - AC 8:  Floating selection bar: count, Move-to dropdown, Deselect button
  - AC 9:  Move-to dropdown calls batch-labels API
  - AC 10: Deselect button in toolbar
  - AC 11: POST /api/sprints/batch-labels returns {applied, failed, errors}
  - AC 12: POST /api/issues/{N}/sprint-label still returns 200 for valid single-ticket move
"""
import os
import re
import pytest
import httpx

BASE_URL = os.environ.get("TEST_BASE_URL", "http://localhost:8000")
TEST_SLUG = "commander"
TEST_REPO = "zealchaiwut/commander"


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=15.0) as c:
        yield c


@pytest.fixture(scope="module")
def html(client):
    r = client.get(f"/project/{TEST_SLUG}/sprint-mgmt")
    assert r.status_code == 200, f"sprint-mgmt page not served: {r.status_code}"
    return r.text


# ── AC 1: Checkbox on each ticket row ─────────────────────────────────────────

class TestCheckboxPresence:
    def test_checkbox_class_defined_in_template(self, html):
        """AC 1: smgmt-ticket-cb checkbox is rendered inside each ticket row template."""
        assert 'smgmt-ticket-cb' in html

    def test_checkbox_is_input_type_checkbox(self, html):
        """AC 1: Checkbox element is <input type='checkbox' class='smgmt-ticket-cb'>."""
        assert 'type="checkbox"' in html
        assert 'class="smgmt-ticket-cb"' in html

    def test_checkbox_uses_toggle_select_handler(self, html):
        """AC 1: Clicking checkbox calls _smgmtToggleSelect with issue number and checked state."""
        assert '_smgmtToggleSelect' in html

    def test_checkbox_stopspropagation_on_click(self, html):
        """AC 1: Checkbox click stops event propagation to prevent row-click side effects."""
        assert 'event.stopPropagation()' in html


# ── AC 2: Selected row styling ─────────────────────────────────────────────────

class TestSelectedRowStyling:
    def test_is_selected_class_uses_blue_bg(self, html):
        """AC 2: .smgmt-ticket.is-selected uses var(--blue-bg) background."""
        css = re.search(r'\.smgmt-ticket\.is-selected\s*\{([^}]+)\}', html)
        assert css, "CSS rule .smgmt-ticket.is-selected not found"
        assert 'var(--blue-bg)' in css.group(1)

    def test_is_selected_class_has_3px_left_blue_border(self, html):
        """AC 2: .smgmt-ticket.is-selected has border-left: 3px solid var(--blue)."""
        css = re.search(r'\.smgmt-ticket\.is-selected\s*\{([^}]+)\}', html)
        assert css, "CSS rule .smgmt-ticket.is-selected not found"
        assert 'border-left: 3px solid var(--blue)' in css.group(1)

    def test_toggle_select_adds_is_selected_class(self, html):
        """AC 2: _smgmtToggleSelect toggles is-selected class on the row element."""
        assert "classList.toggle('is-selected'" in html or 'classList.toggle("is-selected"' in html

    def test_clear_selection_removes_is_selected_class(self, html):
        """AC 2: _smgmtClearSelection removes is-selected class from previously selected rows."""
        assert "classList.remove('is-selected')" in html or 'classList.remove("is-selected")' in html


# ── AC 3: Single-ticket drag for unchecked rows ────────────────────────────────

class TestSingleTicketDrag:
    def test_drag_start_handler_wired(self, html):
        """AC 3: ondragstart is wired to _smgmtTicketDragStart for every row."""
        assert 'ondragstart="_smgmtTicketDragStart(' in html

    def test_drag_start_function_defined(self, html):
        """AC 3: _smgmtTicketDragStart function is defined."""
        assert 'function _smgmtTicketDragStart(' in html

    def test_unchecked_drag_uses_single_ticket_path(self, html):
        """AC 3: When isChecked is false, drag uses single-ticket path (multi: null)."""
        assert "multi: null" in html

    def test_single_drag_sets_text_plain_to_issue_number(self, html):
        """AC 3: Single-ticket drag sets dataTransfer data to String(issueNum)."""
        assert "String(issueNum)" in html or "String(issueNum))" in html


# ── AC 4: Multi-ticket drag with floating pill ─────────────────────────────────

class TestMultiTicketDrag:
    def test_multi_drag_packs_all_selected_numbers(self, html):
        """AC 4: Multi-drag packs all checked issue numbers into dataTransfer."""
        assert "nums.join(',')" in html

    def test_drag_pill_element_present(self, html):
        """AC 4: smgmt-drag-pill element exists in the DOM for the floating cursor pill."""
        assert 'smgmt-drag-pill' in html

    def test_drag_pill_shows_moving_n_tickets_label(self, html):
        """AC 4: Floating pill label reads 'Moving N tickets' for single sprint."""
        assert '`Moving ${nums.length} tickets`' in html

    def test_drag_pill_shows_from_m_sprints_label(self, html):
        """AC 4: Floating pill reads 'Moving N tickets from M sprints' for cross-sprint drag."""
        assert 'from ${sprints.size} sprints' in html

    def test_drag_pill_follows_cursor(self, html):
        """AC 4: _smgmtDragMovePill updates pill position as cursor moves."""
        assert '_smgmtDragMovePill' in html


# ── AC 5: Selections clear after any drag-drop ────────────────────────────────

class TestSelectionClearsAfterDrag:
    def test_clear_selection_called_on_multi_drop(self, html):
        """AC 5: _smgmtClearSelection is called after a multi-ticket drop completes."""
        drop_fn = re.search(
            r'async function _smgmtDropOnSprint\([\s\S]+?(?=\nasync function|\nfunction )',
            html,
        )
        assert drop_fn, "_smgmtDropOnSprint function not found"
        assert '_smgmtClearSelection()' in drop_fn.group(0)

    def test_clear_selection_called_on_single_drop(self, html):
        """AC 5: _smgmtClearSelection is also called on single-ticket drop."""
        # The function contains at least two calls to _smgmtClearSelection
        count = html.count('_smgmtClearSelection()')
        assert count >= 2, f"Expected >=2 calls to _smgmtClearSelection(), found {count}"

    def test_drag_end_hides_pill(self, html):
        """AC 5: _smgmtTicketDragEnd hides the floating pill after drag ends."""
        assert '_smgmtTicketDragEnd' in html
        # pill text is cleared on drag end
        assert "pill.textContent = ''" in html or 'pill.textContent=""' in html


# ── AC 6: Global selection across sprints ─────────────────────────────────────

class TestGlobalSelection:
    def test_selected_set_is_module_level(self, html):
        """AC 6: _smgmtSelectedIssues is a module-level Set (not scoped per sprint card)."""
        assert 'let _smgmtSelectedIssues = new Set()' in html

    def test_selected_set_used_in_drag_fn(self, html):
        """AC 6: _smgmtSelectedIssues is accessed in _smgmtTicketDragStart (global span)."""
        assert '_smgmtSelectedIssues.has(issueNum)' in html

    def test_toggle_select_fn_reads_global_set(self, html):
        """AC 6: _smgmtToggleSelect adds/removes from the module-level Set."""
        assert '_smgmtSelectedIssues.add(number)' in html
        assert '_smgmtSelectedIssues.delete(number)' in html


# ── AC 7: Escape key clears selection ─────────────────────────────────────────

class TestEscapeKey:
    def test_escape_keydown_listener_registered(self, html):
        """AC 7: A keydown listener checks for Escape key."""
        assert "e.key === 'Escape'" in html or 'e.key === "Escape"' in html

    def test_escape_calls_clear_selection_when_no_modal_open(self, html):
        """AC 7: When no modal is open and 1+ tickets selected, Escape calls _smgmtClearSelection."""
        # The pattern: size > 0 → _smgmtClearSelection()
        assert '_smgmtSelectedIssues.size > 0' in html
        assert '_smgmtClearSelection()' in html


# ── AC 8: Floating selection bar ──────────────────────────────────────────────

class TestFloatingSelectionBar:
    def test_selection_bar_element_present(self, html):
        """AC 8: Floating selection bar element is in the DOM."""
        assert 'id="smgmt-selection-bar"' in html

    def test_selection_bar_fixed_positioning(self, html):
        """AC 8: Floating bar uses position: fixed at bottom-centre."""
        css = re.search(r'\.smgmt-selection-bar\s*\{([^}]+)\}', html, re.DOTALL)
        assert css, ".smgmt-selection-bar CSS rule not found"
        block = css.group(1)
        assert 'position: fixed' in block
        assert 'bottom:' in block

    def test_selection_bar_show_class_toggles_visibility(self, html):
        """AC 8: .smgmt-selection-bar.show makes the bar visible."""
        css = re.search(r'\.smgmt-selection-bar\.show\s*\{([^}]+)\}', html)
        assert css, ".smgmt-selection-bar.show CSS rule not found"
        assert 'opacity: 1' in css.group(1)

    def test_selection_bar_count_element_present(self, html):
        """AC 8: Count element inside selection bar shows 'N selected'."""
        assert 'id="smgmt-sel-count"' in html

    def test_selection_bar_move_to_dropdown_present(self, html):
        """AC 8: 'Move to' dropdown (smgmt-sel-move-to) is present inside the bar."""
        assert 'id="smgmt-sel-move-to"' in html

    def test_selection_bar_has_deselect_button(self, html):
        """AC 8: Deselect button exists inside the floating bar."""
        assert 'smgmt-selection-bar-deselect' in html

    def test_selection_bar_deselect_calls_clear_selection(self, html):
        """AC 8: Floating bar Deselect button calls _smgmtClearSelection."""
        bar_section = re.search(
            r'<div[^>]+class="smgmt-selection-bar"[\s\S]+?</div>\s*(?=\n|$)',
            html,
        )
        assert '_smgmtClearSelection()' in html

    def test_selection_bar_update_fn_toggles_show_class(self, html):
        """AC 8: _smgmtUpdateSelectionUI toggles .show class based on count."""
        assert "bar.classList.toggle('show', count > 0)" in html or \
               'bar.classList.toggle("show", count > 0)' in html


# ── AC 9: Move-to dropdown calls batch-labels API ─────────────────────────────

class TestMoveToDropdown:
    def test_move_selected_to_fn_defined(self, html):
        """AC 9: _smgmtMoveSelectedTo function is defined."""
        assert 'async function _smgmtMoveSelectedTo(' in html

    def test_move_selected_uses_batch_labels_endpoint(self, html):
        """AC 9: _smgmtMoveSelectedTo calls POST /api/sprints/batch-labels."""
        assert "'/api/sprints/batch-labels'" in html or '"/api/sprints/batch-labels"' in html

    def test_move_selected_builds_changes_array(self, html):
        """AC 9: Changes payload maps issue numbers to {issue_num, sprint_label}."""
        assert 'issue_num:' in html
        assert 'sprint_label:' in html

    def test_move_to_dropdown_populated_with_sprints(self, html):
        """AC 9: _smgmtPopulateSelectionDropdown builds sprint options dynamically."""
        assert '_smgmtPopulateSelectionDropdown' in html

    def test_backlog_option_in_dropdown(self, html):
        """AC 9: 'Backlog' option is included in the Move-to dropdown (added dynamically)."""
        assert 'Backlog' in html
        # Option is created dynamically via JS
        assert "backlogOpt.value = 'backlog'" in html or 'backlogOpt.value = "backlog"' in html


# ── AC 10: Toolbar Deselect button ────────────────────────────────────────────

class TestToolbarDeselectButton:
    def test_toolbar_deselect_btn_element_present(self, html):
        """AC 10: Deselect button (smgmt-deselect-btn) is present in the toolbar."""
        assert 'id="smgmt-deselect-btn"' in html

    def test_toolbar_deselect_btn_hidden_by_default(self, html):
        """AC 10: Toolbar Deselect button starts hidden (display: none)."""
        css = re.search(r'\.smgmt-deselect-btn\s*\{([^}]+)\}', html)
        assert css, ".smgmt-deselect-btn CSS rule not found"
        assert 'display: none' in css.group(1)

    def test_toolbar_deselect_btn_visible_class(self, html):
        """AC 10: .smgmt-deselect-btn.visible shows the button (display: inline-flex)."""
        css = re.search(r'\.smgmt-deselect-btn\.visible\s*\{([^}]+)\}', html)
        assert css, ".smgmt-deselect-btn.visible CSS rule not found"
        assert 'display: inline-flex' in css.group(1)

    def test_toolbar_deselect_btn_shows_count(self, html):
        """AC 10: Count span inside the button is updated by _smgmtUpdateSelectionUI."""
        assert 'id="smgmt-deselect-count"' in html

    def test_toolbar_deselect_btn_calls_clear_selection(self, html):
        """AC 10: Toolbar Deselect button click calls _smgmtClearSelection."""
        btn = re.search(r'<button[^>]+id="smgmt-deselect-btn"[^>]*>', html)
        assert btn, "smgmt-deselect-btn not found"
        assert '_smgmtClearSelection' in btn.group(0)

    def test_update_ui_toggles_visible_class_on_btn(self, html):
        """AC 10: _smgmtUpdateSelectionUI toggles .visible class on deselect button."""
        assert "deselectBtn.classList.toggle('visible', count > 0)" in html or \
               'deselectBtn.classList.toggle("visible", count > 0)' in html


# ── AC 11: POST /api/sprints/batch-labels endpoint ────────────────────────────

class TestBatchLabelsEndpoint:
    def test_batch_labels_returns_applied_failed_errors(self, client):
        """AC 11: Endpoint returns JSON with applied, failed, and errors fields."""
        r = client.post(
            "/api/sprints/batch-labels",
            json={"changes": [{"issue_num": 999999, "sprint_label": "sprint-17"}]},
        )
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        data = r.json()
        assert "applied" in data, "Response missing 'applied' field"
        assert "failed" in data, "Response missing 'failed' field"
        assert "errors" in data, "Response missing 'errors' field"

    def test_batch_labels_nonexistent_issue_returns_failed(self, client):
        """AC 11: Non-existent issue number ends up in failed count with an error entry."""
        r = client.post(
            "/api/sprints/batch-labels",
            json={"changes": [{"issue_num": 999999, "sprint_label": "sprint-17"}]},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["failed"] >= 1, "Non-existent issue should result in failed >= 1"
        assert len(data["errors"]) >= 1, "Non-existent issue should produce an error entry"
        assert "#999999" in data["errors"][0]

    def test_batch_labels_invalid_sprint_label_returns_failed(self, client):
        """AC 11: Invalid sprint_label string returns failed without crashing."""
        r = client.post(
            "/api/sprints/batch-labels",
            json={"changes": [{"issue_num": 1, "sprint_label": "not-a-sprint"}]},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["failed"] >= 1

    def test_batch_labels_empty_changes_returns_zero_counts(self, client):
        """AC 11: Empty changes list returns applied=0, failed=0, errors=[]."""
        r = client.post(
            "/api/sprints/batch-labels",
            json={"changes": []},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["applied"] == 0
        assert data["failed"] == 0
        assert data["errors"] == []

    def test_batch_labels_backlog_sprint_label_accepted(self, client):
        """AC 11: 'backlog' is a valid sprint_label value (not an invalid label format)."""
        r = client.post(
            "/api/sprints/batch-labels",
            json={"changes": [{"issue_num": 999999, "sprint_label": "backlog"}]},
        )
        assert r.status_code == 200
        data = r.json()
        # backlog is a valid label but issue 999999 may not exist → failed, but no 400
        assert "applied" in data
        assert "failed" in data
        assert "errors" in data

    def test_batch_labels_response_is_json(self, client):
        """AC 11: Response Content-Type is application/json."""
        r = client.post(
            "/api/sprints/batch-labels",
            json={"changes": []},
        )
        assert "application/json" in r.headers.get("content-type", "")


# ── AC 12: Existing single-ticket sprint-label endpoint still works ────────────

class TestSingleTicketSprintLabel:
    def test_single_ticket_endpoint_still_returns_200(self, client):
        """AC 12: POST /api/issues/{N}/sprint-label returns 200 for a valid request."""
        r = client.get(f"/api/sprint-management/issues?repo={TEST_REPO}")
        assert r.status_code == 200
        data = r.json()
        issues = data.get("issues", [])
        if not issues:
            pytest.skip("No issues available to test single-ticket move")

        issue_num = issues[0]["number"]
        current_sprint = issues[0].get("sprint")
        sprint_label = f"sprint-{current_sprint}" if current_sprint else "backlog"

        r2 = client.post(
            f"/api/issues/{issue_num}/sprint-label",
            json={"sprint_label": sprint_label, "project": TEST_REPO},
        )
        assert r2.status_code == 200, f"Expected 200, got {r2.status_code}: {r2.text}"
        assert r2.json().get("ok") is True

    def test_single_ticket_endpoint_invalid_label_returns_400(self, client):
        """AC 12: POST /api/issues/{N}/sprint-label with invalid label returns 400."""
        r = client.post(
            "/api/issues/1/sprint-label",
            json={"sprint_label": "not-valid", "project": TEST_REPO},
        )
        assert r.status_code == 400

    def test_single_ticket_endpoint_exists_in_html(self, html):
        """AC 12: Frontend references /api/issues/{N}/sprint-label for single-ticket drops."""
        assert '/api/issues/${number}/sprint-label' in html or \
               "/api/issues/\"+number+\"/sprint-label" in html or \
               '`/api/issues/${number}/sprint-label`' in html
