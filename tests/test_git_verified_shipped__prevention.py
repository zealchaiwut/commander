"""Unit tests for git-verified shipped issue prevention.

Tests the strict merge-check helpers that prevent false "shipped" reporting
caused by stale local feature branches that are ancestors of the target but
were never actually merged.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.sprint_manager.sprint_manager import (
    _fail_loud_shipped_reconciliation,
    _git_target_ref,
    _git_verified_shipped_issues,
    _is_issue_merged_into_target,
    _prune_stale_local_feature_branch,
    _reporting_not_shipped_issues,
    _shipped_reconciliation_mismatch,
)


# ---------------------------------------------------------------------------
# Helpers to build minimal IssueState-like objects
# ---------------------------------------------------------------------------

def _issue(number: int, status: str, title: str = "") -> SimpleNamespace:
    return SimpleNamespace(number=number, status=status, title=title or f"Issue #{number}")


def _state(*issues) -> SimpleNamespace:
    return SimpleNamespace(issues=list(issues))


# ---------------------------------------------------------------------------
# _git_target_ref
# ---------------------------------------------------------------------------

class TestGitTargetRef:
    def test_prefers_origin_when_exists(self):
        def _try_stub(*args):
            cmd = list(args)
            if "origin/develop" in cmd:
                return (True, "abc123\n", "")
            return (False, "", "not found")

        with patch("services.sprint_manager.sprint_manager._try", side_effect=_try_stub):
            result = _git_target_ref("develop")
        assert result == "origin/develop"

    def test_falls_back_to_local_when_origin_missing(self):
        def _try_stub(*args):
            return (False, "", "not found")

        with patch("services.sprint_manager.sprint_manager._try", side_effect=_try_stub):
            result = _git_target_ref("develop")
        assert result == "develop"


# ---------------------------------------------------------------------------
# _is_issue_merged_into_target
# ---------------------------------------------------------------------------

class TestIsIssueMergedIntoTarget:
    """Core tests for the strict merge-check logic."""

    def _make_try(
        self,
        *,
        merge_log: bool = False,
        origin_feature_exists: bool = True,
        local_feature_exists: bool = False,
        feature_sha: str = "abc123",
        origin_target_exists: bool = True,
        unmerged_count: int = 0,
    ):
        """Build a _try mock that simulates specific git scenarios."""

        def _try_stub(*args):
            cmd = list(args)
            cmd_str = " ".join(str(a) for a in cmd)

            # git rev-parse --verify origin/target
            if "rev-parse" in cmd and "--verify" in cmd and "origin/develop" in cmd:
                return (origin_target_exists, "targetsha\n", "")

            # git log (for _was_feature_merged_via_log)
            if "log" in cmd and "--merges" in cmd:
                if merge_log:
                    return (True, "abc123 Merge feature/42-foo into develop (issue #42)\n", "")
                return (True, "", "")

            # git branch -r --list origin/feature/42-*  (remote feature lookup)
            if "branch" in cmd and "-r" in cmd and "--list" in cmd:
                branch_pat = [a for a in cmd if "feature/42" in a]
                if branch_pat:
                    if origin_feature_exists:
                        return (True, "  origin/feature/42-fix-foo\n", "")
                    return (True, "", "")
                # For _is_branch_merged_into fallback path (origin/<branch>)
                return (True, "", "")

            # git branch --list feature/42-*  (local feature lookup)
            if "branch" in cmd and "--list" in cmd and "-r" not in cmd:
                branch_pat = [a for a in cmd if "feature/42" in a]
                if branch_pat:
                    if local_feature_exists:
                        return (True, "  feature/42-fix-foo\n", "")
                    return (True, "", "")
                return (True, "", "")

            # git rev-parse --verify origin/feature/42-fix-foo (tip SHA)
            if "rev-parse" in cmd and "--verify" in cmd:
                ref = [a for a in cmd if "feature/" in a]
                if ref:
                    if origin_feature_exists or local_feature_exists:
                        return (True, f"{feature_sha}\n", "")
                    return (False, "", "not found")
                # fallback generic verify
                return (True, "sha\n", "")

            # git rev-parse (no --verify, for tip SHA)
            if "rev-parse" in cmd and "--verify" not in cmd:
                ref = [a for a in cmd if "feature/" in a]
                if ref:
                    return (True, f"{feature_sha}\n", "")
                return (True, "sha\n", "")

            # git rev-list --count feature_sha ^target_ref
            if "rev-list" in cmd and "--count" in cmd:
                return (True, f"{unmerged_count}\n", "")

            return (True, "", "")

        return _try_stub

    def test_stale_ancestor_without_merge_log_returns_false(self):
        """Stale local branch that is an ancestor of target but has no merge log → not merged."""
        _try_mock = self._make_try(
            merge_log=False,
            origin_feature_exists=True,
            unmerged_count=0,  # tip is ancestor of target
        )
        with patch("services.sprint_manager.sprint_manager._try", side_effect=_try_mock):
            result = _is_issue_merged_into_target(42, "develop")
        assert result is False, "Stale ancestor without merge log must not count as merged"

    def test_merge_log_present_returns_true(self):
        """When a merge commit is found in git log → merged."""
        _try_mock = self._make_try(merge_log=True)
        with patch("services.sprint_manager.sprint_manager._try", side_effect=_try_mock):
            result = _is_issue_merged_into_target(42, "develop")
        assert result is True, "Merge log present → should be merged"

    def test_unmerged_unique_commits_returns_false(self):
        """Feature branch has commits not on target → not merged."""
        _try_mock = self._make_try(
            merge_log=False,
            origin_feature_exists=True,
            unmerged_count=3,  # 3 commits not yet on target
        )
        with patch("services.sprint_manager.sprint_manager._try", side_effect=_try_mock):
            result = _is_issue_merged_into_target(42, "develop")
        assert result is False, "Unmerged commits → not merged"

    def test_no_feature_branch_no_log_returns_false(self):
        """No feature branch exists and no merge log → not merged."""
        _try_mock = self._make_try(
            merge_log=False,
            origin_feature_exists=False,
            local_feature_exists=False,
        )
        with patch("services.sprint_manager.sprint_manager._try", side_effect=_try_mock):
            result = _is_issue_merged_into_target(42, "develop")
        assert result is False

    def test_explicit_feature_branch_with_merge_log(self):
        """Passing feature_branch explicitly works when merge log is present."""
        _try_mock = self._make_try(merge_log=True, origin_feature_exists=False)
        with patch("services.sprint_manager.sprint_manager._try", side_effect=_try_mock):
            result = _is_issue_merged_into_target(42, "develop", feature_branch="feature/42-fix-foo")
        assert result is True


# ---------------------------------------------------------------------------
# _git_verified_shipped_issues / _reporting_not_shipped_issues
# ---------------------------------------------------------------------------

class TestReportingHelpers:
    """Tests for the reporting split helpers."""

    def test_reporting_splits_done_without_git_into_not_shipped(self):
        """done-but-unverified tickets end up in not-shipped, git-verified stay in shipped."""
        iss_verified   = _issue(10, "done", "Verified ticket")
        iss_false_done = _issue(11, "done", "False done ticket")
        iss_skipped    = _issue(12, "skipped", "Skipped ticket")
        state = _state(iss_verified, iss_false_done, iss_skipped)

        def _mock_is_merged(issue_num, target, feature_branch=None):
            return issue_num == 10  # only issue 10 is truly merged

        with patch(
            "services.sprint_manager.sprint_manager._is_issue_merged_into_target",
            side_effect=_mock_is_merged,
        ):
            shipped     = _git_verified_shipped_issues(state, "develop")
            not_shipped = _reporting_not_shipped_issues(state, "develop")

        assert len(shipped) == 1
        assert shipped[0].number == 10

        not_shipped_nums = {i.number for i in not_shipped}
        assert 11 in not_shipped_nums, "false-done must appear in not-shipped"
        assert 12 in not_shipped_nums, "skipped must appear in not-shipped"
        assert 10 not in not_shipped_nums, "git-verified shipped must not appear in not-shipped"

    def test_all_verified_none_not_shipped(self):
        """All done issues are git-verified → not-shipped only contains skipped."""
        iss_done1   = _issue(1, "done")
        iss_done2   = _issue(2, "done")
        iss_skipped = _issue(3, "skipped")
        state = _state(iss_done1, iss_done2, iss_skipped)

        with patch(
            "services.sprint_manager.sprint_manager._is_issue_merged_into_target",
            return_value=True,
        ):
            shipped     = _git_verified_shipped_issues(state, "develop")
            not_shipped = _reporting_not_shipped_issues(state, "develop")

        assert len(shipped) == 2
        assert len(not_shipped) == 1
        assert not_shipped[0].number == 3

    def test_empty_state(self):
        """Empty issue list returns empty lists without errors."""
        state = _state()
        with patch(
            "services.sprint_manager.sprint_manager._is_issue_merged_into_target",
            return_value=False,
        ):
            shipped     = _git_verified_shipped_issues(state, "develop")
            not_shipped = _reporting_not_shipped_issues(state, "develop")

        assert shipped == []
        assert not_shipped == []


# ---------------------------------------------------------------------------
# _shipped_reconciliation_mismatch / _fail_loud_shipped_reconciliation
# ---------------------------------------------------------------------------

class TestShippedReconciliation:
    """Tests for P0 fail-loud reconciliation helpers."""

    def test_mismatch_returns_done_unverified_numbers(self):
        """_shipped_reconciliation_mismatch returns issue nums done but not verified."""
        state = _state(
            _issue(10, "done"),
            _issue(11, "done"),
            _issue(12, "skipped"),
        )

        def _mock_merged(issue_num, target, feature_branch=None):
            return issue_num == 10

        with patch(
            "services.sprint_manager.sprint_manager._is_issue_merged_into_target",
            side_effect=_mock_merged,
        ):
            result = _shipped_reconciliation_mismatch(state, "develop")

        assert result == [11], "Only issue 11 is done-but-unverified"

    def test_mismatch_empty_when_all_verified(self):
        """No mismatch when all done issues are git-verified."""
        state = _state(_issue(1, "done"), _issue(2, "done"))

        with patch(
            "services.sprint_manager.sprint_manager._is_issue_merged_into_target",
            return_value=True,
        ):
            result = _shipped_reconciliation_mismatch(state, "develop")

        assert result == []

    def test_fail_loud_logs_error_and_returns_mismatch(self, capsys):
        """_fail_loud_shipped_reconciliation logs error and prints [ERROR] when mismatch."""
        state = _state(_issue(20, "done"), _issue(21, "done"))

        def _mock_merged(issue_num, target, feature_branch=None):
            return issue_num == 20

        mock_log = MagicMock()
        with patch(
            "services.sprint_manager.sprint_manager._is_issue_merged_into_target",
            side_effect=_mock_merged,
        ), patch(
            "services.sprint_manager.sprint_manager.structured_log",
            mock_log,
        ):
            result = _fail_loud_shipped_reconciliation(state, "develop", "Sprint summary")

        assert result == [21]
        mock_log.error.assert_called_once()
        call_args = mock_log.error.call_args
        assert call_args[0][0] == "shipped_reconciliation_failed"
        captured = capsys.readouterr()
        assert "[ERROR]" in captured.out
        assert "#21" in captured.out

    def test_fail_loud_silent_when_all_clear(self, capsys):
        """_fail_loud_shipped_reconciliation returns [] and logs nothing when clean."""
        state = _state(_issue(30, "done"))

        mock_log = MagicMock()
        with patch(
            "services.sprint_manager.sprint_manager._is_issue_merged_into_target",
            return_value=True,
        ), patch(
            "services.sprint_manager.sprint_manager.structured_log",
            mock_log,
        ):
            result = _fail_loud_shipped_reconciliation(state, "develop", "Sprint PR")

        assert result == []
        mock_log.error.assert_not_called()
        captured = capsys.readouterr()
        assert "[ERROR]" not in captured.out


# ---------------------------------------------------------------------------
# _prune_stale_local_feature_branch
# ---------------------------------------------------------------------------

class TestPruneStaleLocalFeatureBranch:
    """Tests for the E2 stale-branch pruner."""

    def _make_try_for_prune(
        self,
        *,
        local_branch: str = "feature/42-fix-foo",
        origin_branch: str = "  origin/feature/42-fix-foo\n",
        local_sha: str = "aaa111",
        origin_sha: str = "bbb222",
        is_merged: bool = False,
        unmerged_count: int = 0,
        delete_ok: bool = True,
    ):
        """Build a _try mock for prune scenarios."""
        calls = []

        def _try_stub(*args, **kwargs):
            cmd = list(args)
            cmd_str = " ".join(str(a) for a in cmd)
            calls.append(cmd_str)

            # git fetch origin — treat as run (not _try), but in case
            if "fetch" in cmd:
                return (True, "", "")

            # git branch --list feature/42-*  (local lookup)
            if "branch" in cmd and "--list" in cmd and "-r" not in cmd:
                if "feature/42" in cmd_str:
                    return (True, f"  {local_branch}\n", "")
                return (True, "", "")

            # git rev-parse --verify (dispatch on origin vs local)
            if "rev-parse" in cmd and "--verify" in cmd:
                if "origin/feature" in cmd_str:
                    if origin_branch.strip():
                        return (True, f"{origin_sha}\n", "")
                    return (False, "", "not found")
                if local_branch in cmd_str:
                    return (True, f"{local_sha}\n", "")
                return (True, "sha\n", "")

            # git branch -r --list origin/feature/42-*
            if "branch" in cmd and "-r" in cmd and "--list" in cmd:
                return (True, origin_branch, "")

            # git branch -D (delete)
            if "branch" in cmd and "-D" in cmd:
                return (delete_ok, "", "")

            # rev-list --count (ancestor check)
            if "rev-list" in cmd and "--count" in cmd:
                return (True, f"{unmerged_count}\n", "")

            return (True, "", "")

        return _try_stub, calls

    def test_deletes_local_branch_when_sha_diverges_from_origin(self):
        """Local branch with different SHA from origin → deleted."""
        _try_stub, calls = self._make_try_for_prune(
            local_sha="aaa111",
            origin_sha="bbb222",  # different — stale local
        )

        with patch("services.sprint_manager.sprint_manager._try", side_effect=_try_stub), \
             patch("services.sprint_manager.sprint_manager._run", return_value=(0, "", "")):
            _prune_stale_local_feature_branch(42, "develop")

        # The delete was attempted (branch -D)
        delete_calls = [c for c in calls if "-D" in c]
        assert delete_calls, "Expected a branch -D call when SHAs diverge"

    def test_no_prune_when_local_matches_origin(self):
        """Local branch with same SHA as origin → not deleted."""
        _try_stub, calls = self._make_try_for_prune(
            local_sha="same111",
            origin_sha="same111",  # identical — not stale
        )

        with patch("services.sprint_manager.sprint_manager._try", side_effect=_try_stub), \
             patch("services.sprint_manager.sprint_manager._run", return_value=(0, "", "")):
            _prune_stale_local_feature_branch(42, "develop")

        delete_calls = [c for c in calls if "-D" in c]
        assert not delete_calls, "Should not delete branch when SHA matches origin"

    def test_no_local_branch_is_noop(self):
        """When no local feature branch exists, prune does nothing."""
        def _try_stub(*args, **kwargs):
            cmd = list(args)
            if "branch" in cmd and "--list" in cmd and "-r" not in cmd:
                return (True, "", "")  # no local branch
            return (True, "", "")

        with patch("services.sprint_manager.sprint_manager._try", side_effect=_try_stub), \
             patch("services.sprint_manager.sprint_manager._run", return_value=(0, "", "")):
            _prune_stale_local_feature_branch(42, "develop")

        # If no exception was raised, test passes — nothing to assert
        assert True

    def test_deletes_stale_ancestor_local_when_no_origin(self):
        """Local branch with 0 unique commits, no origin, not git-verified → deleted."""
        _try_stub, calls = self._make_try_for_prune(
            origin_branch="",  # no origin branch
            unmerged_count=0,  # ancestor of target
            is_merged=False,
        )

        with patch("services.sprint_manager.sprint_manager._try", side_effect=_try_stub), \
             patch("services.sprint_manager.sprint_manager._run", return_value=(0, "", "")), \
             patch(
                 "services.sprint_manager.sprint_manager._is_issue_merged_into_target",
                 return_value=False,
             ):
            _prune_stale_local_feature_branch(42, "develop")

        delete_calls = [c for c in calls if "-D" in c]
        assert delete_calls, "Expected branch -D when no origin and ancestor"
