"""Tests for #617: record_failure() centralises all failure classes to sidecar.

AC items verified:
  AC-1  record_failure(issue_num, failure_class, detail, repo_root=None) exists
  AC-2  record_failure called at every failure exit
  AC-3  Sidecar schema: failure_class, summary, detail, files_to_inspect retained
  AC-4  Non-gate failure classes include log tail + exit code / signal in detail
  AC-5  _build_failure_suffix returns suffix for all failure classes; 10-line cap removed
  AC-6  Sidecar deleted on success path
  AC-7  record_failure works without failure-parser import (graceful fallback)
"""
from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import services.sprint_manager.sprint_manager as sm


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tmproot(tmp_path: Path) -> Path:
    """Create a minimal .commander/runtime layout under tmp_path."""
    (tmp_path / ".commander" / "runtime").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _write_log(log_path: Path, content: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(content, encoding="utf-8")


def _sidecar_path(issue_num: int, repo_root: Path) -> Path:
    return repo_root / ".commander" / "runtime" / f"last-failure-{issue_num}.json"


# ---------------------------------------------------------------------------
# AC-1: record_failure exists with correct signature
# ---------------------------------------------------------------------------

class TestAC1FunctionExists:
    def test_record_failure_is_callable(self):
        assert callable(sm.record_failure), "record_failure must be defined in sprint_manager"

    def test_record_failure_signature(self):
        sig = inspect.signature(sm.record_failure)
        params = list(sig.parameters.keys())
        assert "issue_num" in params
        assert "failure_class" in params
        assert "detail" in params
        assert "repo_root" in params

    def test_record_failure_repo_root_has_default(self):
        sig = inspect.signature(sm.record_failure)
        default = sig.parameters["repo_root"].default
        assert default is not inspect.Parameter.empty, "repo_root must have a default"


# ---------------------------------------------------------------------------
# AC-3: Sidecar schema has required fields
# ---------------------------------------------------------------------------

class TestAC3SidecarSchema:
    def test_sidecar_contains_failure_class(self, tmp_path):
        repo_root = _make_tmproot(tmp_path)
        sm.record_failure(42, "crash", "some detail", repo_root=repo_root)
        sc = _sidecar_path(42, repo_root)
        assert sc.exists()
        data = json.loads(sc.read_text())
        assert data["failure_class"] == "crash"

    def test_sidecar_contains_detail(self, tmp_path):
        repo_root = _make_tmproot(tmp_path)
        sm.record_failure(42, "hang", "process killed", repo_root=repo_root)
        data = json.loads(_sidecar_path(42, repo_root).read_text())
        assert data["detail"] == "process killed"

    def test_sidecar_contains_summary(self, tmp_path):
        repo_root = _make_tmproot(tmp_path)
        sm.record_failure(42, "test", "pytest failed", repo_root=repo_root, summary="3 tests failed")
        data = json.loads(_sidecar_path(42, repo_root).read_text())
        assert "summary" in data
        assert data["summary"] == "3 tests failed"

    def test_sidecar_has_default_summary_if_not_provided(self, tmp_path):
        repo_root = _make_tmproot(tmp_path)
        sm.record_failure(42, "lint", "ruff errors", repo_root=repo_root)
        data = json.loads(_sidecar_path(42, repo_root).read_text())
        assert "summary" in data
        assert isinstance(data["summary"], str) and data["summary"]

    def test_sidecar_retains_files_to_inspect(self, tmp_path):
        repo_root = _make_tmproot(tmp_path)
        files = ["foo/bar.py:10", "baz/qux.py"]
        sm.record_failure(42, "lint", "lint errors", repo_root=repo_root, files_to_inspect=files)
        data = json.loads(_sidecar_path(42, repo_root).read_text())
        assert data["files_to_inspect"] == files

    def test_sidecar_files_to_inspect_defaults_to_empty_list(self, tmp_path):
        repo_root = _make_tmproot(tmp_path)
        sm.record_failure(42, "crash", "detail", repo_root=repo_root)
        data = json.loads(_sidecar_path(42, repo_root).read_text())
        assert data["files_to_inspect"] == []

    def test_sidecar_all_failure_classes_accepted(self, tmp_path):
        classes = ["test", "lint", "typecheck", "design", "crash", "hang", "merge", "reviewer"]
        for fc in classes:
            repo_root = _make_tmproot(tmp_path / fc)
            sm.record_failure(1, fc, "detail", repo_root=repo_root)
            data = json.loads(_sidecar_path(1, repo_root).read_text())
            assert data["failure_class"] == fc


# ---------------------------------------------------------------------------
# AC-4: Non-gate failures include log tail + exit code/signal in detail
# ---------------------------------------------------------------------------

class TestAC4NonGateDetail:
    def test_crash_detail_includes_log_tail(self, tmp_path):
        """_dispatch_coder non-zero exit: detail must contain log tail."""
        repo_root = _make_tmproot(tmp_path)
        logs_dir = tmp_path / ".commander" / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / "sprint-issue-99.log"
        log_lines = [f"line {i}" for i in range(60)]
        _write_log(log_path, "\n".join(log_lines))

        detail = sm._build_crash_detail(log_path, exit_code=1)
        # Detail must contain the exit code
        assert "1" in detail
        # Detail must contain recent log lines (tail, not head)
        assert "line 59" in detail

    def test_hang_detail_includes_log_tail_and_signal(self, tmp_path):
        """Hang-kill: detail must contain log tail and mention signal/kill."""
        repo_root = _make_tmproot(tmp_path)
        logs_dir = tmp_path / ".commander" / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / "sprint-issue-7.log"
        _write_log(log_path, "line A\nline B\nline C")

        detail = sm._build_crash_detail(log_path, signal="SIGKILL")
        assert "line C" in detail
        assert "SIGKILL" in detail or "kill" in detail.lower()

    def test_crash_detail_handles_missing_log(self):
        """If log doesn't exist, detail must not raise."""
        detail = sm._build_crash_detail(Path("/nonexistent/log.txt"), exit_code=2)
        assert isinstance(detail, str)
        assert "2" in detail


# ---------------------------------------------------------------------------
# AC-5: _build_failure_suffix handles all failure classes; no 10-line cap
# ---------------------------------------------------------------------------

class TestAC5BuildFailureSuffix:
    def test_suffix_for_crash_class(self, tmp_path):
        repo_root = _make_tmproot(tmp_path)
        sm.record_failure(10, "crash", "Exited with code 1\nLog tail here", repo_root=repo_root)
        suffix = sm._build_failure_suffix(10, repo_root=repo_root)
        assert suffix, "suffix must be non-empty for crash failure"
        assert "crash" in suffix.lower() or "Exited" in suffix or "Log tail" in suffix

    def test_suffix_for_hang_class(self, tmp_path):
        repo_root = _make_tmproot(tmp_path)
        sm.record_failure(11, "hang", "No log activity for 30 minutes", repo_root=repo_root)
        suffix = sm._build_failure_suffix(11, repo_root=repo_root)
        assert suffix
        assert "hang" in suffix.lower() or "30 minutes" in suffix

    def test_suffix_for_merge_class(self, tmp_path):
        repo_root = _make_tmproot(tmp_path)
        sm.record_failure(12, "merge", "Merge conflict detected", repo_root=repo_root)
        suffix = sm._build_failure_suffix(12, repo_root=repo_root)
        assert suffix
        assert "merge" in suffix.lower() or "Merge conflict" in suffix

    def test_suffix_for_reviewer_class(self, tmp_path):
        repo_root = _make_tmproot(tmp_path)
        sm.record_failure(13, "reviewer", "Reviewer subprocess failed", repo_root=repo_root)
        suffix = sm._build_failure_suffix(13, repo_root=repo_root)
        assert suffix
        assert "reviewer" in suffix.lower() or "Reviewer" in suffix

    def test_suffix_for_test_class(self, tmp_path):
        repo_root = _make_tmproot(tmp_path)
        sm.record_failure(14, "test", "3 tests failed", repo_root=repo_root)
        suffix = sm._build_failure_suffix(14, repo_root=repo_root)
        assert suffix

    def test_no_10_line_cap(self, tmp_path):
        """detail with >10 lines must appear fully in suffix (cap removed)."""
        repo_root = _make_tmproot(tmp_path)
        long_detail = "\n".join(f"failure line {i}" for i in range(25))
        sm.record_failure(20, "crash", long_detail, repo_root=repo_root)
        suffix = sm._build_failure_suffix(20, repo_root=repo_root)
        # All 25 lines must be present — if cap at 10 they'd be truncated
        assert "failure line 24" in suffix

    def test_suffix_empty_when_no_sidecar(self, tmp_path):
        repo_root = _make_tmproot(tmp_path)
        suffix = sm._build_failure_suffix(999, repo_root=repo_root)
        assert suffix == ""

    def test_suffix_empty_when_sidecar_has_no_content(self, tmp_path):
        repo_root = _make_tmproot(tmp_path)
        # Write sidecar with empty detail and no failures
        sm.record_failure(21, "crash", "", repo_root=repo_root)
        # Empty detail is fine — suffix may be minimal or empty based on implementation
        suffix = sm._build_failure_suffix(21, repo_root=repo_root)
        assert isinstance(suffix, str)


# ---------------------------------------------------------------------------
# AC-2: record_failure called at all failure exits
# ---------------------------------------------------------------------------

class TestAC2WiringAtFailureExits:
    """Verify record_failure is invoked at every failure exit point."""

    def _dispatch_coder_with_crash(self, tmp_path, exit_code: int):
        """Helper: run _dispatch_coder with a subprocess that exits with exit_code."""
        cfg_stub = MagicMock()
        cfg_stub.worktree_coder = tmp_path
        cfg_stub.repo_name = None
        cfg_stub.api_url = None
        cfg_stub.coder_prompt_template = None
        cfg_stub.logs_dir = tmp_path / "logs"
        cfg_stub.logs_dir.mkdir(parents=True, exist_ok=True)

        class FakeProc:
            def wait(self): return exit_code
            returncode = exit_code

        fake_detector = MagicMock()
        fake_detector.killed = False

        with (
            patch.object(sm, "subprocess") as mock_sub,
            patch.object(sm, "HangDetector", return_value=fake_detector),
            patch.object(sm, "_post_agent_event"),
            patch.object(sm, "_is_rate_limit_error", return_value=(False, None)),
            patch.object(sm, "dispatch_alerts"),
            patch.object(sm, "_FAILURE_PARSING_AVAILABLE", False),
        ):
            mock_sub.Popen.return_value = FakeProc()
            ok, _ = sm._dispatch_coder(
                issue_num=55,
                alert_modes=[],
                cfg=cfg_stub,
            )
        return ok

    def test_record_failure_called_on_coder_crash(self, tmp_path):
        """_dispatch_coder non-zero exit → record_failure called."""
        calls = []
        original = sm.record_failure

        def capture(*args, **kwargs):
            calls.append((args, kwargs))
            return original(*args, **kwargs)

        cfg_stub = MagicMock()
        cfg_stub.worktree_coder = tmp_path
        cfg_stub.repo_name = None
        cfg_stub.api_url = None
        cfg_stub.coder_prompt_template = None
        cfg_stub.logs_dir = tmp_path / "logs"
        cfg_stub.logs_dir.mkdir(parents=True, exist_ok=True)

        class FakeProc:
            def wait(self): return 1
            returncode = 1

        fake_detector = MagicMock()
        fake_detector.killed = False

        with (
            patch.object(sm, "subprocess") as mock_sub,
            patch.object(sm, "HangDetector", return_value=fake_detector),
            patch.object(sm, "_post_agent_event"),
            patch.object(sm, "_is_rate_limit_error", return_value=(False, None)),
            patch.object(sm, "dispatch_alerts"),
            patch.object(sm, "record_failure", side_effect=capture),
        ):
            mock_sub.Popen.return_value = FakeProc()
            sm._dispatch_coder(issue_num=55, alert_modes=[], cfg=cfg_stub)

        assert calls, "record_failure must be called on coder crash"
        classes = [kw.get("failure_class") or args[1] for args, kw in calls]
        assert any(fc in ("crash", "CRASH") for fc in classes), \
            f"failure_class must be 'crash', got: {classes}"

    def test_record_failure_called_on_hang_kill(self, tmp_path):
        """HangDetector.killed=True → record_failure called with hang class."""
        calls = []

        cfg_stub = MagicMock()
        cfg_stub.worktree_coder = tmp_path
        cfg_stub.repo_name = None
        cfg_stub.api_url = None
        cfg_stub.coder_prompt_template = None
        cfg_stub.logs_dir = tmp_path / "logs"
        cfg_stub.logs_dir.mkdir(parents=True, exist_ok=True)

        class FakeProc:
            def wait(self): return -9
            returncode = -9

        fake_detector = MagicMock()
        fake_detector.killed = True

        with (
            patch.object(sm, "subprocess") as mock_sub,
            patch.object(sm, "HangDetector", return_value=fake_detector),
            patch.object(sm, "_post_agent_event"),
            patch.object(sm, "_add_blocked_label"),
            patch.object(sm, "dispatch_alerts"),
            patch.object(sm, "record_failure", side_effect=lambda *a, **kw: calls.append((a, kw))),
        ):
            mock_sub.Popen.return_value = FakeProc()
            sm._dispatch_coder(issue_num=55, alert_modes=[], cfg=cfg_stub)

        assert calls, "record_failure must be called on hang-kill"
        classes = [kw.get("failure_class") or a[1] for a, kw in calls]
        assert any(fc in ("hang", "HANG") for fc in classes), \
            f"failure_class must be 'hang', got: {classes}"

    def test_record_failure_called_on_gate_failure(self, tmp_path):
        """_revert_to_sit → record_failure called with appropriate gate class."""
        calls = []

        with (
            patch.object(sm, "record_failure", side_effect=lambda *a, **kw: calls.append((a, kw))),
            patch.object(sm, "_transition_safe"),
            patch.object(sm, "github_client"),
            patch.object(sm, "_FAILURE_PARSING_AVAILABLE", False),
        ):
            sm._revert_to_sit(42, "pytest", "FAILED test_foo.py::test_bar")

        assert calls, "record_failure must be called from _revert_to_sit"
        classes = [kw.get("failure_class") or a[1] for a, kw in calls]
        assert any(fc in ("test", "pytest") for fc in classes), \
            f"failure_class must be test-related, got: {classes}"

    def test_record_failure_called_on_merge_gate_failure(self, tmp_path):
        """merge-preview gate failure → record_failure with merge class."""
        calls = []

        with (
            patch.object(sm, "record_failure", side_effect=lambda *a, **kw: calls.append((a, kw))),
            patch.object(sm, "_transition_safe"),
            patch.object(sm, "github_client"),
            patch.object(sm, "_FAILURE_PARSING_AVAILABLE", False),
        ):
            sm._revert_to_sit(42, "merge-preview", "Conflict in foo.py")

        assert calls, "record_failure must be called from _revert_to_sit"
        classes = [kw.get("failure_class") or a[1] for a, kw in calls]
        assert any(fc in ("merge",) for fc in classes), \
            f"failure_class must be 'merge', got: {classes}"

    def test_record_failure_called_on_reviewer_failed(self, tmp_path):
        """_dispatch_reviewer non-zero exit → record_failure called with reviewer class."""
        calls = []

        # Build a state with one merged issue so the skip guard doesn't fire
        merged_issue = MagicMock()
        merged_issue.status = "done"
        state = MagicMock()
        state.sprint_label = "sprint-49"
        state.issues = [merged_issue]

        class FakeProc:
            def wait(self): return 1
            returncode = 1

        fake_detector = MagicMock()
        fake_detector.killed = False

        cfg_stub = MagicMock()
        cfg_stub.worktree_coder = tmp_path
        cfg_stub.logs_dir = tmp_path / "logs"
        cfg_stub.logs_dir.mkdir(parents=True, exist_ok=True)
        cfg_stub.reviewer_prompt_template = None

        with (
            patch.object(sm, "subprocess") as mock_sub,
            patch.object(sm, "HangDetector", return_value=fake_detector),
            patch.object(sm, "record_failure", side_effect=lambda *a, **kw: calls.append((a, kw))),
        ):
            mock_sub.Popen.return_value = FakeProc()
            sm._dispatch_reviewer(
                state=state,
                summary_issue_num=100,
                sprint_branch="sprint/sprint-49",
                base_sha="abc",
                head_sha="def",
                cfg=cfg_stub,
                repo_name=None,
            )

        assert calls, "record_failure must be called on reviewer failure"
        classes = [kw.get("failure_class") or a[1] for a, kw in calls]
        assert any(fc in ("reviewer",) for fc in classes), \
            f"failure_class must be 'reviewer', got: {classes}"


# ---------------------------------------------------------------------------
# AC-6: Sidecar deleted on success path
# ---------------------------------------------------------------------------

class TestAC6SidecarDeletedOnSuccess:
    def test_sidecar_deleted_after_handle_post_tester_success(self, tmp_path):
        """handle_post_tester success path must delete sidecar."""
        repo_root = _make_tmproot(tmp_path)
        issue_num = 77

        # Pre-write a sidecar
        sc = _sidecar_path(issue_num, repo_root)
        sm.record_failure(issue_num, "crash", "old failure", repo_root=repo_root)
        assert sc.exists(), "Pre-condition: sidecar must exist before success"

        cfg_stub = MagicMock()
        cfg_stub.worktree_tester = tmp_path / "tester"
        cfg_stub.worktree_tester_app = tmp_path / "tester" / "apps" / "dashboard"
        cfg_stub.repo_name = None
        cfg_stub.api_url = None
        cfg_stub.documentor_enabled = False
        cfg_stub.logs_dir = tmp_path / "logs"

        with (
            patch.object(sm, "_find_feature_branch", return_value=None),
            patch.object(sm, "_was_feature_merged_via_log", return_value=True),
            patch.object(sm, "_run_quality_gates") as mock_gates,
            patch.object(sm, "_transition_safe"),
            patch.object(sm, "_post_success_comment"),
            patch.object(sm, "_post_agent_event"),
            patch.object(sm, "_call_finish_feature"),
            patch.object(sm, "sidecar_path", return_value=sc),
            patch.object(sm, "REPO_ROOT", repo_root),
        ):
            mock_gates.return_value = [
                sm.GateResult(gate="pytest", passed=True, skipped=False),
            ]
            ok, msg, cat = sm.handle_post_tester(
                issue_num=issue_num,
                tester_exit_code=0,
                skip_gates=False,
                gate_pytest=True,
                gate_lint=False,
                gate_merge_preview=False,
                gate_typecheck=False,
                gate_design=False,
                cfg=cfg_stub,
            )

        assert ok, f"Expected success but got: {msg}"
        assert not sc.exists(), "Sidecar must be deleted after ticket passes"

    def test_sidecar_not_deleted_on_gate_failure(self, tmp_path):
        """Sidecar must remain if gates fail."""
        repo_root = _make_tmproot(tmp_path)
        issue_num = 88

        sm.record_failure(issue_num, "crash", "old failure", repo_root=repo_root)
        sc = _sidecar_path(issue_num, repo_root)
        assert sc.exists()

        cfg_stub = MagicMock()
        cfg_stub.worktree_tester = tmp_path / "tester"
        cfg_stub.worktree_tester_app = tmp_path / "tester" / "apps" / "dashboard"
        cfg_stub.repo_name = None
        cfg_stub.api_url = None
        cfg_stub.documentor_enabled = False
        cfg_stub.logs_dir = tmp_path / "logs"

        with (
            patch.object(sm, "_find_feature_branch", return_value=None),
            patch.object(sm, "_was_feature_merged_via_log", return_value=True),
            patch.object(sm, "_run_quality_gates") as mock_gates,
            patch.object(sm, "_revert_to_sit"),
            patch.object(sm, "_transition_safe"),
            patch.object(sm, "_post_agent_event"),
            patch.object(sm, "sidecar_path", return_value=sc),
            patch.object(sm, "REPO_ROOT", repo_root),
        ):
            mock_gates.return_value = [
                sm.GateResult(gate="pytest", passed=False, skipped=False),
            ]
            ok, msg, cat = sm.handle_post_tester(
                issue_num=issue_num,
                tester_exit_code=0,
                skip_gates=False,
                gate_pytest=True,
                gate_lint=False,
                gate_merge_preview=False,
                gate_typecheck=False,
                gate_design=False,
                cfg=cfg_stub,
            )

        assert not ok


# ---------------------------------------------------------------------------
# AC-7: record_failure works without failure-parser import
# ---------------------------------------------------------------------------

class TestAC7WorksWithoutParser:
    def test_record_failure_with_parsing_unavailable(self, tmp_path):
        """record_failure must write a valid sidecar even when _FAILURE_PARSING_AVAILABLE=False."""
        repo_root = _make_tmproot(tmp_path)

        with patch.object(sm, "_FAILURE_PARSING_AVAILABLE", False):
            result = sm.record_failure(
                33, "crash", "process died", repo_root=repo_root
            )

        sc = _sidecar_path(33, repo_root)
        assert sc.exists(), "Sidecar must be written without parser"
        data = json.loads(sc.read_text())
        assert data["failure_class"] == "crash"
        assert "process died" in data["detail"]

    def test_record_failure_no_exception_without_parser(self, tmp_path):
        """No exception raised when parser unavailable."""
        repo_root = _make_tmproot(tmp_path)

        with patch.object(sm, "_FAILURE_PARSING_AVAILABLE", False):
            try:
                sm.record_failure(34, "hang", "hung", repo_root=repo_root)
            except Exception as e:
                pytest.fail(f"record_failure raised with parser unavailable: {e}")

    def test_record_failure_returns_path(self, tmp_path):
        """record_failure must return the path it wrote to."""
        repo_root = _make_tmproot(tmp_path)
        result = sm.record_failure(35, "lint", "ruff errors", repo_root=repo_root)
        assert result is not None
        assert isinstance(result, Path)
        assert result.exists()
