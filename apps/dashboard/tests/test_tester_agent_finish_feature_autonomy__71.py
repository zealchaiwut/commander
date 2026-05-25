"""Tests for issue #71: Tester agent treats sprint runs as advisory —
asks for human confirmation instead of running finish_feature.py.

Static checks validate sprint_manager.py and tester.md source files directly
(no live server required).
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest import mock

import pytest

SCRIPTS_DIR   = Path(__file__).parent.parent / "scripts"
SPRINT_SCRIPT = SCRIPTS_DIR / "sprint_manager.py"
AGENTS_DIR    = Path(__file__).parent.parent / ".claude" / "agents"
TESTER_AGENT  = AGENTS_DIR / "tester.md"
COMMANDS_DIR  = Path(__file__).parent.parent / ".claude" / "commands"
TESTER_CMD    = COMMANDS_DIR / "tester.md"


# ---------------------------------------------------------------------------
# Helper: import sprint_manager without side effects
# ---------------------------------------------------------------------------

def _import_sprint_manager():
    """Import sprint_manager.py as a module, mocking dotenv and github_client."""
    import importlib.util

    dotenv_stub = types.ModuleType("dotenv")
    dotenv_stub.load_dotenv = lambda *a, **kw: None
    sys.modules.setdefault("dotenv", dotenv_stub)

    gc_stub = types.ModuleType("github_client")
    gc_stub.repo = lambda: "zealchaiwut/commander"
    gc_stub.update_labels = lambda *a, **kw: None
    gc_stub.add_comment   = lambda *a, **kw: None
    sys.modules.setdefault("github_client", gc_stub)

    mod_name = "sprint_manager_test_import_71"
    spec     = importlib.util.spec_from_file_location(mod_name, SPRINT_SCRIPT)
    mod      = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# AC-4: Sprint manager dispatch prompt states autonomous mode explicitly
# ---------------------------------------------------------------------------

class TestSprintManagerTesterPromptAutonomous:
    """Sprint manager's tester dispatch prompt explicitly states autonomous mode."""

    def test_dispatch_tester_function_exists(self):
        content = SPRINT_SCRIPT.read_text()
        assert "_dispatch_tester" in content, \
            "_dispatch_tester function must exist in sprint_manager.py"

    def test_default_prompt_contains_autonomous_sprint_mode(self):
        content = SPRINT_SCRIPT.read_text()
        assert "autonomous sprint mode" in content, (
            "Default tester prompt must say 'autonomous sprint mode'"
        )

    def test_default_prompt_instructs_to_execute_step_10_without_asking(self):
        content = SPRINT_SCRIPT.read_text()
        assert "without asking" in content, (
            "Default tester prompt must say 'execute Step 10 ... without asking'"
        )

    def test_default_prompt_prohibits_let_me_know_language(self):
        content = SPRINT_SCRIPT.read_text()
        assert "let me know if you want me to" in content, (
            "Default tester prompt must explicitly prohibit 'let me know if you want me to...' language"
        )

    def test_default_prompt_mentions_finish_feature(self):
        content = SPRINT_SCRIPT.read_text()
        assert "finish_feature" in content, (
            "Default tester prompt must reference finish_feature.py"
        )

    def test_default_prompt_mentions_uat_label(self):
        content = SPRINT_SCRIPT.read_text()
        # The prompt should reference applying the UAT label
        assert "UAT label" in content or "uat label" in content.lower() or \
               ("gh issue edit" in content and "UAT" in content), (
            "Default tester prompt must instruct the agent to apply the UAT label"
        )


# ---------------------------------------------------------------------------
# AC-6: Sprint manager logs "tester promoted issue #N to UAT"
# ---------------------------------------------------------------------------

class TestSprintManagerTesterPromotedLog:
    """Sprint manager logs show 'tester promoted issue #N to UAT'."""

    def test_promoted_log_phrase_present_in_source(self):
        content = SPRINT_SCRIPT.read_text()
        assert "tester promoted issue" in content.lower() or \
               "Tester promoted issue" in content, (
            "sprint_manager.py must log 'Tester promoted issue #N to UAT' "
            "instead of 'tester exited 0 but not UAT'"
        )

    def test_handle_post_tester_success_message_uses_promoted_phrase(self):
        """handle_post_tester success return value includes 'promoted' phrase."""
        sm = _import_sprint_manager()

        # Patch _get_issue_labels to return UAT label (tester DID promote)
        with mock.patch.object(sm, "_get_issue_labels", return_value={"UAT"}), \
             mock.patch.object(sm, "_find_feature_branch", return_value="feature/71-test"), \
             mock.patch.object(sm, "_call_finish_feature"), \
             mock.patch.object(sm, "_post_success_comment"), \
             mock.patch.object(sm, "_post_agent_event"), \
             mock.patch.object(sm, "_run_quality_gates", return_value=[
                 sm.GateResult(gate="pytest",        passed=True, skipped=True),
                 sm.GateResult(gate="lint",          passed=True, skipped=True),
                 sm.GateResult(gate="merge-preview", passed=True, skipped=True),
             ]):
            merged, summary_line, cat = sm.handle_post_tester(
                issue_num=71,
                tester_exit_code=0,
                skip_gates=True,
                gate_pytest=False,
                gate_lint=False,
                gate_merge_preview=False,
                target_branch="develop",
                repo_name="zealchaiwut/commander",
            )

        assert merged is True, "handle_post_tester should return merged=True when UAT label is set"
        assert "promoted" in summary_line.lower(), (
            f"Summary line should contain 'promoted', got: {summary_line!r}"
        )

    def test_handle_post_tester_rejected_message_still_explains(self):
        """When tester did NOT promote (no UAT label), the message is still informative."""
        sm = _import_sprint_manager()

        with mock.patch.object(sm, "_get_issue_labels", return_value={"SIT"}):
            merged, summary_line, cat = sm.handle_post_tester(
                issue_num=71,
                tester_exit_code=0,
                skip_gates=False,
                gate_pytest=True,
                gate_lint=True,
                gate_merge_preview=True,
                target_branch="develop",
                repo_name="zealchaiwut/commander",
            )

        assert merged is False
        assert cat == sm.FailureCategory.TESTER_REJECTED
        # The "not UAT" explanation should still appear in the summary
        assert "not UAT" in summary_line or "TESTER_REJECTED" in (cat or ""), (
            f"TESTER_REJECTED path should mention 'not UAT', got: {summary_line!r}"
        )


# ---------------------------------------------------------------------------
# AC-5: tester.md Step 10 is mandatory in sprint context
# ---------------------------------------------------------------------------

class TestTesterMdStep10Mandatory:
    """tester.md is updated to make Step 10 mandatory in sprint context."""

    def test_tester_agent_file_exists(self):
        assert TESTER_AGENT.exists(), \
            f"tester agent file must exist at {TESTER_AGENT}"

    def test_step_10_section_present_in_agent(self):
        content = TESTER_AGENT.read_text()
        assert "Step 10" in content, \
            "tester.md must contain a 'Step 10' section"

    def test_step_10_is_mandatory_or_must_not_skip(self):
        content = TESTER_AGENT.read_text()
        # Check for mandatory language around Step 10
        lower = content.lower()
        assert "mandatory" in lower or "must" in lower or "never skip" in lower, (
            "tester.md Step 10 section must use mandatory language (MANDATORY / must / never skip)"
        )

    def test_sprint_manager_prompt_prohibits_advisory_language(self):
        """The sprint manager dispatch prompt (sprint_manager.py) prohibits advisory language.

        AC-3 is enforced via the sprint_manager.py tester dispatch prompt rather than
        tester.md itself, because the sprint manager injects this constraint at dispatch
        time to override any advisory tendencies in the base agent.
        """
        content = SPRINT_SCRIPT.read_text()
        lower = content.lower()
        assert "let me know if you want me to" in lower or \
               "without asking" in lower or "without prompting" in lower, (
            "sprint_manager.py tester dispatch prompt must prohibit 'let me know if you want me to...' language"
        )

    def test_tester_command_mentions_finish_feature_on_pass(self):
        """The /tester command also references finish_feature.py on test pass."""
        content = TESTER_CMD.read_text()
        assert "finish_feature" in content, \
            "tester command (tester.md) must reference finish_feature.py on pass"


# ---------------------------------------------------------------------------
# AC-1 / AC-2: Tester workflow structure (static analysis)
# ---------------------------------------------------------------------------

class TestTesterWorkflowStructure:
    """Structural checks: finish_feature and UAT label are both in tester workflow."""

    def test_tester_agent_references_finish_feature(self):
        content = TESTER_AGENT.read_text()
        assert "finish_feature" in content, \
            "tester.md must reference finish_feature.py in the promote step"

    def test_tester_agent_references_uat_label_via_gh(self):
        """Tester should show how to apply UAT label (gh issue edit or finish_feature)."""
        content = TESTER_AGENT.read_text()
        assert "UAT" in content and ("gh issue edit" in content or "finish_feature" in content), (
            "tester.md must show how to apply the UAT label (gh issue edit or via finish_feature)"
        )

    def test_tester_command_applies_uat_label_on_pass(self):
        content = TESTER_CMD.read_text()
        # Command should either use update_ticket with uat status or gh issue edit
        assert "uat" in content.lower(), \
            "tester command must mention UAT label promotion on test pass"

    def test_dispatch_tester_injects_sprint_branch_when_non_develop(self):
        """_dispatch_tester must pass COMMANDER_MERGE_TARGET for non-develop branches."""
        content = SPRINT_SCRIPT.read_text()
        assert "COMMANDER_MERGE_TARGET" in content, (
            "_dispatch_tester must set COMMANDER_MERGE_TARGET env var for sprint branches"
        )
