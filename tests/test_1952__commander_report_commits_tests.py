"""Tests for issue #1952: populate completed[].commits and .tests from per-issue state.

AC1: IssueState has feature_commits field that serializes/deserializes correctly.
AC2: IssueState has tester_test_files field that serializes/deserializes correctly.
AC3: build_commander_report uses feature_commits from issue state for completed[].commits.
AC4: build_commander_report uses tester_test_files from issue state for completed[].tests.
AC5: completed[].commits and .tests fall back to [] when fields absent from state.
AC6: needs_review[].commits and .tests are also populated from per-issue state.
AC7: Sprint manager captures feature_commits via git log after coder_done.
AC8: Sprint manager captures tester_test_files via feature branch diff after coder_done.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _write_plan(sprints_dir: Path, label: str, **extra):
    data = {"state": "ready_to_merge", "started_at": "2026-01-01T00:00:00+00:00", **extra}
    (sprints_dir / f"{label}-plan.json").write_text(json.dumps(data))
    return data


def _write_state(sprints_dir: Path, label: str, issues: list, **extra):
    data = {"issues": issues, **extra}
    (sprints_dir / f"{label}-state.json").write_text(json.dumps(data))
    return data


# ── AC1/AC2: IssueState serialization ────────────────────────────────────────

class TestIssueStateNewFields:
    """AC1 + AC2: IssueState serializes and deserializes feature_commits and tester_test_files."""

    def test_feature_commits_default_empty(self):
        from services.sprint_manager.state import IssueState
        iss = IssueState(number=1, title="T")
        assert iss.feature_commits == []

    def test_tester_test_files_default_empty(self):
        from services.sprint_manager.state import IssueState
        iss = IssueState(number=1, title="T")
        assert iss.tester_test_files == []

    def test_feature_commits_roundtrip(self):
        from services.sprint_manager.state import IssueState
        shas = ["abc123def456", "789xyz"]
        iss = IssueState(number=1, title="T", feature_commits=shas)
        d = iss.to_dict()
        assert d["feature_commits"] == shas
        iss2 = IssueState.from_dict(d)
        assert iss2.feature_commits == shas

    def test_tester_test_files_roundtrip(self):
        from services.sprint_manager.state import IssueState
        files = ["tests/test_foo__ac1.py", "tests/test_foo__ac2.py"]
        iss = IssueState(number=1, title="T", tester_test_files=files)
        d = iss.to_dict()
        assert d["tester_test_files"] == files
        iss2 = IssueState.from_dict(d)
        assert iss2.tester_test_files == files

    def test_from_dict_missing_fields_fall_back_to_empty(self):
        """AC1+AC2: old state JSON without these keys deserializes to empty list."""
        from services.sprint_manager.state import IssueState
        d = {"number": 1, "title": "T", "status": "done"}
        iss = IssueState.from_dict(d)
        assert iss.feature_commits == []
        assert iss.tester_test_files == []


# ── AC3/AC4: build_commander_report reads from issue state ───────────────────

class TestBuildCommanderReportCommitsTests:
    """AC3 + AC4: build_commander_report populates commits/tests from per-issue state fields."""

    def test_completed_commits_populated_from_state(self, tmp_path):
        """AC3: completed[].commits comes from feature_commits in issue state."""
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-10")
        _write_state(sprints_dir, "sprint-10", [
            {
                "number": 1, "title": "Ticket A", "status": "done",
                "feature_commits": ["sha1abc", "sha2def"],
            },
        ])
        payload = build_commander_report(
            sprints_dir=sprints_dir,
            sprint_label="sprint-10",
            project="owner/repo",
        )
        assert len(payload["completed"]) == 1
        assert payload["completed"][0]["commits"] == ["sha1abc", "sha2def"], (
            "completed[].commits must be populated from feature_commits in issue state"
        )

    def test_completed_tests_populated_from_state(self, tmp_path):
        """AC4: completed[].tests comes from tester_test_files in issue state."""
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-10")
        _write_state(sprints_dir, "sprint-10", [
            {
                "number": 1, "title": "Ticket A", "status": "done",
                "tester_test_files": ["tests/test_feature__ac1.py", "tests/test_feature__ac2.py"],
            },
        ])
        payload = build_commander_report(
            sprints_dir=sprints_dir,
            sprint_label="sprint-10",
            project="owner/repo",
        )
        assert len(payload["completed"]) == 1
        assert payload["completed"][0]["tests"] == [
            "tests/test_feature__ac1.py", "tests/test_feature__ac2.py"
        ], "completed[].tests must be populated from tester_test_files in issue state"

    def test_completed_both_fields_populated(self, tmp_path):
        """AC3+AC4: both commits and tests are populated simultaneously."""
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-11")
        _write_state(sprints_dir, "sprint-11", [
            {
                "number": 7, "title": "Full ticket", "status": "done",
                "feature_commits": ["deadbeef", "cafebabe"],
                "tester_test_files": ["tests/test_x__ac1.py"],
            },
        ])
        payload = build_commander_report(
            sprints_dir=sprints_dir, sprint_label="sprint-11", project="owner/repo"
        )
        c = payload["completed"][0]
        assert c["commits"] == ["deadbeef", "cafebabe"]
        assert c["tests"] == ["tests/test_x__ac1.py"]

    def test_completed_fallback_to_empty_when_absent(self, tmp_path):
        """AC5: when feature_commits / tester_test_files are absent, fields are []."""
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-12")
        _write_state(sprints_dir, "sprint-12", [
            {"number": 3, "title": "Old ticket", "status": "done"},
        ])
        payload = build_commander_report(
            sprints_dir=sprints_dir, sprint_label="sprint-12", project="owner/repo"
        )
        c = payload["completed"][0]
        assert c["commits"] == [], "commits must default to [] when feature_commits absent"
        assert c["tests"] == [], "tests must default to [] when tester_test_files absent"


# ── AC6: needs_review entries also populated ─────────────────────────────────

class TestNeedsReviewCommitsTests:
    """AC6: needs_review[].commits and .tests are populated from per-issue state."""

    def test_needs_review_commits_populated(self, tmp_path):
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-20")
        _write_state(sprints_dir, "sprint-20", [
            {
                "number": 5, "title": "Needs review", "status": "needs_review",
                "feature_commits": ["abc111", "abc222"],
                "tester_test_files": ["tests/test_nr__ac1.py"],
            },
        ])
        payload = build_commander_report(
            sprints_dir=sprints_dir, sprint_label="sprint-20", project="owner/repo"
        )
        assert len(payload["needs_review"]) == 1
        nr = payload["needs_review"][0]
        assert nr["commits"] == ["abc111", "abc222"]
        assert nr["tests"] == ["tests/test_nr__ac1.py"]

    def test_needs_review_fallback_to_empty_when_absent(self, tmp_path):
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-21")
        _write_state(sprints_dir, "sprint-21", [
            {"number": 6, "title": "NR ticket", "status": "needs_review"},
        ])
        payload = build_commander_report(
            sprints_dir=sprints_dir, sprint_label="sprint-21", project="owner/repo"
        )
        nr = payload["needs_review"][0]
        assert nr["commits"] == []
        assert nr["tests"] == []


# ── AC7/AC8: sprint_manager captures fields after coder_done ─────────────────

class TestCaptureAfterCoderDone:
    """AC7 + AC8: _capture_feature_branch_data populates IssueState fields from git."""

    def test_capture_commits_from_git_log(self):
        """AC7: feature_commits is populated from git log output."""
        from services.sprint_manager.sprint_manager import _capture_feature_branch_data
        from services.sprint_manager.state import IssueState

        def _mock_try(*cmd, cwd=None):
            if "log" in cmd:
                return (True, "sha111\nsha222\nsha333\n", "")
            return (True, "", "")

        iss = IssueState(number=1, title="T")
        _capture_feature_branch_data(
            issue_state=iss,
            feature_branch="feature/1-my-ticket",
            target_branch="develop",
            cwd=Path("/tmp/wt"),
            _try_fn=_mock_try,
        )
        assert iss.feature_commits == ["sha111", "sha222", "sha333"]

    def test_capture_test_files_from_diff(self):
        """AC8: tester_test_files is populated from git diff --name-only filtered to tests/."""
        from services.sprint_manager.sprint_manager import _capture_feature_branch_data
        from services.sprint_manager.state import IssueState

        def _mock_try(*cmd, cwd=None):
            if "log" in cmd:
                return (True, "sha111\n", "")
            if "diff" in cmd:
                return (True, "tests/test_feature__ac1.py\napps/dashboard/server.py\ntests/test_feature__ac2.py\n", "")
            return (True, "", "")

        iss = IssueState(number=1, title="T")
        _capture_feature_branch_data(
            issue_state=iss,
            feature_branch="feature/1-ticket",
            target_branch="develop",
            cwd=Path("/tmp/wt"),
            _try_fn=_mock_try,
        )
        assert iss.tester_test_files == [
            "tests/test_feature__ac1.py",
            "tests/test_feature__ac2.py",
        ]

    def test_capture_handles_empty_log_gracefully(self):
        """AC7: when git log returns empty output, feature_commits stays []."""
        from services.sprint_manager.sprint_manager import _capture_feature_branch_data
        from services.sprint_manager.state import IssueState

        def _mock_try(*cmd, cwd=None):
            return (True, "", "")

        iss = IssueState(number=1, title="T")
        _capture_feature_branch_data(
            issue_state=iss,
            feature_branch="feature/1-ticket",
            target_branch="develop",
            cwd=Path("/tmp/wt"),
            _try_fn=_mock_try,
        )
        assert iss.feature_commits == []
        assert iss.tester_test_files == []

    def test_capture_handles_git_failure_gracefully(self):
        """AC7+AC8: when git commands fail, fields remain [] — no exception raised."""
        from services.sprint_manager.sprint_manager import _capture_feature_branch_data
        from services.sprint_manager.state import IssueState

        def _mock_try(*cmd, cwd=None):
            return (False, "", "error")

        iss = IssueState(number=1, title="T")
        _capture_feature_branch_data(
            issue_state=iss,
            feature_branch="feature/1-ticket",
            target_branch="develop",
            cwd=Path("/tmp/wt"),
            _try_fn=_mock_try,
        )
        assert iss.feature_commits == []
        assert iss.tester_test_files == []

    def test_capture_no_test_files_in_diff(self):
        """AC8: when diff has no tests/ files, tester_test_files is []."""
        from services.sprint_manager.sprint_manager import _capture_feature_branch_data
        from services.sprint_manager.state import IssueState

        def _mock_try(*cmd, cwd=None):
            if "log" in cmd:
                return (True, "sha001\n", "")
            if "diff" in cmd:
                return (True, "apps/dashboard/server.py\napps/dashboard/config.py\n", "")
            return (True, "", "")

        iss = IssueState(number=1, title="T")
        _capture_feature_branch_data(
            issue_state=iss,
            feature_branch="feature/1-ticket",
            target_branch="develop",
            cwd=Path("/tmp/wt"),
            _try_fn=_mock_try,
        )
        assert iss.feature_commits == ["sha001"]
        assert iss.tester_test_files == []
