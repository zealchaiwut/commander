"""Regression tests for issue #113: tester/sprint_manager must NEVER auto-approve
UAT tickets without human interaction.

Static checks only — no live server required.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

# ── Path resolution ────────────────────────────────────────────────────────────
# Test file lives at: apps/dashboard/tests/test_...py
# Repo root is four levels up: tests/ → dashboard/ → apps/ → coder/
REPO_ROOT     = Path(__file__).parent.parent.parent.parent
SPRINT_SCRIPT = REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"
FINISH_SCRIPT = REPO_ROOT / "scripts" / "finish_feature.py"
UPDATE_SCRIPT = REPO_ROOT / "scripts" / "update_ticket.py"
TESTER_AGENT  = REPO_ROOT / ".claude" / "agents" / "tester.md"


def _import_sprint_manager():
    dotenv_stub = types.ModuleType("dotenv")
    dotenv_stub.load_dotenv = lambda *a, **kw: None
    sys.modules.setdefault("dotenv", dotenv_stub)

    gc_stub = types.ModuleType("github_client")
    gc_stub.repo          = lambda: "zealchaiwut/commander"
    gc_stub.update_labels = lambda *a, **kw: None
    gc_stub.add_comment   = lambda *a, **kw: None
    gc_stub.approve_issue = lambda *a, **kw: None  # must not be called by sprint_manager
    sys.modules.setdefault("github_client", gc_stub)

    mod_name = "sprint_manager_113_import"
    spec     = importlib.util.spec_from_file_location(mod_name, SPRINT_SCRIPT)
    mod      = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# AC: Tester prompt never instructs agent to apply UAT-approved
# ---------------------------------------------------------------------------

class TestTesterPromptNoAutoApprove:
    """_dispatch_tester prompt must not instruct the AI to apply UAT-approved."""

    def test_sprint_manager_exists(self):
        assert SPRINT_SCRIPT.exists(), f"sprint_manager.py not found at {SPRINT_SCRIPT}"

    def test_dispatch_tester_prompt_prohibits_uat_approved(self):
        content = SPRINT_SCRIPT.read_text()
        assert "NEVER apply the UAT-approved label" in content, (
            "Tester prompt must contain 'NEVER apply the UAT-approved label' "
            "to prevent the agent from closing issues automatically"
        )

    def test_dispatch_tester_prompt_says_human_only(self):
        content = SPRINT_SCRIPT.read_text()
        assert "UAT-approved is set ONLY by the human" in content, (
            "Tester dispatch prompt must state 'UAT-approved is set ONLY by the human'"
        )

    def test_dispatch_tester_prompt_says_finish_feature_applies_label(self):
        content = SPRINT_SCRIPT.read_text()
        assert "finish_feature.py applies the UAT label automatically" in content, (
            "Tester prompt must say 'finish_feature.py applies the UAT label automatically' "
            "so the agent doesn't try to apply it separately"
        )


# ---------------------------------------------------------------------------
# AC: finish_feature.py applies UAT (not UAT-approved), does not close issue
# ---------------------------------------------------------------------------

class TestFinishFeatureNoClose:
    """finish_feature.py must apply --status uat and never close the issue."""

    def test_finish_feature_exists(self):
        assert FINISH_SCRIPT.exists(), f"finish_feature.py not found at {FINISH_SCRIPT}"

    def test_finish_feature_uses_status_uat(self):
        content = FINISH_SCRIPT.read_text()
        assert '"--status", "uat"' in content or "'--status', 'uat'" in content, (
            "finish_feature.py must call update_ticket.py with --status uat"
        )

    def test_finish_feature_never_uses_uat_approved(self):
        content = FINISH_SCRIPT.read_text()
        assert "uat-approved" not in content.lower(), (
            "finish_feature.py must not reference uat-approved — "
            "closing/approving is a human action only"
        )

    def test_finish_feature_does_not_call_issue_close(self):
        content = FINISH_SCRIPT.read_text()
        assert "issue close" not in content and "issue_close" not in content, (
            "finish_feature.py must not call 'gh issue close' — "
            "only update_ticket.py --status uat is allowed"
        )


# ---------------------------------------------------------------------------
# AC: update_ticket.py --status uat does NOT close the issue
# ---------------------------------------------------------------------------

class TestUpdateTicketUatNoClose:
    """update_ticket.py with --status uat must not close the issue."""

    def test_update_ticket_exists(self):
        assert UPDATE_SCRIPT.exists(), f"update_ticket.py not found at {UPDATE_SCRIPT}"

    def test_uat_status_has_no_close(self):
        import ast
        content = UPDATE_SCRIPT.read_text()
        tree = ast.parse(content)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == "STATUS_MAP":
                        status_map = ast.literal_eval(node.value)
                        uat_entry  = status_map.get("uat", {})
                        assert uat_entry.get("close") is None, (
                            "STATUS_MAP['uat']['close'] must be None — "
                            "UAT label must not close the issue"
                        )
                        uat_approved_entry = status_map.get("uat-approved", {})
                        assert uat_approved_entry.get("close") == "completed", (
                            "STATUS_MAP['uat-approved']['close'] must be 'completed' "
                            "(only human-triggered path closes issues)"
                        )


# ---------------------------------------------------------------------------
# AC: Sprint manager does not call approve_issue after tester completes
# ---------------------------------------------------------------------------

class TestSprintManagerNoApproveCall:
    """handle_post_tester must not call approve_issue at any point."""

    def test_handle_post_tester_does_not_call_approve_issue(self):
        sm = _import_sprint_manager()

        approve_called = []

        with mock.patch.object(sm, "_get_issue_labels", return_value={"UAT"}), \
             mock.patch.object(sm, "_find_feature_branch", return_value="feature/113-test"), \
             mock.patch.object(sm, "_call_finish_feature"), \
             mock.patch.object(sm, "_post_success_comment"), \
             mock.patch.object(sm, "_post_agent_event"), \
             mock.patch.object(sm, "_run_quality_gates", return_value=[
                 sm.GateResult(gate="pytest",        passed=True, skipped=True),
                 sm.GateResult(gate="lint",          passed=True, skipped=True),
                 sm.GateResult(gate="merge-preview", passed=True, skipped=True),
             ]):
            with mock.patch.object(sm.github_client, "approve_issue",
                                   side_effect=lambda *a, **kw: approve_called.append(a)):
                sm.handle_post_tester(
                    issue_num=113,
                    tester_exit_code=0,
                    skip_gates=True,
                    gate_pytest=False,
                    gate_lint=False,
                    gate_merge_preview=False,
                    target_branch="develop",
                    repo_name="zealchaiwut/commander",
                )

        assert approve_called == [], (
            "handle_post_tester must NEVER call approve_issue — "
            f"got {len(approve_called)} call(s): {approve_called}"
        )

    def test_sprint_manager_source_has_no_approve_issue_call_in_post_tester(self):
        content = SPRINT_SCRIPT.read_text()
        start = content.find("def handle_post_tester(")
        end   = content.find("\ndef ", start + 1)
        if end == -1:
            end = len(content)
        func_body = content[start:end]
        assert "approve_issue" not in func_body, (
            "handle_post_tester must not call approve_issue — "
            "that is a human-only action"
        )


# ---------------------------------------------------------------------------
# AC: Sprint summary shows UAT tickets as "pending review" not "shipped"
# ---------------------------------------------------------------------------

class TestSprintSummaryPendingReview:
    """generate_sprint_summary must label merged tickets as pending UAT review."""

    def test_summary_section_is_pending_uat_review(self):
        sm = _import_sprint_manager()
        state = sm.SprintState(sprint_label="sprint-5", sprint_number=5)
        state.issues = [
            sm.IssueState(number=1, title="Feature A", status="done"),
        ]
        content = sm.generate_sprint_summary(state, elapsed_secs=60)
        assert "## Pending UAT Review" in content, (
            "Sprint summary must have '## Pending UAT Review' section — "
            "tickets are merged but not yet approved by the human"
        )
        assert "## What Shipped" not in content, (
            "Sprint summary must not use '## What Shipped' — "
            "tickets are merged but not yet approved"
        )

    def test_summary_outcome_is_not_uat_approved(self):
        sm = _import_sprint_manager()
        state = sm.SprintState(sprint_label="sprint-5", sprint_number=5)
        state.issues = [
            sm.IssueState(number=1, title="Feature A", status="done"),
        ]
        content = sm.generate_sprint_summary(state, elapsed_secs=60)
        assert "UAT-approved" not in content, (
            "Sprint summary must not say 'UAT-approved' for merged tickets — "
            "they are awaiting human review"
        )
        assert "awaiting UAT review" in content, (
            "Sprint summary outcome must say 'awaiting UAT review'"
        )

    def test_summary_outcome_says_merged(self):
        sm = _import_sprint_manager()
        state = sm.SprintState(sprint_label="sprint-5", sprint_number=5)
        state.issues = [
            sm.IssueState(number=42, title="My Feature", status="done"),
        ]
        content = sm.generate_sprint_summary(state, elapsed_secs=60)
        assert "merged" in content.lower(), (
            "Sprint summary outcome must include 'merged'"
        )


# ---------------------------------------------------------------------------
# AC: tester.md explicitly prohibits UAT-approved
# ---------------------------------------------------------------------------

class TestTesterMdNoAutoApprove:
    """tester.md agent must explicitly prohibit applying UAT-approved."""

    def test_tester_md_exists(self):
        assert TESTER_AGENT.exists(), f"tester.md not found at {TESTER_AGENT}"

    def test_tester_md_prohibits_uat_approved(self):
        content = TESTER_AGENT.read_text()
        assert "UAT-approved" in content, (
            "tester.md must mention 'UAT-approved' to explicitly prohibit it"
        )
        # Strip markdown bold markers so "Do **NOT** apply" matches "Do NOT apply"
        clean = content.replace("**", "")
        assert "Do NOT apply" in clean or "NEVER" in clean, (
            "tester.md must explicitly prohibit applying UAT-approved "
            "(using 'Do NOT apply' or 'NEVER')"
        )

    def test_tester_md_says_issue_stays_open(self):
        content = TESTER_AGENT.read_text()
        # tester.md Step 10 must note that issue stays OPEN
        assert "OPEN" in content, (
            "tester.md must state that the issue stays OPEN after finish_feature.py"
        )

    def test_tester_md_human_in_the_loop_gate_present(self):
        content = TESTER_AGENT.read_text()
        assert "human" in content.lower(), (
            "tester.md must reference the human approval step"
        )
