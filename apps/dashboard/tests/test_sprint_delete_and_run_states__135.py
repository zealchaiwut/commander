"""Tests for issue #135 — Add Delete button and fix Run/Rerun states per sprint history.

Static checks validate server.py, app.js, and index.html directly.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

SERVER_PY  = Path(__file__).parent.parent / "server.py"
APP_JS     = Path(__file__).parent.parent / "static" / "app.js"
INDEX_HTML = Path(__file__).parent.parent / "static" / "index.html"


# ── AC: Delete button visible on every sprint block ──────────────────────────

class TestDeleteButtonPresence:
    def test_delete_button_rendered_in_sprint_block_html(self):
        """smgmtSprintBlockHtml renders a delete button for every sprint."""
        content = APP_JS.read_text()
        fn_start = content.find("function smgmtSprintBlockHtml(")
        fn_end   = content.find("\nfunction ", fn_start + 1)
        fn_body  = content[fn_start:fn_end]
        assert "smgmt-delete-btn" in fn_body, (
            "smgmtSprintBlockHtml must render a .smgmt-delete-btn"
        )

    def test_delete_button_calls_smgmt_delete_sprint(self):
        """Delete button has onclick handler smgmtDeleteSprint."""
        content = APP_JS.read_text()
        fn_start = content.find("function smgmtSprintBlockHtml(")
        fn_end   = content.find("\nfunction ", fn_start + 1)
        fn_body  = content[fn_start:fn_end]
        assert "smgmtDeleteSprint" in fn_body, (
            "Delete button onclick must call smgmtDeleteSprint"
        )

    def test_delete_button_not_conditional_on_isnext(self):
        """Delete button must appear on all sprint blocks, not just the next one."""
        content = APP_JS.read_text()
        fn_start = content.find("function smgmtSprintBlockHtml(")
        fn_end   = content.find("\nfunction ", fn_start + 1)
        fn_body  = content[fn_start:fn_end]
        # Ensure delete button is not gated inside isNext ternary
        isnext_delete_pattern = re.compile(r'\$\{isNext\s*\?.*smgmt-delete', re.DOTALL)
        assert not isnext_delete_pattern.search(fn_body), (
            "Delete button must not be conditional on isNext"
        )

    def test_delete_button_css_exists(self):
        """.smgmt-delete-btn CSS class is defined in index.html."""
        content = INDEX_HTML.read_text()
        assert ".smgmt-delete-btn" in content, (
            "CSS class .smgmt-delete-btn must be defined"
        )


# ── AC: Delete confirmation modal ────────────────────────────────────────────

class TestDeleteModal:
    def test_delete_modal_exists_in_html(self):
        """Delete confirmation modal is present in index.html."""
        content = INDEX_HTML.read_text()
        assert 'id="smgmt-delete-modal"' in content, (
            "Delete modal element must exist in index.html"
        )

    def test_delete_modal_has_backdrop(self):
        """Delete modal has a backdrop element."""
        content = INDEX_HTML.read_text()
        assert 'id="smgmt-delete-backdrop"' in content

    def test_delete_modal_has_confirm_button(self):
        """Delete modal has a confirm button that calls smgmtDeleteConfirm."""
        content = INDEX_HTML.read_text()
        assert 'id="smgmt-delete-confirm"' in content
        assert "smgmtDeleteConfirm()" in content

    def test_smgmt_delete_sprint_function_exists(self):
        """smgmtDeleteSprint function is defined in app.js."""
        content = APP_JS.read_text()
        assert "function smgmtDeleteSprint(" in content

    def test_smgmt_delete_close_function_exists(self):
        """smgmtDeleteClose function is defined in app.js."""
        content = APP_JS.read_text()
        assert "function smgmtDeleteClose(" in content

    def test_smgmt_delete_confirm_function_exists(self):
        """smgmtDeleteConfirm function is defined in app.js."""
        content = APP_JS.read_text()
        assert "async function smgmtDeleteConfirm(" in content

    def test_delete_modal_shows_ticket_count(self):
        """smgmtDeleteSprint shows how many tickets will be unlabelled."""
        content = APP_JS.read_text()
        fn_start = content.find("function smgmtDeleteSprint(")
        fn_end   = content.find("\nfunction ", fn_start + 1)
        fn_body  = content[fn_start:fn_end]
        assert "ticketCount" in fn_body or "ticket" in fn_body, (
            "smgmtDeleteSprint must communicate ticket impact to the user"
        )


# ── AC: DELETE /api/sprints/{sprint_label} backend endpoint ──────────────────

class TestDeleteSprintEndpoint:
    def test_delete_endpoint_exists(self):
        """DELETE /api/sprints/{sprint_label} endpoint is defined in server.py."""
        content = SERVER_PY.read_text()
        assert '@app.delete("/api/sprints/{sprint_label}")' in content or \
               "@app.delete('/api/sprints/{sprint_label}')" in content, (
            "DELETE /api/sprints/{sprint_label} endpoint must be defined"
        )

    def test_delete_endpoint_validates_sprint_label(self):
        """Backend delete endpoint validates the sprint label format."""
        content = SERVER_PY.read_text()
        fn_start = content.find('def delete_sprint(')
        fn_end   = content.find('\ndef ', fn_start + 1)
        fn_body  = content[fn_start:fn_end]
        assert "_SPRINT_LABEL_RE" in fn_body, (
            "delete_sprint must validate the sprint label with _SPRINT_LABEL_RE"
        )

    def test_delete_endpoint_rejects_running_sprint(self):
        """Backend delete endpoint returns 409 when sprint is running."""
        content = SERVER_PY.read_text()
        fn_start = content.find('def delete_sprint(')
        fn_end   = content.find('\ndef ', fn_start + 1)
        fn_body  = content[fn_start:fn_end]
        assert "_is_sprint_running" in fn_body, (
            "delete_sprint must refuse to delete a running sprint"
        )
        assert "409" in fn_body, (
            "delete_sprint must return HTTP 409 when sprint is running"
        )

    def test_delete_endpoint_unlabels_attached_tickets(self):
        """Backend delete endpoint removes the sprint label from all attached issues."""
        content = SERVER_PY.read_text()
        fn_start = content.find('def delete_sprint(')
        fn_end   = content.find('\ndef ', fn_start + 1)
        fn_body  = content[fn_start:fn_end]
        assert "update_labels" in fn_body, (
            "delete_sprint must call update_labels to remove sprint label from attached tickets"
        )

    def test_delete_endpoint_deletes_github_label(self):
        """Backend delete endpoint deletes the GitHub label itself."""
        content = SERVER_PY.read_text()
        fn_start = content.find('def delete_sprint(')
        fn_end   = content.find('\ndef ', fn_start + 1)
        fn_body  = content[fn_start:fn_end]
        assert "delete_label" in fn_body, (
            "delete_sprint must call github_client.delete_label"
        )

    def test_delete_endpoint_returns_unlabelled_count(self):
        """Backend delete endpoint response includes unlabelled_count."""
        content = SERVER_PY.read_text()
        fn_start = content.find('def delete_sprint(')
        fn_end   = content.find('\ndef ', fn_start + 1)
        fn_body  = content[fn_start:fn_end]
        assert "unlabelled_count" in fn_body, (
            "delete_sprint response must include unlabelled_count"
        )

    def test_delete_endpoint_cleans_up_state_files(self):
        """Backend delete endpoint removes sprint state and goal files."""
        content = SERVER_PY.read_text()
        fn_start = content.find('def delete_sprint(')
        fn_end   = content.find('\ndef ', fn_start + 1)
        fn_body  = content[fn_start:fn_end]
        assert "state.json" in fn_body, (
            "delete_sprint must clean up the sprint state file"
        )

    def test_delete_frontend_uses_delete_method(self):
        """smgmtDeleteConfirm sends a DELETE request to the API."""
        content = APP_JS.read_text()
        fn_start = content.find("async function smgmtDeleteConfirm(")
        fn_end   = content.find("\nfunction ", fn_start + 1)
        fn_body  = content[fn_start:fn_end]
        assert "DELETE" in fn_body, (
            "smgmtDeleteConfirm must use method: 'DELETE'"
        )
        assert "/api/sprints/" in fn_body, (
            "smgmtDeleteConfirm must call /api/sprints/{label}"
        )


# ── AC: Past sprint — Run disabled, Rerun enabled ────────────────────────────

class TestPastSprintRunState:
    def test_can_run_gated_by_has_completed(self):
        """canRun in smgmtSprintBlockHtml is false when hasCompleted is true."""
        content = APP_JS.read_text()
        fn_start = content.find("function smgmtSprintBlockHtml(")
        fn_end   = content.find("\nfunction ", fn_start + 1)
        fn_body  = content[fn_start:fn_end]
        # canRun must incorporate !hasCompleted
        assert "!hasCompleted" in fn_body, (
            "canRun must be gated on !hasCompleted for past-sprint detection"
        )

    def test_rerun_button_enabled_for_past_sprint(self):
        """Rerun button disabled attr uses hasCompleted ternary in the template literal."""
        content = APP_JS.read_text()
        fn_start = content.find("function smgmtSprintBlockHtml(")
        fn_end   = content.find("\nfunction ", fn_start + 1)
        fn_body  = content[fn_start:fn_end]
        # The template literal must render ${hasCompleted ? '' : 'disabled'} on the rerun btn
        assert re.search(r'smgmt-rerun-btn.*\$\{hasCompleted', fn_body, re.DOTALL), (
            "Rerun button in template must have ${hasCompleted ? ...} disabled logic"
        )

    def test_run_button_title_mentions_rerun_for_past_sprint(self):
        """Run button tooltip explains to use Rerun when sprint already ran."""
        content = APP_JS.read_text()
        assert "Rerun" in content or "rerun" in content, (
            "Run button tooltip must mention Rerun option when sprint already ran"
        )

    def test_goal_input_checks_has_completed(self):
        """smgmtGoalInput also gates canRun on hasCompleted (reactive update)."""
        content = APP_JS.read_text()
        fn_start = content.find("function smgmtGoalInput(")
        fn_end   = content.find("\n}", fn_start)
        # Extend to full function
        brace_count = 0
        i = fn_start
        while i < len(content):
            if content[i] == '{':
                brace_count += 1
            elif content[i] == '}':
                brace_count -= 1
                if brace_count == 0:
                    fn_end = i + 1
                    break
            i += 1
        fn_body = content[fn_start:fn_end]
        assert "hasCompleted" in fn_body, (
            "smgmtGoalInput must check hasCompleted when computing canRun"
        )

    def test_update_running_state_checks_has_completed(self):
        """smgmtUpdateRunningState gates run button on hasCompleted (reactive updates)."""
        content = APP_JS.read_text()
        # Find the forEach block that updates .smgmt-run-btn elements — there may be multiple
        # occurrences; find the one that contains the canRun / !running branch logic
        idx = 0
        found = False
        while True:
            pos = content.find("querySelectorAll('.smgmt-run-btn')", idx)
            if pos == -1:
                break
            block = content[pos:pos + 900]
            if "hasCompleted" in block:
                found = True
                break
            idx = pos + 1
        assert found, (
            "smgmtUpdateRunningState must check hasCompleted when updating run button state"
        )


# ── AC: Upcoming sprint — Rerun disabled, Run follows canRun ─────────────────

class TestUpcomingSprintRunState:
    def test_rerun_disabled_when_no_completed_tickets(self):
        """Rerun button disabled when hasCompleted is false (no completed tickets)."""
        content = APP_JS.read_text()
        fn_start = content.find("function smgmtSprintBlockHtml(")
        fn_end   = content.find("\nfunction ", fn_start + 1)
        fn_body  = content[fn_start:fn_end]
        # Should have: ${hasCompleted ? '' : 'disabled'} or similar
        assert "hasCompleted" in fn_body
        assert "disabled" in fn_body

    def test_can_run_requires_tickets_and_goal(self):
        """canRun still requires >= 1 ticket and valid goal for upcoming sprints."""
        content = APP_JS.read_text()
        fn_start = content.find("function smgmtSprintBlockHtml(")
        fn_end   = content.find("\nfunction ", fn_start + 1)
        fn_body  = content[fn_start:fn_end]
        assert "goalValid" in fn_body
        assert "tickets.length" in fn_body

    def test_rerun_update_in_running_state_checks_has_completed(self):
        """smgmtUpdateRunningState rerun forEach also uses hasCompleted."""
        content = APP_JS.read_text()
        rerun_btn_update = content.find("querySelectorAll('.smgmt-rerun-btn')")
        assert rerun_btn_update != -1
        block = content[rerun_btn_update:rerun_btn_update + 700]
        assert "hasCompleted" in block, (
            "smgmtUpdateRunningState must check hasCompleted when updating rerun button state"
        )
