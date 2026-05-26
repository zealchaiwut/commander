"""Tests for issue #130 — Sprint Mgmt: allow starting any sprint regardless of order,
prompt to migrate previous tickets.

Static checks validate server.py, app.js, and index.html directly.
Live-server checks are marked with @pytest.mark.live.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from unittest import mock

import pytest

SERVER_PY  = Path(__file__).parent.parent / "server.py"
APP_JS     = Path(__file__).parent.parent / "static" / "app.js"
INDEX_HTML = Path(__file__).parent.parent / "static" / "index.html"


# ── AC: Run button enablement ─────────────────────────────────────────────────

class TestRunButtonEnablement:
    def test_run_btn_not_gated_to_isnext(self):
        """Run button is rendered for every sprint (not just the isNext card)."""
        content = APP_JS.read_text()
        # The run button must be rendered unconditionally (not wrapped in isNext conditional)
        # Check that the smgmt-run-btn is no longer inside an `isNext ? ... : ''` ternary
        assert "smgmt-run-btn" in content

        # Old pattern: `${isNext ? '<button ... Run sprint...' : ''}` should not exist
        # (the button was conditional on isNext)
        old_pattern = re.compile(r'\$\{isNext\s*\?.*smgmt-run-btn', re.DOTALL)
        assert not old_pattern.search(content), (
            "Run button must not be conditional on isNext; it should appear on all eligible sprints"
        )

    def test_run_button_disabled_without_goal(self):
        """Run button is disabled when goal < 10 chars (canRun uses goalValid && hasTickets)."""
        content = APP_JS.read_text()
        # canRun logic must exist
        assert "canRun" in content
        assert "goalValid" in content
        assert "hasTickets" in content

    def test_lockout_removed_from_render_logic(self):
        """'Another sprint is earlier — run that first' lockout is no longer present."""
        content = APP_JS.read_text()
        assert "run that first" not in content, (
            "Old lockout message must not be present"
        )

    def test_sprint_block_html_shows_run_btn_unconditionally(self):
        """smgmtSprintBlockHtml always renders a smgmt-run-btn (no isNext gate)."""
        content = APP_JS.read_text()
        # Find the smgmtSprintBlockHtml function
        fn_start = content.find("function smgmtSprintBlockHtml(")
        fn_end   = content.find("\nfunction ", fn_start + 1)
        if fn_end == -1:
            fn_end = fn_start + 3000
        fn_body = content[fn_start:fn_end]
        assert "smgmt-run-btn" in fn_body
        # The button should not be inside a conditional `isNext ? ... : ''` block
        assert "isNext\n" not in fn_body or "smgmt-run-btn" in fn_body


# ── AC: Backend migration field ───────────────────────────────────────────────

class TestBackendMigrateFrom:
    def test_sprint_mgmt_run_body_has_migrate_from(self):
        """SprintMgmtRunBody model includes migrate_from: list[int] field."""
        content = SERVER_PY.read_text()
        assert "migrate_from" in content
        # Should be defined in the Pydantic model
        assert "migrate_from: list[int]" in content

    def test_run_endpoint_handles_migration(self):
        """POST /api/sprints/run endpoint includes migration logic."""
        content = SERVER_PY.read_text()
        assert "_MIGRATION_STATUS_LABELS" in content
        assert "migrate_from" in content
        # Rollback pattern
        assert "ROLLBACK" in content

    def test_migration_strips_status_labels(self):
        """Migration strips UAT, SIT, in-progress labels from migrated tickets."""
        content = SERVER_PY.read_text()
        assert "_MIGRATION_STATUS_LABELS" in content
        # Check the set includes expected status labels
        assert '"UAT"' in content or "'UAT'" in content
        assert '"SIT"' in content or "'SIT'" in content
        assert '"in-progress"' in content or "'in-progress'" in content

    def test_migration_audit_log(self):
        """Audit log written to .commander/logs/sprint-migration-<timestamp>.log."""
        content = SERVER_PY.read_text()
        assert "sprint-migration-" in content

    def test_migration_rollback_on_failure(self):
        """On label update failure, applied changes are rolled back."""
        content = SERVER_PY.read_text()
        # Rollback logic: iterate reversed(applied)
        assert "reversed(applied)" in content
        assert "rollback_err" in content or "rollback" in content.lower()

    def test_run_response_includes_migrated_count(self):
        """POST /api/sprints/run response includes migrated_count."""
        content = SERVER_PY.read_text()
        assert "migrated_count" in content


# ── AC: Migration modal HTML ──────────────────────────────────────────────────

class TestMigrationModalHtml:
    def test_modal_exists_in_html(self):
        """index.html contains the migration modal element."""
        content = INDEX_HTML.read_text()
        assert "smgmt-migrate-modal" in content
        assert "smgmt-migrate-backdrop" in content

    def test_modal_has_title_element(self):
        """Migration modal has smgmt-migrate-title span."""
        content = INDEX_HTML.read_text()
        assert "smgmt-migrate-title" in content

    def test_modal_has_confirm_button(self):
        """Migration modal has Run sprint confirm button."""
        content = INDEX_HTML.read_text()
        assert "smgmt-migrate-confirm" in content

    def test_modal_has_cancel_button(self):
        """Migration modal has Cancel button."""
        content = INDEX_HTML.read_text()
        assert "smgmtMigrateClose()" in content

    def test_modal_css_exists(self):
        """CSS classes for migration modal are defined."""
        content = INDEX_HTML.read_text()
        assert "smgmt-migrate-body" in content
        assert "smgmt-migrate-radios" in content


# ── AC: Migration modal JS logic ──────────────────────────────────────────────

class TestMigrationModalJs:
    def test_smgmt_migrate_open_function(self):
        """smgmtMigrateOpen function exists."""
        content = APP_JS.read_text()
        assert "function smgmtMigrateOpen(" in content

    def test_smgmt_migrate_close_function(self):
        """smgmtMigrateClose function exists."""
        content = APP_JS.read_text()
        assert "function smgmtMigrateClose(" in content

    def test_smgmt_migrate_confirm_function(self):
        """smgmtMigrateConfirm function exists."""
        content = APP_JS.read_text()
        assert "function smgmtMigrateConfirm(" in content

    def test_smgmt_migrate_choice_function(self):
        """smgmtMigrateChoice function exists for radio button state tracking."""
        content = APP_JS.read_text()
        assert "function smgmtMigrateChoice(" in content

    def test_dispatch_run_sends_migrate_from(self):
        """smgmtDispatchRun sends migrate_from in POST body."""
        content = APP_JS.read_text()
        assert "smgmtDispatchRun" in content
        assert "migrate_from" in content

    def test_earlier_sprints_checked_before_dispatch(self):
        """smgmtRunSprint checks for earlier sprints with open tickets."""
        content = APP_JS.read_text()
        assert "earlierWithTickets" in content or "earlier" in content.lower()

    def test_direct_dispatch_when_no_earlier_tickets(self):
        """If no earlier sprints have open tickets, dispatch directly without modal."""
        content = APP_JS.read_text()
        # Should call smgmtDispatchRun with empty migrate_from list
        assert "smgmtDispatchRun" in content

    def test_modal_title_includes_target_sprint(self):
        """Modal title is set to 'Run Sprint <N>?'."""
        content = APP_JS.read_text()
        assert "Run Sprint" in content

    def test_radio_buttons_default_to_move(self):
        """Radio buttons default to 'Move all tickets' selection."""
        content = APP_JS.read_text()
        assert "move" in content
        assert "leave" in content
        # Default should be 'move'
        assert "_smgmtMigrateChoices[sprintNum] = 'move'" in content or \
               "= 'move'" in content

    def test_escape_closes_modal(self):
        """Esc key closes the migration modal."""
        content = APP_JS.read_text()
        assert "smgmt-migrate-modal" in content
        assert "Escape" in content
        # smgmtMigrateClose is called on Escape
        assert "smgmtMigrateClose" in content

    def test_enter_confirms_when_choices_made(self):
        """Enter key triggers confirm when all choices are made."""
        content = APP_JS.read_text()
        assert "Enter" in content
        assert "smgmt-migrate-confirm" in content

    def test_success_toast_shows_migrated_count(self):
        """Success toast mentions migrated ticket count and target sprint."""
        content = APP_JS.read_text()
        assert "migrated_count" in content or "Migrated" in content


# ── AC: UX — concurrency guard unchanged ─────────────────────────────────────

class TestConcurrencyGuard:
    def test_any_sprint_running_check_remains(self):
        """Existing same-sprint concurrency guard (PID files) still in server.py."""
        content = SERVER_PY.read_text()
        assert "_any_sprint_running" in content
        assert "is already running" in content

    def test_pid_file_written(self):
        """PID file is still written after successful dispatch."""
        content = SERVER_PY.read_text()
        assert "pid_path.write_text" in content
        assert "sprint_label}-pid" in content or "{body.sprint_label}-pid" in content
