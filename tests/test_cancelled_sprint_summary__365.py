"""Tests for issue #365 — Skip summary issue creation on cancelled sprints.

AC coverage:
  AC-1  Completed sprint creates a summary GitHub issue — happy path unchanged.
  AC-2  Cancelled sprint produces no summary GitHub issue on the repo.
  AC-3  Local summary .md file is not written on cancel.
  AC-4  Race condition: open summary issue closed with comment
        "Sprint was cancelled; summary not applicable".
  AC-5  Guard at source — end_reason check fires before create_summary_github_issue.
  AC-6  Unit tests cover both paths: completed → issue created; cancelled → skipped.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── sys.path setup ────────────────────────────────────────────────────────────

REPO_ROOT     = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SM_DIR        = REPO_ROOT / "services" / "sprint_manager"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))
sys.path.insert(0, str(SM_DIR))

# Mock github_client before sprint_manager is imported (module-level import).
if "github_client" not in sys.modules:
    sys.modules["github_client"] = MagicMock()

import sprint_manager as sm  # noqa: E402
from sprint_manager import (
    IssueState,
    SprintState,
    _close_cancelled_sprint_summary,
    write_sprint_summary,
)

# ── helpers ───────────────────────────────────────────────────────────────────

REPO = "test/repo"


def _make_state(sprint_label: str = "sprint-26", sprint_number: int = 26) -> SprintState:
    s = SprintState(sprint_label=sprint_label, sprint_number=sprint_number)
    issue = IssueState(number=365, title="Test issue", status="done")
    s.issues = [issue]
    s.wall_clock_secs = 60.0
    return s


# ── AC-1 / AC-6 path A: completed sprint → summary file + issue created ───────

class TestCompletedSprintCreatesSummary:
    """AC-1, AC-6: natural completion path is unchanged."""

    def test_summary_file_written(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sm, "SPRINTS_DIR", tmp_path)
        with patch.object(sm, "create_summary_github_issue", return_value=(1, "http://gh/1")):
            result = write_sprint_summary(
                _make_state(), elapsed_secs=60.0, end_reason="complete", repo_name=REPO
            )
        assert result is not None
        assert result.exists(), "summary .md must be written for a completed sprint"

    def test_create_summary_issue_called(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sm, "SPRINTS_DIR", tmp_path)
        with patch.object(sm, "create_summary_github_issue", return_value=(1, "http://gh/1")) as mock_create:
            write_sprint_summary(
                _make_state(), elapsed_secs=60.0, end_reason="complete", repo_name=REPO
            )
        mock_create.assert_called_once()


# ── AC-2 / AC-3 / AC-5 / AC-6 path B: cancelled sprint → nothing written ──────

class TestCancelledSprintSkipsSummary:
    """AC-2, AC-3, AC-5, AC-6: cancellation suppresses both local file and GitHub issue."""

    def test_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sm, "SPRINTS_DIR", tmp_path)
        with patch.object(sm, "create_summary_github_issue") as mock_create:
            result = write_sprint_summary(
                _make_state(), elapsed_secs=60.0, end_reason="cancelled", repo_name=REPO
            )
        assert result is None, "write_sprint_summary must return None for a cancelled sprint"

    def test_local_file_not_written(self, tmp_path, monkeypatch):
        """AC-3: local summary .md must not be created."""
        monkeypatch.setattr(sm, "SPRINTS_DIR", tmp_path)
        with patch.object(sm, "create_summary_github_issue"):
            write_sprint_summary(
                _make_state(), elapsed_secs=60.0, end_reason="cancelled", repo_name=REPO
            )
        md_files = list(tmp_path.glob("*.md"))
        assert md_files == [], f"No .md file should be written on cancel; found: {md_files}"

    def test_create_summary_issue_not_called(self, tmp_path, monkeypatch):
        """AC-5: guard fires before create_summary_github_issue."""
        monkeypatch.setattr(sm, "SPRINTS_DIR", tmp_path)
        with patch.object(sm, "create_summary_github_issue") as mock_create:
            write_sprint_summary(
                _make_state(), elapsed_secs=60.0, end_reason="cancelled", repo_name=REPO
            )
        mock_create.assert_not_called()


# ── AC-4: race condition — open issue closed with the exact comment ────────────

class TestCloseRaceSummaryIssue:
    """AC-4: _close_cancelled_sprint_summary closes an open summary issue."""

    def test_open_issue_closed_with_comment(self, monkeypatch):
        mock_gc = MagicMock()
        mock_gc.search_issues_by_title.return_value = [{"number": 99, "state": "open"}]
        monkeypatch.setattr(sm, "github_client", mock_gc)

        _close_cancelled_sprint_summary(26, "sprint-26", repo_name=REPO)

        mock_gc.add_comment.assert_called_once_with(
            99,
            "Sprint was cancelled; summary not applicable",
            repo_name=REPO,
        )
        mock_gc.close_issue.assert_called_once_with(99, repo_name=REPO)

    def test_already_closed_issue_not_touched(self, monkeypatch):
        mock_gc = MagicMock()
        mock_gc.search_issues_by_title.return_value = [{"number": 99, "state": "closed"}]
        monkeypatch.setattr(sm, "github_client", mock_gc)

        _close_cancelled_sprint_summary(26, "sprint-26", repo_name=REPO)

        mock_gc.add_comment.assert_not_called()
        mock_gc.close_issue.assert_not_called()

    def test_no_existing_issue_is_noop(self, monkeypatch):
        mock_gc = MagicMock()
        mock_gc.search_issues_by_title.return_value = []
        monkeypatch.setattr(sm, "github_client", mock_gc)

        _close_cancelled_sprint_summary(26, "sprint-26", repo_name=REPO)

        mock_gc.add_comment.assert_not_called()
        mock_gc.close_issue.assert_not_called()

    def test_sprint_number_none_falls_back_to_label(self, monkeypatch):
        """When sprint_number is None, the label is used to construct the issue title."""
        mock_gc = MagicMock()
        mock_gc.search_issues_by_title.return_value = []
        monkeypatch.setattr(sm, "github_client", mock_gc)

        _close_cancelled_sprint_summary(None, "sprint-26", repo_name=REPO)

        mock_gc.search_issues_by_title.assert_called_once_with(
            "Sprint sprint-26 Executive Summary", repo_name=REPO
        )

    def test_search_exception_is_swallowed(self, monkeypatch):
        """Errors during search must not propagate (best-effort cleanup)."""
        mock_gc = MagicMock()
        mock_gc.search_issues_by_title.side_effect = RuntimeError("gh offline")
        monkeypatch.setattr(sm, "github_client", mock_gc)

        _close_cancelled_sprint_summary(26, "sprint-26", repo_name=REPO)  # must not raise


# ── cancellation flag is accessible at module level ───────────────────────────

class TestCancellationFlag:
    def test_flag_exists_and_defaults_false(self):
        assert hasattr(sm, "_sprint_user_cancelled")
        # Migrated to threading.Event in #514; default state is unset (falsy).
        import threading
        assert isinstance(sm._sprint_user_cancelled, threading.Event)
        assert not sm._sprint_user_cancelled.is_set()
