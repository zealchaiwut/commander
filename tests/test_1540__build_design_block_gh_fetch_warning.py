"""Tests for issue #1540: _build_design_block logs WARNING on gh-fetch failure.

AC items verified:
  AC-1  Exception path: when subprocess.run raises (e.g. TimeoutExpired), a
        WARNING line is printed to stdout before falling back.
  AC-2  Non-zero returncode path: when gh exits non-zero, a WARNING line is
        printed to stdout before falling back.
  AC-3  Fallback behaviour: in both failure cases execution continues and returns
        a valid string (no exception propagated).
  AC-4  Warning content: the WARNING line names the issue number so context is
        identifiable in logs.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

from services.sprint_manager.sprint_manager import _build_design_block  # noqa: E402

DESIGN_CONTENT = """\
# Project Design

## Sprint Board

The sprint board shows active and past sprints.

## Ticket Lifecycle

Tickets flow: backlog → in-progress → sit → uat.
"""


def _make_fail_mock(returncode: int = 1, stderr: str = "error") -> MagicMock:
    mock = MagicMock()
    mock.returncode = returncode
    mock.stdout = ""
    mock.stderr = stderr
    return mock


# ---------------------------------------------------------------------------
# AC-1: exception path logs WARNING
# ---------------------------------------------------------------------------

class TestAC1ExceptionPathLogsWarning:
    def test_timeout_exception_logs_warning(self, tmp_path, capsys):
        """subprocess.run raising TimeoutExpired causes a WARNING line on stdout."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=30)):
            _build_design_block(42, "owner/repo", tmp_path)

        captured = capsys.readouterr()
        assert "WARNING" in captured.out, (
            "A WARNING must be logged to stdout when subprocess.run raises"
        )

    def test_os_error_exception_logs_warning(self, tmp_path, capsys):
        """subprocess.run raising OSError (e.g. gh not found) logs WARNING."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")

        with patch("subprocess.run", side_effect=OSError("gh not found")):
            _build_design_block(42, "owner/repo", tmp_path)

        captured = capsys.readouterr()
        assert "WARNING" in captured.out, (
            "A WARNING must be logged to stdout when subprocess.run raises OSError"
        )

    def test_generic_exception_logs_warning(self, tmp_path, capsys):
        """subprocess.run raising a generic Exception logs WARNING."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")

        with patch("subprocess.run", side_effect=Exception("unexpected error")):
            _build_design_block(42, "owner/repo", tmp_path)

        captured = capsys.readouterr()
        assert "WARNING" in captured.out, (
            "A WARNING must be logged to stdout when subprocess.run raises any exception"
        )


# ---------------------------------------------------------------------------
# AC-2: non-zero returncode path logs WARNING
# ---------------------------------------------------------------------------

class TestAC2NonZeroReturncodeLogsWarning:
    def test_nonzero_returncode_logs_warning(self, tmp_path, capsys):
        """gh exiting non-zero causes a WARNING line on stdout."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")

        with patch("subprocess.run", return_value=_make_fail_mock(returncode=1)):
            _build_design_block(42, "owner/repo", tmp_path)

        captured = capsys.readouterr()
        assert "WARNING" in captured.out, (
            "A WARNING must be logged to stdout when gh exits non-zero"
        )

    def test_returncode_128_logs_warning(self, tmp_path, capsys):
        """gh exiting 128 (auth failure) logs a WARNING line."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")

        with patch("subprocess.run", return_value=_make_fail_mock(returncode=128)):
            _build_design_block(42, "owner/repo", tmp_path)

        captured = capsys.readouterr()
        assert "WARNING" in captured.out, (
            "A WARNING must be logged when gh exits 128 (auth failure)"
        )


# ---------------------------------------------------------------------------
# AC-3: fallback — no exception propagated, returns a string
# ---------------------------------------------------------------------------

class TestAC3FallbackBehaviour:
    def test_exception_path_does_not_raise(self, tmp_path):
        """subprocess.run raising does not propagate — _build_design_block returns."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=30)):
            block = _build_design_block(42, "owner/repo", tmp_path)

        assert isinstance(block, str), "Must return a string even when subprocess raises"

    def test_nonzero_returncode_does_not_raise(self, tmp_path):
        """Non-zero gh exit does not propagate — _build_design_block returns."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")

        with patch("subprocess.run", return_value=_make_fail_mock(returncode=1)):
            block = _build_design_block(42, "owner/repo", tmp_path)

        assert isinstance(block, str), "Must return a string even when gh exits non-zero"

    def test_exception_path_falls_back_to_heading_index(self, tmp_path):
        """After a gh-fetch exception the function falls back to the heading index."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=30)):
            block = _build_design_block(42, "owner/repo", tmp_path)

        # No refs known → heading index injected
        assert "Sprint Board" in block or "Ticket Lifecycle" in block, (
            "Fallback must inject the heading index from DESIGN.md"
        )

    def test_nonzero_returncode_falls_back_to_heading_index(self, tmp_path):
        """After a non-zero gh exit the function falls back to the heading index."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")

        with patch("subprocess.run", return_value=_make_fail_mock(returncode=1)):
            block = _build_design_block(42, "owner/repo", tmp_path)

        assert "Sprint Board" in block or "Ticket Lifecycle" in block, (
            "Fallback must inject the heading index from DESIGN.md"
        )


# ---------------------------------------------------------------------------
# AC-4: warning content includes issue number
# ---------------------------------------------------------------------------

class TestAC4WarningContent:
    def test_warning_names_issue_number_on_exception(self, tmp_path, capsys):
        """WARNING line includes the issue number for traceability."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=30)):
            _build_design_block(42, "owner/repo", tmp_path)

        captured = capsys.readouterr()
        assert "42" in captured.out, (
            "WARNING must include the issue number so the failure is traceable in logs"
        )

    def test_warning_names_issue_number_on_nonzero(self, tmp_path, capsys):
        """WARNING line includes the issue number when gh exits non-zero."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")

        with patch("subprocess.run", return_value=_make_fail_mock(returncode=1)):
            _build_design_block(42, "owner/repo", tmp_path)

        captured = capsys.readouterr()
        assert "42" in captured.out, (
            "WARNING must include the issue number when gh exits non-zero"
        )
