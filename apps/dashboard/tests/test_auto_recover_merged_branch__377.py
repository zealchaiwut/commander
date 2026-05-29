"""Tests for issue #377: auto-recover merged branch missing UAT label.

Covers three new scenarios in handle_post_tester:

AC-2: Branch not found + merge commit in log → auto-recovery (UAT label applied,
      recovery comment posted, NOT TESTER_REJECTED).

AC-3: Branch not found + no merge commit → genuine tester skip (warning comment,
      TESTER_REJECTED).

AC-4: Branch found and merged → auto-recovery, "tester skipped" comment never posted.

AC-5 (happy path): UAT label already present → no recovery, no extra comments.

All tests are static (no live server, no real git ops).
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

REPO_ROOT             = Path(__file__).parent.parent.parent.parent
SPRINT_MANAGER_SCRIPT = REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"


# ---------------------------------------------------------------------------
# Import helper
# ---------------------------------------------------------------------------

def _import_sprint_manager(mod_key: str = "sprint_manager_377") -> object:
    dotenv_stub = types.ModuleType("dotenv")
    dotenv_stub.load_dotenv = lambda *a, **kw: None
    sys.modules.setdefault("dotenv", dotenv_stub)

    gc_stub = types.ModuleType("github_client")
    gc_stub.repo = lambda: "zealchaiwut/commander"
    gc_stub.update_labels = lambda *a, **kw: None
    gc_stub.add_comment = MagicMock()
    sys.modules.setdefault("github_client", gc_stub)

    if mod_key in sys.modules:
        return sys.modules[mod_key]

    spec = importlib.util.spec_from_file_location(mod_key, SPRINT_MANAGER_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_key] = mod
    spec.loader.exec_module(mod)
    return mod


# ===========================================================================
# AC-2: branch not found, merge commit in git log → auto-recovery
# ===========================================================================

class TestAC2BranchDeletedMergeInLog:

    def test_applies_uat_label_and_posts_recovery_comment(self):
        """Branch deleted after merge. handle_post_tester must detect the merge
        via git log, apply UAT via update_ticket.py, and post the recovery comment."""
        sm = _import_sprint_manager()

        mock_add_comment = MagicMock()
        mock_subproc_result = MagicMock()
        mock_subproc_result.returncode = 0
        mock_subproc_result.stderr = ""

        with mock.patch.object(sm, "_get_issue_labels", return_value={"sprint-27", "sit"}), \
             mock.patch.object(sm, "_find_feature_branch", return_value=None), \
             mock.patch.object(sm, "_was_feature_merged_via_log", return_value=True), \
             mock.patch.object(sm.github_client, "add_comment", mock_add_comment), \
             mock.patch.object(sm, "subprocess") as mock_subproc, \
             mock.patch.object(sm, "_post_agent_event"), \
             mock.patch.object(sm, "_post_success_comment"), \
             mock.patch.object(sm, "_run_quality_gates", return_value=[]), \
             mock.patch.object(sm, "_call_finish_feature"):

            mock_subproc.run.return_value = mock_subproc_result

            result = sm.handle_post_tester(
                issue_num=377,
                tester_exit_code=0,
                skip_gates=True,
                gate_pytest=True,
                gate_lint=True,
                gate_merge_preview=True,
            )

        merged, summary_line, category = result

        assert merged is True, "merged must be True when branch was merged-and-deleted"
        assert category is None, "failure_category must be None on auto-recovery success"

        # update_ticket.py --status uat must be called
        uat_calls = [
            c for c in mock_subproc.run.call_args_list
            if "update_ticket" in str(c)
        ]
        assert uat_calls, "update_ticket.py --status uat must be called during auto-recovery"
        uat_cmd = list(uat_calls[0][0][0])
        assert "--status" in uat_cmd and "uat" in uat_cmd, (
            "update_ticket.py must be called with --status uat"
        )

        # Recovery comment must be posted
        mock_add_comment.assert_called()
        recovery_body = mock_add_comment.call_args[0][1]
        assert "Detected merged feature branch without UAT label" in recovery_body
        assert "likely a transient label-apply failure" in recovery_body

    def test_not_marked_tester_rejected(self):
        """When auto-recovery succeeds, TESTER_REJECTED must never be the failure category."""
        sm = _import_sprint_manager()

        mock_subproc_result = MagicMock()
        mock_subproc_result.returncode = 0
        mock_subproc_result.stderr = ""

        with mock.patch.object(sm, "_get_issue_labels", return_value={"sit"}), \
             mock.patch.object(sm, "_find_feature_branch", return_value=None), \
             mock.patch.object(sm, "_was_feature_merged_via_log", return_value=True), \
             mock.patch.object(sm.github_client, "add_comment"), \
             mock.patch.object(sm, "subprocess") as mock_subproc, \
             mock.patch.object(sm, "_post_agent_event"), \
             mock.patch.object(sm, "_post_success_comment"), \
             mock.patch.object(sm, "_call_finish_feature"):

            mock_subproc.run.return_value = mock_subproc_result

            result = sm.handle_post_tester(
                issue_num=377,
                tester_exit_code=0,
                skip_gates=True,
                gate_pytest=True,
                gate_lint=True,
                gate_merge_preview=True,
            )

        _, _, category = result
        assert category != sm.FailureCategory.TESTER_REJECTED, (
            "TESTER_REJECTED must not be set when branch was merged and auto-recovered"
        )

    def test_tester_skip_comment_never_posted_when_merged_via_log(self):
        """The 'tester skipped finish_feature.py' comment must never appear on a
        ticket whose feature branch merge is confirmed in git log."""
        sm = _import_sprint_manager()

        posted_comments: list[str] = []
        mock_subproc_result = MagicMock()
        mock_subproc_result.returncode = 0
        mock_subproc_result.stderr = ""

        def capture_comment(issue_num, body, **kw):
            posted_comments.append(body)

        with mock.patch.object(sm, "_get_issue_labels", return_value={"sit"}), \
             mock.patch.object(sm, "_find_feature_branch", return_value=None), \
             mock.patch.object(sm, "_was_feature_merged_via_log", return_value=True), \
             mock.patch.object(sm.github_client, "add_comment", side_effect=capture_comment), \
             mock.patch.object(sm, "subprocess") as mock_subproc, \
             mock.patch.object(sm, "_post_agent_event"), \
             mock.patch.object(sm, "_post_success_comment"), \
             mock.patch.object(sm, "_call_finish_feature"):

            mock_subproc.run.return_value = mock_subproc_result

            sm.handle_post_tester(
                issue_num=377,
                tester_exit_code=0,
                skip_gates=True,
                gate_pytest=True,
                gate_lint=True,
                gate_merge_preview=True,
            )

        for body in posted_comments:
            assert "skipped running" not in body.lower(), (
                "'tester skipped finish_feature.py' comment must never be posted "
                "when the merge is confirmed in git log"
            )
            assert "skipped finish_feature" not in body.lower()


# ===========================================================================
# AC-3: branch not found, no merge commit in log → genuine tester skip
# ===========================================================================

class TestAC3BranchNotFoundNoMergeInLog:

    def test_posts_tester_skip_warning_and_returns_rejected(self):
        """When the feature branch is absent and no merge commit exists in log,
        the 'tester skipped finish_feature.py' warning must be posted and
        TESTER_REJECTED returned."""
        sm = _import_sprint_manager()

        mock_add_comment = MagicMock()

        with mock.patch.object(sm, "_get_issue_labels", return_value={"sprint-27", "sit"}), \
             mock.patch.object(sm, "_find_feature_branch", return_value=None), \
             mock.patch.object(sm, "_was_feature_merged_via_log", return_value=False), \
             mock.patch.object(sm.github_client, "add_comment", mock_add_comment):

            result = sm.handle_post_tester(
                issue_num=377,
                tester_exit_code=0,
                skip_gates=False,
                gate_pytest=True,
                gate_lint=True,
                gate_merge_preview=True,
            )

        merged, summary_line, category = result

        assert merged is False
        assert category == sm.FailureCategory.TESTER_REJECTED

        mock_add_comment.assert_called_once()
        body = mock_add_comment.call_args[0][1]
        assert "tester exited 0 but not UAT" in body or "tester agent skipped" in body.lower(), (
            "Warning comment must indicate that the tester skipped finish_feature.py"
        )

    def test_was_feature_merged_via_log_called_when_branch_not_found(self):
        """_was_feature_merged_via_log must be called when _find_feature_branch returns None."""
        sm = _import_sprint_manager()

        mock_log_check = MagicMock(return_value=False)

        with mock.patch.object(sm, "_get_issue_labels", return_value={"sit"}), \
             mock.patch.object(sm, "_find_feature_branch", return_value=None), \
             mock.patch.object(sm, "_was_feature_merged_via_log", mock_log_check), \
             mock.patch.object(sm.github_client, "add_comment"):

            sm.handle_post_tester(
                issue_num=377,
                tester_exit_code=0,
                skip_gates=False,
                gate_pytest=True,
                gate_lint=True,
                gate_merge_preview=True,
            )

        mock_log_check.assert_called_once_with(377, "develop")


# ===========================================================================
# AC-4: branch found and merged → auto-recovery, no tester-skip comment
# ===========================================================================

class TestAC4BranchFoundAndMerged:

    def test_recovery_path_taken_no_tester_skip_comment(self):
        """When the feature branch exists and is confirmed merged, the auto-recovery
        path runs and the 'tester skipped' comment is never posted."""
        sm = _import_sprint_manager()

        posted_comments: list[str] = []
        mock_subproc_result = MagicMock()
        mock_subproc_result.returncode = 0
        mock_subproc_result.stderr = ""

        def capture_comment(issue_num, body, **kw):
            posted_comments.append(body)

        with mock.patch.object(sm, "_get_issue_labels", return_value={"sit"}), \
             mock.patch.object(sm, "_find_feature_branch", return_value="feature/377-my-fix"), \
             mock.patch.object(sm, "_is_branch_merged_into", return_value=True), \
             mock.patch.object(sm.github_client, "add_comment", side_effect=capture_comment), \
             mock.patch.object(sm, "subprocess") as mock_subproc, \
             mock.patch.object(sm, "_post_agent_event"), \
             mock.patch.object(sm, "_post_success_comment"), \
             mock.patch.object(sm, "_call_finish_feature"):

            mock_subproc.run.return_value = mock_subproc_result

            result = sm.handle_post_tester(
                issue_num=377,
                tester_exit_code=0,
                skip_gates=True,
                gate_pytest=True,
                gate_lint=True,
                gate_merge_preview=True,
            )

        merged, _, category = result
        assert merged is True
        assert category is None

        for body in posted_comments:
            assert "skipped finish_feature" not in body.lower(), (
                "'tester skipped finish_feature.py' must never be posted when branch is merged"
            )

        recovery_bodies = [b for b in posted_comments if "Detected merged" in b]
        assert recovery_bodies, "Recovery comment must be posted when branch is merged"
        assert "likely a transient label-apply failure" in recovery_bodies[0]


# ===========================================================================
# AC-5 (happy path): UAT label already present → no recovery, no extra comments
# ===========================================================================

class TestAC5HappyPath:

    def test_no_recovery_comment_when_uat_label_already_present(self):
        """When the UAT label is already present, _was_feature_merged_via_log must
        not be called and no recovery comment is posted."""
        sm = _import_sprint_manager()

        mock_add_comment = MagicMock()
        mock_log_check = MagicMock(return_value=False)

        with mock.patch.object(sm, "_get_issue_labels", return_value={"UAT", "sprint-27"}), \
             mock.patch.object(sm, "_find_feature_branch", return_value=None), \
             mock.patch.object(sm, "_was_feature_merged_via_log", mock_log_check), \
             mock.patch.object(sm.github_client, "add_comment", mock_add_comment), \
             mock.patch.object(sm, "_post_agent_event"), \
             mock.patch.object(sm, "_post_success_comment"), \
             mock.patch.object(sm, "_call_finish_feature"):

            result = sm.handle_post_tester(
                issue_num=377,
                tester_exit_code=0,
                skip_gates=True,
                gate_pytest=True,
                gate_lint=True,
                gate_merge_preview=True,
            )

        merged, _, category = result
        assert merged is True
        assert category is None

        mock_log_check.assert_not_called()

        # add_comment may be called by _post_success_comment (mocked) — but no
        # recovery or tester-skip comment should be posted via github_client.add_comment
        for call_args in mock_add_comment.call_args_list:
            body = call_args[0][1] if len(call_args[0]) > 1 else ""
            assert "Detected merged feature branch" not in body, (
                "Recovery comment must not be posted when UAT label is already present"
            )
            assert "skipped finish_feature" not in body.lower(), (
                "Tester-skip comment must not be posted on the happy path"
            )
