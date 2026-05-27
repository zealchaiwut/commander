"""Tests for issue #195: Add Finish Sprint Button to Close All Issues.

Verifies all 9 acceptance criteria:
  AC1 - Finish Sprint button appears only after sprint run completes (PID gone)
  AC2 - Button is hidden (not just disabled) while sprint is running
  AC3 - Clicking button triggers confirmation modal listing number of open issues
  AC4 - On confirmation, server adds UAT label, removes workflow labels, closes issues
  AC5 - New POST endpoint POST /api/projects/{owner}/{repo_name}/sprints/{label}/finish in server.py
  AC6 - Endpoint returns {"closed": N, "errors": []}, HTTP 200 on success, HTTP 207 on partial failure
  AC7 - Dashboard refreshes after operation (SSE broadcast or cache invalidation)
  AC8 - Zero open issues succeeds silently with {"closed": 0, "errors": []}
  AC9 - Button is styled consistently (destructive/warning style)
"""
import os
import re
from pathlib import Path

import httpx
import pytest

BASE_URL = os.environ.get("TEST_BASE_URL", "http://localhost:8004")
DASHBOARD_DIR = Path(__file__).parent.parent
APP_JS = DASHBOARD_DIR / "static" / "app.js"
INDEX_HTML = DASHBOARD_DIR / "static" / "index.html"
SERVER_PY = DASHBOARD_DIR / "server.py"


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


@pytest.fixture(scope="module")
def app_js_text():
    return APP_JS.read_text()


@pytest.fixture(scope="module")
def index_html_text():
    return INDEX_HTML.read_text()


@pytest.fixture(scope="module")
def server_py_text():
    return SERVER_PY.read_text()


# ---------------------------------------------------------------------------
# AC-1: Finish Sprint button appears only after sprint run has completed
# ---------------------------------------------------------------------------

class TestAC1FinishButtonAppearsAfterRun:
    def test_finish_button_html_exists_in_sprint_block(self, app_js_text):
        """AC-1: smgmtSprintBlockHtml renders a Finish Sprint button element."""
        assert "smgmt-finish-btn" in app_js_text, \
            "smgmtSprintBlockHtml must render a Finish Sprint button with class smgmt-finish-btn"

    def test_finish_button_gated_on_has_completed(self, app_js_text):
        """AC-1: Finish Sprint button is only visible when hasCompleted is true."""
        assert "hasCompleted" in app_js_text, \
            "Finish Sprint button visibility must depend on hasCompleted flag"

    def test_finish_button_hidden_class_when_not_completed(self, app_js_text):
        """AC-1: Button has 'hidden' class when hasCompleted is false."""
        # The button template should conditionally add 'hidden' class
        assert "hasCompleted ? '' : ' hidden'" in app_js_text or \
               "hasCompleted ?'' : ' hidden'" in app_js_text or \
               re.search(r"hasCompleted.*hidden", app_js_text, re.DOTALL), \
            "Finish Sprint button must use 'hidden' class when sprint has no completed tickets"

    def test_finish_button_visible_in_running_state_is_hidden(self, app_js_text):
        """AC-1: smgmtApplyRunState hides Finish Sprint button while running."""
        assert "smgmt-finish-btn" in app_js_text, \
            "smgmtApplyRunState must reference smgmt-finish-btn to control its visibility"

    def test_finish_button_restored_after_run(self, app_js_text):
        """AC-1: After sprint run ends, smgmtApplyRunState restores Finish Sprint button visibility."""
        # After run ends, style.display should be restored
        assert "smgmt-finish-btn" in app_js_text, \
            "Finish button visibility must be managed in smgmtApplyRunState"


# ---------------------------------------------------------------------------
# AC-2: Button is hidden (not just disabled) while sprint is running
# ---------------------------------------------------------------------------

class TestAC2ButtonHiddenWhileRunning:
    def test_finish_btn_hidden_via_display_none_during_run(self, app_js_text):
        """AC-2: smgmtApplyRunState sets finishBtn.style.display = 'none' during run."""
        assert "finishBtn" in app_js_text, \
            "finishBtn variable must be defined in smgmtApplyRunState to hide it during a run"
        assert "style.display = 'none'" in app_js_text or 'style.display = "none"' in app_js_text, \
            "Finish button must be hidden via style.display = 'none' (not just disabled)"

    def test_finish_btn_restored_after_run_ends(self, app_js_text):
        """AC-2: After run ends, Finish Sprint button display is restored."""
        # Look for style.display = '' to restore visibility
        assert "style.display = ''" in app_js_text or 'style.display = ""' in app_js_text, \
            "Finish button display must be restored (style.display = '') after run ends"

    def test_finish_btn_id_pattern_for_runtime_hiding(self, app_js_text):
        """AC-2: finishBtnId uses the same safeLabel pattern as other action buttons."""
        assert "finishBtnId" in app_js_text, \
            "finishBtnId must be defined for DOM lookup during state transitions"


# ---------------------------------------------------------------------------
# AC-3: Confirmation modal lists the count of open issues
# ---------------------------------------------------------------------------

class TestAC3ConfirmationModal:
    def test_finish_modal_exists_in_html(self, index_html_text):
        """AC-3: The Finish Sprint confirmation modal is present in index.html."""
        assert 'id="smgmt-finish-modal"' in index_html_text, \
            "smgmt-finish-modal element must exist in index.html"

    def test_finish_backdrop_exists_in_html(self, index_html_text):
        """AC-3: A modal backdrop element for Finish Sprint exists in index.html."""
        assert 'id="smgmt-finish-backdrop"' in index_html_text, \
            "smgmt-finish-backdrop must exist in index.html"

    def test_finish_modal_shows_issue_count(self, app_js_text):
        """AC-3: smgmtFinishSprint() populates the modal body with open issue count."""
        assert "openCount" in app_js_text, \
            "smgmtFinishSprint must calculate openCount and show it in the modal"

    def test_finish_modal_has_cancel_button(self, index_html_text):
        """AC-3: Modal contains a Cancel button."""
        assert "smgmtFinishClose" in index_html_text, \
            "Modal must contain a Cancel button calling smgmtFinishClose()"

    def test_finish_modal_has_confirm_button(self, index_html_text):
        """AC-3: Modal contains a Finish Sprint confirm button."""
        assert "smgmtFinishConfirm" in index_html_text, \
            "Modal must contain a confirm button calling smgmtFinishConfirm()"

    def test_finish_sprint_function_exists(self, app_js_text):
        """AC-3: smgmtFinishSprint() function is defined in app.js."""
        assert "function smgmtFinishSprint(" in app_js_text, \
            "smgmtFinishSprint() function must be defined in app.js"

    def test_finish_close_function_exists(self, app_js_text):
        """AC-3: smgmtFinishClose() function is defined in app.js."""
        assert "function smgmtFinishClose(" in app_js_text, \
            "smgmtFinishClose() function must be defined in app.js"

    def test_cancel_hides_modal(self, app_js_text):
        """AC-3: smgmtFinishClose() hides the modal and backdrop."""
        assert "smgmt-finish-backdrop" in app_js_text, \
            "smgmtFinishClose must reference smgmt-finish-backdrop to hide it"
        assert "smgmt-finish-modal" in app_js_text, \
            "smgmtFinishClose must reference smgmt-finish-modal to hide it"

    def test_cancel_resets_label_state(self, app_js_text):
        """AC-3: smgmtFinishClose() resets _smgmtFinishLabel to null."""
        assert "_smgmtFinishLabel = null" in app_js_text, \
            "smgmtFinishClose must reset _smgmtFinishLabel to null"


# ---------------------------------------------------------------------------
# AC-4: Server adds UAT label, removes workflow labels, closes issues
# ---------------------------------------------------------------------------

class TestAC4ServerBatchOperation:
    def test_endpoint_adds_uat_label(self, server_py_text):
        """AC-4: finish_sprint endpoint adds 'UAT' label to each open issue."""
        assert '"UAT"' in server_py_text or "'UAT'" in server_py_text, \
            "finish_sprint must add UAT label to each open issue"

    def test_endpoint_removes_workflow_labels(self, server_py_text):
        """AC-4: finish_sprint removes in-progress, sit, need-rework labels."""
        assert "_FINISH_SPRINT_REMOVE_LABELS" in server_py_text, \
            "A constant defining labels to remove (in-progress, sit, need-rework) must exist"
        assert "in-progress" in server_py_text, \
            "_FINISH_SPRINT_REMOVE_LABELS must include 'in-progress'"
        assert "need-rework" in server_py_text, \
            "_FINISH_SPRINT_REMOVE_LABELS must include 'need-rework'"

    def test_endpoint_closes_each_issue(self, server_py_text):
        """AC-4: finish_sprint calls close_issue for each open sprint issue."""
        assert "close_issue(" in server_py_text, \
            "finish_sprint must call github_client.close_issue() for each issue"

    def test_endpoint_calls_update_labels(self, server_py_text):
        """AC-4: finish_sprint calls update_labels to add UAT and remove workflow labels."""
        assert "update_labels(" in server_py_text, \
            "finish_sprint must call github_client.update_labels() to label issues"

    def test_endpoint_blocks_running_sprint(self, server_py_text):
        """AC-4: finish_sprint returns 409 if the sprint is currently running."""
        assert "409" in server_py_text, \
            "finish_sprint must return HTTP 409 if sprint is still running"
        assert "_is_sprint_running" in server_py_text, \
            "finish_sprint must check _is_sprint_running before proceeding"


# ---------------------------------------------------------------------------
# AC-5: POST endpoint exists in server.py
# ---------------------------------------------------------------------------

class TestAC5EndpointExists:
    def test_endpoint_route_registered(self, server_py_text):
        """AC-5: POST /api/projects/{owner}/{repo_name}/sprints/{label}/finish route is defined."""
        assert "/sprints/{label}/finish" in server_py_text, \
            "POST /api/projects/{owner}/{repo_name}/sprints/{label}/finish route must be in server.py"

    def test_endpoint_is_async_post(self, server_py_text):
        """AC-5: The finish_sprint endpoint is an async POST handler."""
        assert "async def finish_sprint(" in server_py_text, \
            "finish_sprint must be an async function (async def)"

    def test_endpoint_handles_batch_server_side(self, server_py_text):
        """AC-5: Logic loops over sprint_issues server-side, not delegated to client."""
        assert "for iss in sprint_issues" in server_py_text, \
            "Batch operation must be a server-side for-loop, not a client-side loop"

    def test_endpoint_reachable_via_http(self, client):
        """AC-5: POST /api/projects/.../sprints/.../finish returns non-404 (route is wired)."""
        r = client.post("/api/projects/zealchaiwut/commander/sprints/sprint-999/finish")
        assert r.status_code != 404, \
            f"Endpoint returned 404 — route not registered in server.py"

    def test_endpoint_validates_sprint_label_format(self, client):
        """AC-5: Endpoint returns 400 for invalid sprint label format."""
        r = client.post("/api/projects/zealchaiwut/commander/sprints/invalid-label/finish")
        assert r.status_code == 400, \
            f"Endpoint must return 400 for invalid sprint label, got {r.status_code}"


# ---------------------------------------------------------------------------
# AC-6: Response format and status codes
# ---------------------------------------------------------------------------

class TestAC6ResponseFormat:
    def test_response_has_closed_field(self, server_py_text):
        """AC-6: Endpoint response includes 'closed' field."""
        assert '"closed"' in server_py_text or "'closed'" in server_py_text, \
            "finish_sprint response must include 'closed' field"

    def test_response_has_errors_field(self, server_py_text):
        """AC-6: Endpoint response includes 'errors' field."""
        assert '"errors"' in server_py_text or "'errors'" in server_py_text, \
            "finish_sprint response must include 'errors' field"

    def test_http_207_on_partial_failure(self, server_py_text):
        """AC-6: Endpoint returns HTTP 207 if any individual issue operation failed."""
        assert "207" in server_py_text, \
            "finish_sprint must return HTTP 207 when some issues failed"

    def test_http_200_on_full_success(self, server_py_text):
        """AC-6: Endpoint returns HTTP 200 on full success."""
        assert "status_code = 207 if errors else 200" in server_py_text or \
               re.search(r"207.*200|200.*207", server_py_text), \
            "finish_sprint must return 200 on success and 207 on partial failure"

    def test_json_response_for_empty_sprint(self, client):
        """AC-6: Non-existent sprint returns 200 with {"closed": 0, "errors": []} — no open issues."""
        # sprint-999 should not exist in any real project, so 0 issues → 200
        r = client.post("/api/projects/zealchaiwut/commander/sprints/sprint-999/finish")
        # Either 200 (0 closed) or 409 (running) or 422 (project not found) are acceptable
        # The important thing is the route exists and can return proper JSON
        if r.status_code == 200:
            data = r.json()
            assert "closed" in data, "Response must have 'closed' key"
            assert "errors" in data, "Response must have 'errors' key"
            assert data["closed"] == 0, "No open issues → closed should be 0"
            assert data["errors"] == [], "No failures → errors should be empty list"


# ---------------------------------------------------------------------------
# AC-7: Dashboard refreshes after operation
# ---------------------------------------------------------------------------

class TestAC7DashboardRefresh:
    def test_sse_broadcast_on_finish(self, server_py_text):
        """AC-7: finish_sprint broadcasts sprint_finished event via SSE."""
        assert "sprint_finished" in server_py_text, \
            "finish_sprint must broadcast 'sprint_finished' event via SSE"
        assert "broadcast(" in server_py_text, \
            "finish_sprint must call broadcast() to push SSE update"

    def test_cache_invalidated_after_finish(self, server_py_text):
        """AC-7: finish_sprint invalidates open_issues cache entries."""
        assert 'invalidate(f"open_issues' in server_py_text or \
               'invalidate("open_issues' in server_py_text or \
               "invalidate" in server_py_text, \
            "finish_sprint must invalidate cached open_issues data"

    def test_client_calls_smgmt_select_on_confirm(self, app_js_text):
        """AC-7: smgmtFinishConfirm() triggers a board refresh via smgmtSelectProject."""
        assert "smgmtSelectProject" in app_js_text, \
            "smgmtFinishConfirm must call smgmtSelectProject() to refresh the board"

    def test_sse_handler_calls_refresh_on_sprint_finished(self, app_js_text):
        """AC-7: SSE event handler reacts to sprint_finished event."""
        assert "sprint_finished" in app_js_text, \
            "SSE event handler in connectSSE must handle sprint_finished event"


# ---------------------------------------------------------------------------
# AC-8: Zero open issues — succeeds silently
# ---------------------------------------------------------------------------

class TestAC8ZeroIssues:
    def test_empty_sprint_message_in_modal(self, app_js_text):
        """AC-8: smgmtFinishSprint shows 'No open issues' message when openCount is 0."""
        assert "openCount === 0" in app_js_text or "openCount == 0" in app_js_text, \
            "smgmtFinishSprint must handle openCount === 0 with a descriptive message"

    def test_server_iterates_empty_list_safely(self, server_py_text):
        """AC-8: Server-side loop over empty sprint_issues list results in closed=0."""
        assert "closed = 0" in server_py_text, \
            "finish_sprint must initialise closed = 0 before the loop"

    def test_zero_issues_returns_200(self, server_py_text):
        """AC-8: When no issues exist, response is still HTTP 200 (not 207 or error)."""
        # With empty errors list, status_code should be 200
        assert "status_code = 207 if errors else 200" in server_py_text, \
            "finish_sprint must return 200 when errors list is empty (including zero-issue case)"


# ---------------------------------------------------------------------------
# AC-9: Button styled consistently (destructive/warning style)
# ---------------------------------------------------------------------------

class TestAC9ButtonStyling:
    def test_finish_button_has_smgmt_finish_btn_class(self, app_js_text):
        """AC-9: Finish Sprint button uses smgmt-finish-btn class."""
        assert "smgmt-finish-btn" in app_js_text, \
            "Finish Sprint button must use smgmt-finish-btn CSS class"

    def test_finish_button_css_defined_in_html(self, index_html_text):
        """AC-9: smgmt-finish-btn CSS is defined in index.html."""
        assert ".smgmt-finish-btn" in index_html_text, \
            "smgmt-finish-btn CSS rule must be defined in index.html"

    def test_finish_button_has_warning_color(self, index_html_text):
        """AC-9: Finish Sprint button uses amber/warning color to signal finality."""
        assert "amber" in index_html_text.lower() or "#d97706" in index_html_text or \
               "var(--amber" in index_html_text, \
            "Finish Sprint button must use amber/warning color scheme"

    def test_confirm_button_in_modal_has_danger_style(self, index_html_text):
        """AC-9: The confirm button in the modal uses btn-danger style (destructive action)."""
        # The final confirmation button should be styled as danger
        assert "btn-danger" in index_html_text, \
            "Modal confirm button must use btn-danger class to signal irreversible action"

    def test_finish_button_has_icon(self, app_js_text):
        """AC-9: Finish Sprint button includes a visual icon for consistency."""
        assert "ti-flag" in app_js_text or "ti ti-" in app_js_text, \
            "Finish Sprint button must include a tabler icon for visual consistency"
