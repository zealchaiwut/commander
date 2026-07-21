"""Gate coder-no-test-edits: new test files created by the coder pass the gate.

The TDD coder prompt requires writing a new pytest test per acceptance
criterion, so the gate must only block *pre-existing* grading tests
(present on base_branch), not files the coder itself created.

Behavioral coverage (real git repos via tmp_path, plus mocked-boundary cases):
  - new tests/ file absent on base branch → gate passes
  - pre-existing tests/ file modified → gate fails, file named
  - mixed diff: new file allowed, modified pre-existing file blocked
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))

from sprint_manager import _gate_coder_no_test_edits  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────

def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=cwd, check=True,
        capture_output=True, text=True,
    )


def _make_repo(tmp_path: Path) -> Path:
    """Init a repo with develop containing one grading test, plus a feature branch."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "develop")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_grading__1.py").write_text("def test_ac1(): pass\n")
    (repo / "app.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")
    _git(repo, "checkout", "-b", "feature/2-work")
    return repo


def _commit_all(repo: Path, msg: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", msg)


# ── new coder-created test file passes ────────────────────────────────────────

class TestNewTestFileAllowed:
    def test_new_test_file_passes(self, tmp_path):
        repo = _make_repo(tmp_path)
        (repo / "tests" / "test_feature__2.py").write_text("def test_new(): pass\n")
        _commit_all(repo, "coder adds own TDD test")
        with patch("sprint_manager._revert_to_sit") as revert:
            result = _gate_coder_no_test_edits(2, repo, skip=False)
        assert result.passed is True
        revert.assert_not_called()

    def test_new_test_plus_source_change_passes(self, tmp_path):
        repo = _make_repo(tmp_path)
        (repo / "tests" / "test_feature__2.py").write_text("def test_new(): pass\n")
        (repo / "app.py").write_text("x = 2\n")
        _commit_all(repo, "coder implements with TDD test")
        with patch("sprint_manager._revert_to_sit"):
            result = _gate_coder_no_test_edits(2, repo, skip=False)
        assert result.passed is True


# ── pre-existing grading test still blocked ───────────────────────────────────

class TestPreExistingStillBlocked:
    def test_modified_grading_test_fails(self, tmp_path):
        repo = _make_repo(tmp_path)
        (repo / "tests" / "test_grading__1.py").write_text("def test_ac1(): assert 0\n")
        _commit_all(repo, "coder tampers with grading test")
        with patch("sprint_manager._revert_to_sit") as revert:
            result = _gate_coder_no_test_edits(2, repo, skip=False)
        assert result.passed is False
        assert "tests/test_grading__1.py" in (result.output or "")
        revert.assert_called_once()

    def test_mixed_diff_blocks_only_pre_existing(self, tmp_path):
        repo = _make_repo(tmp_path)
        (repo / "tests" / "test_grading__1.py").write_text("def test_ac1(): assert 0\n")
        (repo / "tests" / "test_feature__2.py").write_text("def test_new(): pass\n")
        _commit_all(repo, "coder adds own test and tampers with grading test")
        with patch("sprint_manager._revert_to_sit"):
            result = _gate_coder_no_test_edits(2, repo, skip=False)
        assert result.passed is False
        assert "tests/test_grading__1.py" in (result.output or "")
        assert "tests/test_feature__2.py" not in (result.output or "")
