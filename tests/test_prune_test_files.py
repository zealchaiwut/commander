"""Tests for scripts/prune_test_files.py — keep N newest tests/test_*.py by git log."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"

sys.path.insert(0, str(SCRIPTS_DIR))

import prune_test_files as ptf  # noqa: E402


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def mini_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    tests = repo / "tests"
    tests.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "tester")

    for i in range(5):
        path = tests / f"test_{i:03d}.py"
        path.write_text(f"# test {i}\n", encoding="utf-8")
        _git(repo, "add", str(path.relative_to(repo)))
        _git(repo, "commit", "-m", f"add test {i}")

    return repo


def test_rank_keeps_newest_by_git_history(mini_repo, monkeypatch):
    plan = ptf.rank_test_files(mini_repo, keep=3)
    assert plan["total_count"] == 5
    assert plan["kept_count"] == 3
    assert plan["remove_count"] == 2
    assert plan["kept"] == [
        "tests/test_004.py",
        "tests/test_003.py",
        "tests/test_002.py",
    ]
    assert plan["remove"] == ["tests/test_001.py", "tests/test_000.py"]


def test_dry_run_does_not_delete(mini_repo):
    result = ptf.run_prune(mini_repo, keep=2, dry_run=True)
    assert result["dry_run"] is True
    assert result["remove_count"] == 3
    assert all((mini_repo / rel).exists() for rel in result["remove"])


def test_apply_deletes_old_files(mini_repo):
    result = ptf.run_prune(mini_repo, keep=2, dry_run=False)
    assert result["dry_run"] is False
    assert len(result["deleted"]) == 3
    assert (mini_repo / "tests/test_004.py").exists()
    assert (mini_repo / "tests/test_003.py").exists()
    assert not (mini_repo / "tests/test_000.py").exists()


def test_empty_tests_dir(tmp_path):
    repo = tmp_path / "empty"
    repo.mkdir()
    (repo / "tests").mkdir()
    plan = ptf.rank_test_files(repo, keep=100)
    assert plan["total_count"] == 0
    assert plan["remove_count"] == 0
