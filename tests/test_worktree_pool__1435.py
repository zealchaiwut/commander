"""Tests for issue #1435: Worktree pool enqueues failed slots into the free pool (runs against UAT)"""
import sys
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.sprint_manager.worktree_pool import WorktreePool, _run


@pytest.fixture
def temp_pool_dir():
    """Create a temporary directory for worktree pool tests."""
    tmpdir = Path(tempfile.mkdtemp(prefix="pool_test_"))
    yield tmpdir
    if tmpdir.exists():
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def temp_repo_dir():
    """Create a temporary git repo for testing."""
    tmpdir = Path(tempfile.mkdtemp(prefix="repo_test_"))
    _run(["git", "init"], cwd=tmpdir)
    _run(["git", "config", "user.email", "test@example.com"], cwd=tmpdir)
    _run(["git", "config", "user.name", "Test User"], cwd=tmpdir)
    (tmpdir / "README.md").write_text("test repo")
    _run(["git", "add", "README.md"], cwd=tmpdir)
    _run(["git", "commit", "-m", "initial"], cwd=tmpdir)
    yield tmpdir
    if tmpdir.exists():
        shutil.rmtree(tmpdir, ignore_errors=True)


# --- Acceptance Criteria ---

def test_worktree_pool__create_slot_returns_boolean(temp_pool_dir, temp_repo_dir):
    # AC: `_create_slot()` returns a boolean (or equivalent success flag)
    pool = WorktreePool(temp_pool_dir, temp_repo_dir, "master", slots=1)
    wt_path = temp_pool_dir / "slot-0"
    result = pool._create_slot(wt_path)
    assert isinstance(result, bool), f"Expected _create_slot to return bool, got {type(result)}"


def test_worktree_pool__append_only_on_success(temp_pool_dir, temp_repo_dir):
    # AC: In `create()`, a slot's path is appended to `created` only when
    # `_create_slot()` returns True. Inspect the code to verify this gate.
    pool = WorktreePool(temp_pool_dir, temp_repo_dir, "master", slots=1)

    # Mock _create_slot to return False explicitly
    with patch.object(pool, '_create_slot', return_value=False):
        pool.create()

    # With failed _create_slot (returns False), pool should be empty
    assert len(pool._free) == 0, f"Expected empty pool after _create_slot returns False, got {len(pool._free)}"


def test_worktree_pool__git_worktree_failure_not_in_pool(temp_pool_dir, temp_repo_dir):
    # AC: When `git worktree add` fails, the slot is NOT in the free pool.
    pool = WorktreePool(temp_pool_dir, temp_repo_dir, "master", slots=1)

    with patch('services.sprint_manager.worktree_pool._run') as mock_run:
        def run_side_effect(args, **kwargs):
            if args[0:3] == ["git", "worktree", "add"]:
                return False, "", "invalid branch"
            return True, "", ""

        mock_run.side_effect = run_side_effect
        pool.create()

    assert len(pool._free) == 0, f"Expected 0 free slots after git failure"
    assert (temp_pool_dir / "slot-0") not in pool._free


def test_worktree_pool__pip_failure_not_in_pool(temp_pool_dir, temp_repo_dir):
    # AC: When `pip install` fails, the slot is NOT in the free pool.
    pool = WorktreePool(temp_pool_dir, temp_repo_dir, "master", slots=1)
    req_file = temp_pool_dir / "requirements.txt"
    req_file.write_text("nonexistent-package==999.0.0")
    pool.requirements_file = req_file

    with patch('services.sprint_manager.worktree_pool._run') as mock_run:
        def run_side_effect(args, **kwargs):
            if args[0:3] == ["git", "worktree", "add"]:
                slot_path = Path(args[2])
                slot_path.mkdir(parents=True, exist_ok=True)
                (slot_path / ".git").mkdir(exist_ok=True)
                return True, "", ""
            elif args[0:4] == [sys.executable, "-m", "venv"]:
                venv_path = Path(args[3])
                venv_path.mkdir(parents=True, exist_ok=True)
                return True, "", ""
            elif "pip" in str(args[0]):
                return False, "", "not found"
            else:
                return True, "", ""

        mock_run.side_effect = run_side_effect
        pool.create()

    assert len(pool._free) == 0, f"Expected 0 free slots after pip failure"


def test_worktree_pool__mixed_success_and_failure(temp_pool_dir, temp_repo_dir):
    # AC: With one failing and one succeeding slot, only the successful one is in pool.
    pool = WorktreePool(temp_pool_dir, temp_repo_dir, "master", slots=2)

    with patch('services.sprint_manager.worktree_pool._run') as mock_run:
        slot_count = [0]

        def run_side_effect(args, **kwargs):
            if args[0:3] == ["git", "worktree", "add"]:
                slot_count[0] += 1
                if slot_count[0] == 1:
                    return False, "", "failed"
                else:
                    slot_path = Path(args[2])
                    slot_path.mkdir(parents=True, exist_ok=True)
                    (slot_path / ".git").mkdir(exist_ok=True)
                    return True, "", ""
            elif args[0:4] == [sys.executable, "-m", "venv"]:
                venv_path = Path(args[3])
                venv_path.mkdir(parents=True, exist_ok=True)
                return True, "", ""
            else:
                return True, "", ""

        mock_run.side_effect = run_side_effect
        pool.create()

    assert len(pool._free) == 1, f"Expected 1 free slot"
    assert (temp_pool_dir / "slot-1") in pool._free
    assert (temp_pool_dir / "slot-0") not in pool._free


def test_worktree_pool__acquire_returns_existing_path(temp_pool_dir, temp_repo_dir):
    # AC: `acquire()` never returns a non-existent path.
    pool = WorktreePool(temp_pool_dir, temp_repo_dir, "master", slots=1)
    pool.create()

    if pool._free:
        wt = pool.acquire()
        assert wt.is_dir(), f"acquire() returned non-existent path: {wt}"
        pool.release(wt)


def test_worktree_pool__create_slot_returns_success(temp_pool_dir, temp_repo_dir):
    # AC: Code review check — `_create_slot()` has explicit return True/False
    import inspect
    from services.sprint_manager.worktree_pool import WorktreePool

    source = inspect.getsource(WorktreePool._create_slot)
    # Verify _create_slot has explicit returns (not just reaching end)
    assert "return True" in source or "return False" in source, \
        "_create_slot should have explicit boolean returns"
