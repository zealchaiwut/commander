"""Tests for issue #849: Add timeout to gh subprocess calls in stale-branch scan/cleanup.

Verifies that subprocess.TimeoutExpired is caught in _run_gh, _is_merged, and
_delete_branch, returning safe defaults when gh calls timeout.
"""
from __future__ import annotations

import subprocess
from unittest.mock import patch, MagicMock

import pytest

from routers import stale_branches_service


class TestRunGhTimeout:
    """Tests for _run_gh timeout handling (AC1)."""

    def test_run_gh_passes_timeout_30(self):
        """AC1: _run_gh passes timeout=30 to subprocess.run."""
        with patch("routers.stale_branches_service.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="test")
            stale_branches_service._run_gh(["api", "test"])
            mock_run.assert_called_once()
            # Verify timeout=30 was passed
            call_kwargs = mock_run.call_args[1]
            assert call_kwargs["timeout"] == 30

    def test_run_gh_catches_timeout_expired(self):
        """AC1: _run_gh catches subprocess.TimeoutExpired and returns empty string."""
        with patch("routers.stale_branches_service.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("gh", timeout=30)
            result = stale_branches_service._run_gh(["api", "test"])
            assert result == ""

    def test_run_gh_returns_empty_on_failure(self):
        """AC1: _run_gh returns empty string on non-zero return code."""
        with patch("routers.stale_branches_service.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            result = stale_branches_service._run_gh(["api", "test"])
            assert result == ""

    def test_run_gh_returns_stdout_on_success(self):
        """AC1: _run_gh returns stdout on success."""
        with patch("routers.stale_branches_service.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="branch-data")
            result = stale_branches_service._run_gh(["api", "test"])
            assert result == "branch-data"

    def test_run_gh_generic_exception_handling(self):
        """AC1: _run_gh generic Exception handler still present alongside TimeoutExpired."""
        with patch("routers.stale_branches_service.subprocess.run") as mock_run:
            mock_run.side_effect = RuntimeError("unexpected error")
            result = stale_branches_service._run_gh(["api", "test"])
            assert result == ""


class TestIsMergedTimeout:
    """Tests for _is_merged timeout handling (AC2)."""

    def test_is_merged_timeout_returns_false(self):
        """AC2: _is_merged catches subprocess.TimeoutExpired and returns False."""
        with patch.object(stale_branches_service, "_run_gh") as mock_run_gh:
            mock_run_gh.side_effect = subprocess.TimeoutExpired("gh", timeout=30)
            result = stale_branches_service._is_merged("owner/repo", "feature/123-test", "develop")
            assert result is False

    def test_is_merged_zero_commits_ahead_returns_true(self):
        """AC2: _is_merged returns True when zero commits ahead."""
        with patch.object(stale_branches_service, "_run_gh") as mock_run_gh:
            mock_run_gh.return_value = "0"
            result = stale_branches_service._is_merged("owner/repo", "feature/123-test", "develop")
            assert result is True

    def test_is_merged_commits_ahead_returns_false(self):
        """AC2: _is_merged returns False when commits ahead."""
        with patch.object(stale_branches_service, "_run_gh") as mock_run_gh:
            mock_run_gh.return_value = "5"
            result = stale_branches_service._is_merged("owner/repo", "feature/123-test", "develop")
            assert result is False

    def test_is_merged_empty_response_returns_false(self):
        """AC2: _is_merged returns False on empty response from _run_gh."""
        with patch.object(stale_branches_service, "_run_gh") as mock_run_gh:
            mock_run_gh.return_value = ""
            result = stale_branches_service._is_merged("owner/repo", "feature/123-test", "develop")
            assert result is False

    def test_is_merged_malformed_response_returns_false(self):
        """AC2: _is_merged returns False on non-integer response."""
        with patch.object(stale_branches_service, "_run_gh") as mock_run_gh:
            mock_run_gh.return_value = "not-a-number"
            result = stale_branches_service._is_merged("owner/repo", "feature/123-test", "develop")
            assert result is False


class TestDeleteBranchTimeout:
    """Tests for _delete_branch timeout handling (AC3)."""

    def test_delete_branch_passes_timeout_30(self):
        """AC3: _delete_branch passes timeout=30 to subprocess.run."""
        with patch("routers.stale_branches_service.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            stale_branches_service._delete_branch("owner/repo", "feature/123-test")
            mock_run.assert_called_once()
            # Verify timeout=30 was passed
            call_kwargs = mock_run.call_args[1]
            assert call_kwargs["timeout"] == 30

    def test_delete_branch_catches_timeout_expired(self):
        """AC3: _delete_branch catches subprocess.TimeoutExpired and returns False."""
        with patch("routers.stale_branches_service.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("gh", timeout=30)
            result = stale_branches_service._delete_branch("owner/repo", "feature/123-test")
            assert result is False

    def test_delete_branch_success_returns_true(self):
        """AC3: _delete_branch returns True on success (return code 0)."""
        with patch("routers.stale_branches_service.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = stale_branches_service._delete_branch("owner/repo", "feature/123-test")
            assert result is True

    def test_delete_branch_failure_returns_false(self):
        """AC3: _delete_branch returns False on failure (non-zero return code)."""
        with patch("routers.stale_branches_service.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            result = stale_branches_service._delete_branch("owner/repo", "feature/123-test")
            assert result is False

    def test_delete_branch_protected_returns_false(self):
        """AC3: _delete_branch returns False for protected branches without trying."""
        result = stale_branches_service._delete_branch("owner/repo", "develop")
        assert result is False

    def test_delete_branch_generic_exception_handling(self):
        """AC3: _delete_branch generic Exception handler still present alongside TimeoutExpired."""
        with patch("routers.stale_branches_service.subprocess.run") as mock_run:
            mock_run.side_effect = RuntimeError("unexpected error")
            result = stale_branches_service._delete_branch("owner/repo", "feature/123-test")
            assert result is False


class TestTimeoutIndependence:
    """AC4: Verify timeout in one function doesn't crash the entire scan."""

    def test_scan_continues_on_timeout_in_gh_call(self):
        """AC4: Scan continues when a gh call times out in one branch check."""
        with patch.object(stale_branches_service, "_list_remote_feature_branches") as mock_list:
            with patch.object(stale_branches_service, "_run_gh") as mock_run_gh:
                with patch.object(stale_branches_service, "_ticket_sprint_map") as mock_tsm:
                    mock_list.return_value = ["feature/100-test", "feature/101-test"]
                    mock_tsm.return_value = {100: "sprint-10", 101: "sprint-10"}
                    # First branch's gh call times out (handled by _run_gh), second succeeds
                    mock_run_gh.side_effect = [
                        "",  # First timeout -> empty string from _run_gh
                        "0",  # Second success
                    ]
                    result = stale_branches_service.scan_stale_branches("owner/repo")
                    # Scan should complete and return both branches
                    assert len(result["branches"]) == 2
                    # First branch marked as not-merged (timeout safe default via empty string)
                    assert result["branches"][0]["merged"] is False
                    # Second branch correctly marked as merged
                    assert result["branches"][1]["merged"] is True

    def test_cleanup_continues_on_timeout_in_gh_call(self):
        """AC4: Cleanup continues when a gh call times out in one branch check."""
        with patch.object(stale_branches_service, "_run_gh") as mock_run_gh:
            # First branch's gh call times out (returns ""), second succeeds
            mock_run_gh.side_effect = [
                "",  # Timeout -> empty string
                "0",  # Second branch is merged
            ]
            result = stale_branches_service.cleanup_stale_branches(
                "owner/repo",
                ["feature/100-test", "feature/101-test"],
                confirm=False
            )
            # Dry-run should complete and include both branches in the plan
            assert "to_delete" in result
            assert "skipped_unmerged" in result
            # Second branch should be marked for deletion (it's merged)
            assert "feature/101-test" in result["to_delete"]


class TestExistingExceptionHandling:
    """AC5: Verify existing except Exception handlers are preserved."""

    def test_run_gh_preserves_exception_handler(self):
        """AC5: _run_gh still catches generic Exception after TimeoutExpired."""
        with patch("routers.stale_branches_service.subprocess.run") as mock_run:
            # Test with a generic exception (not timeout)
            mock_run.side_effect = OSError("permission denied")
            result = stale_branches_service._run_gh(["api", "test"])
            assert result == ""

    def test_delete_branch_preserves_exception_handler(self):
        """AC5: _delete_branch still catches generic Exception after TimeoutExpired."""
        with patch("routers.stale_branches_service.subprocess.run") as mock_run:
            # Test with a generic exception (not timeout)
            mock_run.side_effect = OSError("permission denied")
            result = stale_branches_service._delete_branch("owner/repo", "feature/123-test")
            assert result is False

    def test_is_merged_preserves_exception_handler(self):
        """AC5: _is_merged still handles ValueError/TypeError for bad parse."""
        with patch.object(stale_branches_service, "_run_gh") as mock_run_gh:
            # Simulate a response that would trigger ValueError in int() conversion
            mock_run_gh.return_value = "malformed"
            result = stale_branches_service._is_merged("owner/repo", "feature/123-test", "develop")
            assert result is False
