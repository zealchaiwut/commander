"""Tests for issue #273: Restore empty sprint panes as visible drop targets.

Covers all 6 acceptance criteria via static JS analysis + Selenium browser test.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

DASHBOARD_DIR = Path(__file__).parent.parent
APP_JS = DASHBOARD_DIR / "static" / "app.js"


@pytest.fixture(scope="module")
def js() -> str:
    return APP_JS.read_text()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _extract_function(source: str, fn_name: str, window: int = 5000) -> str:
    idx = source.find(f"function {fn_name}(")
    if idx == -1:
        idx = source.find(f"async function {fn_name}(")
    assert idx != -1, f"Function '{fn_name}' not found in app.js"
    return source[idx: idx + window]


# ── AC1: Empty sprint renders full pane (name, "empty" count, Delete, drop area) ──

class TestAC1EmptySprintFullPane:
    def test_empty_sprint_uses_sprint_block_html_not_placeholder(self, js):
        """smgmtRender must NOT use an isEmpty branch to call smgmtPlaceholderBlockHtml inside the map."""
        render_fn = _extract_function(js, "smgmtRender", window=6000)
        map_start = render_fn.find("allSprintNums.map")
        assert map_start != -1
        # Extract just the map callback body (ends at the first }).join)
        map_body_end = render_fn.find("}).join('')", map_start)
        if map_body_end == -1:
            map_body_end = render_fn.find(").join('')", map_start)
        map_callback = render_fn[map_start: map_body_end + 20] if map_body_end != -1 else render_fn[map_start: map_start + 300]
        # The callback must not branch on isEmpty to smgmtPlaceholderBlockHtml
        assert "smgmtPlaceholderBlockHtml" not in map_callback, (
            "smgmtRender map callback must not call smgmtPlaceholderBlockHtml for known empty sprints"
        )
        # The old isEmpty branch must be gone
        assert "isEmpty" not in map_callback or "num, isEmpty" not in map_callback[:50]

    def test_all_sprints_use_smgmtSprintBlockHtml_in_map(self, js):
        """Every sprint in the main map loop (non-placeholder) calls smgmtSprintBlockHtml."""
        render_fn = _extract_function(js, "smgmtRender", window=6000)
        map_start = render_fn.find("allSprintNums.map")
        assert map_start != -1
        map_block = render_fn[map_start: map_start + 300]
        assert "smgmtSprintBlockHtml" in map_block

    def test_sprint_count_shows_empty_when_zero_tickets(self, js):
        """smgmt-sprint-count shows 'empty' when tickets.length === 0."""
        block_fn = _extract_function(js, "smgmtSprintBlockHtml")
        assert "tickets.length === 0 ? 'empty'" in block_fn, (
            "smgmtSprintBlockHtml must render 'empty' text when tickets.length is 0"
        )

    def test_delete_button_present_in_sprint_block(self, js):
        """smgmtSprintBlockHtml renders a Delete button for every sprint including empty ones."""
        block_fn = _extract_function(js, "smgmtSprintBlockHtml", window=5000)
        assert "smgmt-delete-btn" in block_fn
        assert "smgmtDeleteSprint" in block_fn

    def test_empty_state_drop_hint_rendered_when_no_tickets(self, js):
        """smgmtSprintBlockHtml renders a drop-hint element when tickets array is empty."""
        block_fn = _extract_function(js, "smgmtSprintBlockHtml")
        assert "smgmt-drop-hint" in block_fn, (
            "smgmtSprintBlockHtml must include a smgmt-drop-hint element for the empty state"
        )
        assert "Drop tickets here" in block_fn

    def test_drop_zone_wired_on_sprint_block(self, js):
        """smgmtSprintBlockHtml wires ondragover and ondrop on the sprint block div."""
        block_fn = _extract_function(js, "smgmtSprintBlockHtml")
        assert "ondragover=" in block_fn
        assert "ondrop=" in block_fn
        assert "smgmtDropOnSprint" in block_fn


# ── AC2: Empty pane accepts drop → assigns sprint label on GitHub ──────────────

class TestAC2DropAssignsSprintLabel:
    def test_drop_handler_calls_api_to_assign_label(self, js):
        """smgmtDropOnSprint POSTs to the label assignment API endpoint."""
        drop_fn = _extract_function(js, "smgmtDropOnSprint")
        assert "/api/" in drop_fn or "fetch(" in drop_fn, (
            "smgmtDropOnSprint must make a fetch() API call to assign the label"
        )

    def test_drop_on_empty_sprint_promotes_it_to_order(self, js):
        """When dropping onto an empty sprint (not yet in order), it is added to _smgmtData.order."""
        drop_fn = _extract_function(js, "smgmtDropOnSprint")
        assert "_smgmtData.order.push(targetSprintLabel)" in drop_fn, (
            "Drop on empty sprint must push targetSprintLabel into _smgmtData.order"
        )

    def test_wasInOrder_tracks_pre_drop_state(self, js):
        """wasInOrder variable is introduced to track whether the sprint was already in order."""
        drop_fn = _extract_function(js, "smgmtDropOnSprint")
        assert "wasInOrder" in drop_fn


# ── AC3: Dropped ticket appears inside sprint pane immediately ─────────────────

class TestAC3OptimisticRender:
    def test_smgmt_render_called_after_optimistic_update(self, js):
        """smgmtRender() is called immediately after the optimistic data update."""
        drop_fn = _extract_function(js, "smgmtDropOnSprint")
        update_idx = drop_fn.find("iss.sprint = targetNum")
        render_idx = drop_fn.find("smgmtRender()")
        assert update_idx != -1 and render_idx != -1
        assert render_idx > update_idx, (
            "smgmtRender() must be called after the optimistic sprint assignment"
        )

    def test_order_promoted_before_render(self, js):
        """Empty sprint is promoted to _smgmtData.order before smgmtRender() is called."""
        drop_fn = _extract_function(js, "smgmtDropOnSprint")
        promote_idx = drop_fn.find("_smgmtData.order.push(targetSprintLabel)")
        # Use 'smgmtRender();\n' (the actual call with semicolon) to skip occurrences
        # that appear only in comments (e.g. "// after smgmtRender().")
        render_idx = drop_fn.find("smgmtRender();\n")
        if render_idx == -1:
            render_idx = drop_fn.find("    smgmtRender();")
        assert promote_idx != -1 and render_idx != -1, (
            f"promote_idx={promote_idx}, render_idx={render_idx}"
        )
        assert promote_idx < render_idx, (
            "Empty sprint must be added to _smgmtData.order before smgmtRender() is called"
        )


# ── AC4: Run Sprint disabled on empty sprints with tooltip ────────────────────

class TestAC4RunSprintDisabled:
    def test_can_run_requires_at_least_one_ticket(self, js):
        """canRun is false when tickets.length < 1."""
        block_fn = _extract_function(js, "smgmtSprintBlockHtml")
        assert "tickets.length >= 1" in block_fn or "tickets.length > 0" in block_fn, (
            "canRun must require at least one ticket"
        )

    def test_run_btn_disabled_attribute_when_no_tickets(self, js):
        """Run Sprint button has 'disabled' attribute when !canRun."""
        block_fn = _extract_function(js, "smgmtSprintBlockHtml")
        assert "runDisabled" in block_fn
        assert "!canRun ? 'disabled'" in block_fn

    def test_run_btn_tooltip_no_tickets_to_run(self, js):
        """Run Sprint tooltip says 'No tickets to run' when disabled."""
        block_fn = _extract_function(js, "smgmtSprintBlockHtml")
        assert "No tickets to run" in block_fn, (
            "Run Sprint disabled tooltip must read 'No tickets to run'"
        )

    def test_run_btn_tooltip_wired_via_title_attribute(self, js):
        """Tooltip is delivered via the title attribute on the button."""
        block_fn = _extract_function(js, "smgmtSprintBlockHtml")
        assert 'title="${runTitle}"' in block_fn or "title=\"${runTitle}\"" in block_fn


# ── AC5: Delete button removes pane from board ─────────────────────────────────

class TestAC5DeleteEmptySprint:
    def test_delete_button_calls_smgmt_delete_sprint(self, js):
        """smgmt-delete-btn calls smgmtDeleteSprint with the sprint label."""
        block_fn = _extract_function(js, "smgmtSprintBlockHtml", window=5000)
        assert "smgmtDeleteSprint('${label}')" in block_fn

    def test_smgmt_delete_sprint_function_exists(self, js):
        """smgmtDeleteSprint function exists in app.js."""
        assert "function smgmtDeleteSprint(" in js or \
               "async function smgmtDeleteSprint(" in js

    def test_delete_shows_confirmation_modal(self, js):
        """smgmtDeleteSprint shows a confirmation modal (not a silent immediate delete)."""
        del_fn = _extract_function(js, "smgmtDeleteSprint", window=2000)
        # Should show the delete modal or confirmation dialog
        assert "smgmt-delete" in del_fn, (
            "smgmtDeleteSprint must show the delete confirmation modal"
        )

    def test_delete_confirm_refreshes_board(self, js):
        """smgmtDeleteConfirm (the modal confirm handler) calls the API and refreshes the board."""
        confirm_fn = _extract_function(js, "smgmtDeleteConfirm", window=2000)
        refreshes = "smgmtSelectProject" in confirm_fn or "smgmtRefreshBoard" in confirm_fn
        assert refreshes, (
            "smgmtDeleteConfirm must reload the board after deletion"
        )


# ── AC6: Rollback on failed drop reverts order promotion ──────────────────────

class TestAC6RollbackOnError:
    def test_rollback_reverts_order_promotion(self, js):
        """On API failure, the sprint is removed from _smgmtData.order if it was added."""
        drop_fn = _extract_function(js, "smgmtDropOnSprint")
        assert "_smgmtData.order.filter" in drop_fn or \
               "_smgmtData.order = _smgmtData.order.filter" in drop_fn, (
            "Error rollback must remove targetSprintLabel from _smgmtData.order"
        )

    def test_rollback_uses_was_in_order_guard(self, js):
        """Rollback only removes sprint from order when !wasInOrder."""
        drop_fn = _extract_function(js, "smgmtDropOnSprint")
        rollback_idx = drop_fn.rfind("wasInOrder")  # last occurrence = rollback section
        assert rollback_idx != -1
        rollback_block = drop_fn[rollback_idx: rollback_idx + 200]
        assert "filter" in rollback_block or "order" in rollback_block

    def test_render_called_after_rollback(self, js):
        """smgmtRender() is called after rollback to restore original UI state."""
        # Limit window to just smgmtDropOnSprint (not into the next function)
        drop_fn = _extract_function(js, "smgmtDropOnSprint", window=2500)
        # Find the first catch block inside the ticket drop path
        catch_idx = drop_fn.find("catch (e)")
        if catch_idx == -1:
            catch_idx = drop_fn.find("catch(e)")
        assert catch_idx != -1, "No catch block found in smgmtDropOnSprint"
        catch_block = drop_fn[catch_idx: catch_idx + 500]
        assert "smgmtRender()" in catch_block or "smgmtRender();" in catch_block


# ── Selenium: Live browser verification ───────────────────────────────────────

@pytest.mark.selenium
class TestAC6BrowserVerification:
    def test_empty_sprint_pane_renders_in_browser(self, driver):
        """Empty sprint pane renders with correct UI elements in real browser."""
        import time
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        driver.get("http://localhost:8000/project/commander/sprint-mgmt")
        wait = WebDriverWait(driver, 10)

        # Wait for sprint management pane to load
        wait.until(EC.presence_of_element_located((By.CLASS_NAME, "smgmt-sprint-block")))
        time.sleep(1)

        # Check that sprint blocks with empty count ("empty") render if any exist
        blocks = driver.find_elements(By.CLASS_NAME, "smgmt-sprint-block")
        assert len(blocks) > 0, "At least one sprint block (including placeholder) should render"

        # Any sprint with no tickets should show "empty" in its count badge
        for block in blocks:
            if "smgmt-sprint-placeholder" in block.get_attribute("class"):
                continue
            count_spans = block.find_elements(By.CLASS_NAME, "smgmt-sprint-count")
            ticket_cards = block.find_elements(By.CLASS_NAME, "smgmt-ticket-card")
            if len(ticket_cards) == 0 and count_spans:
                assert "empty" in count_spans[0].text, (
                    f"Empty sprint block should show 'empty' in count, got: {count_spans[0].text}"
                )

    def test_empty_sprint_run_button_disabled(self, driver):
        """Run Sprint button is disabled for empty sprints in real browser."""
        import time
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        driver.get("http://localhost:8000/project/commander/sprint-mgmt")
        wait = WebDriverWait(driver, 10)
        wait.until(EC.presence_of_element_located((By.CLASS_NAME, "smgmt-sprint-block")))
        time.sleep(1)

        blocks = driver.find_elements(By.CLASS_NAME, "smgmt-sprint-block")
        for block in blocks:
            if "smgmt-sprint-placeholder" in block.get_attribute("class"):
                continue
            ticket_cards = block.find_elements(By.CLASS_NAME, "smgmt-ticket-card")
            run_btns = block.find_elements(By.CLASS_NAME, "smgmt-run-btn")
            if len(ticket_cards) == 0 and run_btns:
                btn = run_btns[0]
                assert not btn.is_enabled(), "Run Sprint must be disabled for empty sprints"
                tooltip = btn.get_attribute("title")
                assert tooltip and "No tickets" in tooltip, (
                    f"Run Sprint tooltip should mention 'No tickets', got: '{tooltip}'"
                )
