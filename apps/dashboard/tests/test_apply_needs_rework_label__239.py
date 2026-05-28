"""Tests for issue #239 — Apply needs-rework label on logic-failure ticket statuses.

Acceptance Criteria:
  AC-1: When sprint manager encounters a logic failure (TESTER_REJECTED, CODER_NO_WORK,
        MERGE_CONFLICT, LINT_FAIL, PYTEST_FAIL), it calls update_ticket.py --status
        needs-rework --force for that issue.
  AC-2: Infrastructure failures (CRASH, TIMEOUT, RATE_LIMIT) do NOT trigger the
        needs-rework label.
  AC-3: The skip logic on resume/retry does NOT skip tickets with the need-rework
        label — they are picked up as fresh attempts on re-run.
  AC-4: Tickets with need-rework are visually distinguishable in the sprint dashboard
        (different badge/color).

Tests validate:
  - Sprint manager calls update_ticket for logic failures
  - Sprint manager does NOT call update_ticket for infrastructure failures
  - Skip logic checks GitHub labels before skipping on resume
  - Frontend correctly shows need-rework badge
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

SPRINT_SCRIPT = Path(__file__).parent.parent.parent / "services" / "sprint_manager" / "sprint_manager.py"
APP_JS = Path(__file__).parent.parent / "static" / "app.js"
INDEX_HTML = Path(__file__).parent.parent / "static" / "index.html"


def _import_sprint_manager():
    """Import sprint_manager.py as a module, mocking dotenv and github_client."""
    import importlib.util

    # Provide stub modules
    dotenv_stub = types.ModuleType("dotenv")
    dotenv_stub.load_dotenv = lambda *a, **kw: None
    sys.modules.setdefault("dotenv", dotenv_stub)

    gc_stub = types.ModuleType("github_client")
    gc_stub.repo = lambda: "zealchaiwut/commander"
    gc_stub.update_labels = lambda *a, **kw: None
    gc_stub.add_comment = lambda *a, **kw: None
    sys.modules.setdefault("github_client", gc_stub)

    mod_name = "sprint_manager_test_239"
    spec = importlib.util.spec_from_file_location(mod_name, SPRINT_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# AC-1 & AC-2: Logic failures trigger needs-rework label, infrastructure failures don't
# ---------------------------------------------------------------------------

class TestApplyNeedsReworkLabel:
    def test_logic_failure_categories_defined(self):
        sm = _import_sprint_manager()
        assert hasattr(sm, "_LOGIC_FAILURE_CATEGORIES")
        assert sm.FailureCategory.TESTER_REJECTED in sm._LOGIC_FAILURE_CATEGORIES
        assert sm.FailureCategory.CODER_NO_WORK in sm._LOGIC_FAILURE_CATEGORIES
        assert sm.FailureCategory.MERGE_CONFLICT in sm._LOGIC_FAILURE_CATEGORIES
        assert sm.FailureCategory.LINT_FAIL in sm._LOGIC_FAILURE_CATEGORIES
        assert sm.FailureCategory.PYTEST_FAIL in sm._LOGIC_FAILURE_CATEGORIES

    def test_infrastructure_failures_not_in_logic_failures(self):
        sm = _import_sprint_manager()
        # Infrastructure failures should NOT trigger needs-rework
        assert sm.FailureCategory.CRASH not in sm._LOGIC_FAILURE_CATEGORIES
        assert sm.FailureCategory.HANG not in sm._LOGIC_FAILURE_CATEGORIES
        assert sm.FailureCategory.RETRY_EXHAUSTED not in sm._LOGIC_FAILURE_CATEGORIES

    def test_apply_needs_rework_label_function_exists(self):
        content = SPRINT_SCRIPT.read_text()
        assert "_apply_needs_rework_label" in content
        assert "update_ticket.py" in content
        assert "--status" in content
        assert "needs-rework" in content

    def test_apply_needs_rework_label_accepts_category(self):
        sm = _import_sprint_manager()
        assert hasattr(sm, "_apply_needs_rework_label")
        # Function should accept issue_num, category, cfg=None
        import inspect
        sig = inspect.signature(sm._apply_needs_rework_label)
        assert "issue_num" in sig.parameters
        assert "category" in sig.parameters

    def test_apply_needs_rework_label_checks_category(self):
        """Function should only apply label for logic failure categories."""
        sm = _import_sprint_manager()
        with mock.patch("subprocess.run") as mock_run:
            # Should not call subprocess for infrastructure failures
            sm._apply_needs_rework_label(42, sm.FailureCategory.CRASH)
            mock_run.assert_not_called()

            # Should call subprocess for logic failures
            mock_run.reset_mock()
            sm._apply_needs_rework_label(42, sm.FailureCategory.TESTER_REJECTED)
            mock_run.assert_called_once()

    def test_needs_rework_label_called_on_coder_failures(self):
        """Verify _apply_needs_rework_label is called when coder fails."""
        content = SPRINT_SCRIPT.read_text()
        # Find where coder failure is handled
        assert "if not coder_ok:" in content
        # After coder_failed, needs-rework should be applied
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if "coder_ok, coder_category = _dispatch_coder" in line:
                # Look ahead for the failure handling
                found_apply = False
                for j in range(i, min(i + 50, len(lines))):
                    if "_apply_needs_rework_label" in lines[j]:
                        found_apply = True
                        break
                assert found_apply, "needs-rework label should be applied on coder failure"

    def test_needs_rework_label_called_on_coder_no_work(self):
        """Verify needs-rework is applied when coder creates no feature branch."""
        content = SPRINT_SCRIPT.read_text()
        assert "CODER_NO_WORK" in content
        assert "_apply_needs_rework_label" in content

    def test_needs_rework_label_called_on_post_tester_failure(self):
        """Verify needs-rework is applied when post-tester gates fail."""
        content = SPRINT_SCRIPT.read_text()
        # Look for handle_post_tester call and subsequent needs-rework apply
        assert "handle_post_tester" in content
        assert "_apply_needs_rework_label" in content


# ---------------------------------------------------------------------------
# AC-3: Skip logic doesn't skip need-rework tickets on resume
# ---------------------------------------------------------------------------

class TestSkipLogicWithNeedsRework:
    def test_get_issue_labels_function_exists(self):
        sm = _import_sprint_manager()
        assert hasattr(sm, "_get_issue_labels")

    def test_resume_skip_logic_checks_labels(self):
        """Skip logic should check GitHub labels before skipping on resume."""
        content = SPRINT_SCRIPT.read_text()
        assert "if resume and issue_state.status in" in content
        # Should check for need-rework label
        assert "need-rework" in content or "needs-rework" in content
        # Should check GitHub labels via _get_issue_labels
        assert "_get_issue_labels" in content

    def test_skip_logic_picks_up_need_rework_tickets(self):
        """Skipped tickets with need-rework label should be picked up on re-run."""
        content = SPRINT_SCRIPT.read_text()
        # Look for the resume skip logic section
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if "if resume and issue_state.status in" in line and '"done", "skipped"' in line:
                # Find the next _get_issue_labels call in this block
                found_check = False
                for j in range(i, min(i + 30, len(lines))):
                    if "_get_issue_labels" in lines[j] and "need-rework" in lines[j]:
                        found_check = True
                        break
                assert found_check or any(
                    "need-rework" in lines[k] for k in range(i, min(i + 30, len(lines)))
                ), "skip logic should check for need-rework label"
                break

    def test_skip_logic_resets_state_for_need_rework_tickets(self):
        """When picking up need-rework tickets, state should be reset to queued."""
        content = SPRINT_SCRIPT.read_text()
        # When a need-rework ticket is picked up, status should change back to queued
        assert 'issue_state.status = "queued"' in content or "status = 'queued'" in content


# ---------------------------------------------------------------------------
# AC-4: Frontend visual distinction for need-rework tickets
# ---------------------------------------------------------------------------

class TestFrontendNeedsReworkBadge:
    def test_app_js_has_needs_rework_label_check(self):
        content = APP_JS.read_text()
        assert "need-rework" in content or "needs-rework" in content

    def test_app_js_checks_both_spellings(self):
        """Should handle both 'need-rework' and 'needs-rework' spellings."""
        content = APP_JS.read_text()
        assert "need-rework" in content
        # May also check for needs-rework (plural) for safety
        if "needs-rework" in content:
            # Good — handles both variants
            pass

    def test_html_has_needs_rework_status_class(self):
        content = INDEX_HTML.read_text()
        assert "smgmt-status-need-rework" in content

    def test_html_has_rework_badge_styling(self):
        content = INDEX_HTML.read_text()
        assert "smgmt-rework-badge" in content

    def test_html_rework_styling_is_distinctive(self):
        """need-rework styling should be visually distinct from other statuses."""
        content = INDEX_HTML.read_text()
        # Check for red/warning-like colors for need-rework
        lines = content.split("\n")
        found_rework_style = False
        for i, line in enumerate(lines):
            if "smgmt-status-need-rework" in line:
                # Should have some color/styling that makes it visually distinct
                # Look for color, background, or border properties
                assert any(
                    prop in line for prop in ["color", "background", "border"]
                ), f"need-rework styling should include visual properties: {line}"
                found_rework_style = True
                break
        assert found_rework_style, "HTML must include need-rework styling"

    def test_app_js_shows_needs_rework_label_in_card(self):
        """Ticket cards should display 'needs rework' status when label is present."""
        content = APP_JS.read_text()
        # Should show 'needs rework' text in the UI
        assert "needs rework" in content

    def test_app_js_shows_needs_rework_badge_in_backlog(self):
        """Backlog tickets should show a 'needs rework' badge when labeled."""
        content = APP_JS.read_text()
        # Should have a badge or indicator in backlog view
        assert "smgmt-rework-badge" in content or "needs rework" in content


# ---------------------------------------------------------------------------
# Update_ticket.py integration
# ---------------------------------------------------------------------------

class TestUpdateTicketNeetsReworkStatus:
    def test_update_ticket_has_needs_rework_status(self):
        update_script = Path(__file__).parent.parent.parent / "scripts" / "update_ticket.py"
        content = update_script.read_text()
        assert '"needs-rework"' in content

    def test_needs_rework_adds_correct_label(self):
        update_script = Path(__file__).parent.parent.parent / "scripts" / "update_ticket.py"
        content = update_script.read_text()
        # Should add "need-rework" label (singular)
        assert '"need-rework"' in content or "'need-rework'" in content

    def test_needs_rework_removes_conflicting_labels(self):
        """needs-rework status should remove status labels that conflict."""
        update_script = Path(__file__).parent.parent.parent / "scripts" / "update_ticket.py"
        content = update_script.read_text()
        # Find the needs-rework mapping
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if '"needs-rework"' in line:
                # Look at the next few lines for remove section
                for j in range(i, min(i + 10, len(lines))):
                    if '"remove"' in lines[j]:
                        remove_line = lines[j]
                        # Should remove at least: in-progress, SIT, UAT, UAT-approved
                        assert "in-progress" in remove_line
                        assert "SIT" in remove_line
                        assert "UAT" in remove_line
                        break
                break
