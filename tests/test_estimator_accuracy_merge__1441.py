"""Tests for issue #1441: Estimator accuracy miscounts files on ff/squash merges.

Tests verify that finish_feature.py correctly handles merge vs fast-forward/squash
commits when computing estimator file-prediction accuracy.
"""
import subprocess
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.finish_feature import _is_true_merge_commit, _changed_files_for_merge


def _run_git(*cmd, cwd) -> str:
    """Run git command, raise on error."""
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=True)
    return r.stdout.strip()


def _setup_test_repo(tmp_path: Path) -> Path:
    """Create a test git repo with branches for testing merge detection."""
    repo = tmp_path / "test_repo"
    repo.mkdir()

    _run_git("git", "init", "-b", "main", cwd=repo)
    _run_git("git", "config", "user.name", "Test User", cwd=repo)
    _run_git("git", "config", "user.email", "test@example.com", cwd=repo)

    # Create initial commit on main
    (repo / "initial.txt").write_text("initial")
    _run_git("git", "add", "initial.txt", cwd=repo)
    _run_git("git", "commit", "-m", "Initial commit", cwd=repo)

    # Create develop branch and switch to it
    _run_git("git", "checkout", "-b", "develop", cwd=repo)

    return repo


def _commit_file(repo: Path, filename: str, content: str, message: str) -> str:
    """Commit a file and return the commit SHA."""
    (repo / filename).write_text(content)
    _run_git("git", "add", filename, cwd=repo)
    _run_git("git", "commit", "-m", message, cwd=repo)
    return _run_git("git", "rev-parse", "HEAD", cwd=repo)


# ===== AC1: Detect true merge vs ff/squash =====

def test_estimator_accuracy_detects_true_merge_commit(tmp_path):
    """AC1: Detect whether merge_sha is a true merge commit by checking for second parent."""
    repo = _setup_test_repo(tmp_path)

    # Create feature branch
    _run_git("git", "checkout", "-b", "feature/test", cwd=repo)
    _commit_file(repo, "feature1.txt", "content1", "Feature commit 1")
    _commit_file(repo, "feature2.txt", "content2", "Feature commit 2")

    # Merge back to develop with --no-ff (creates true merge)
    _run_git("git", "checkout", "develop", cwd=repo)
    _run_git("git", "merge", "--no-ff", "feature/test", "-m", "Merge feature", cwd=repo)

    merge_sha = _run_git("git", "rev-parse", "HEAD", cwd=repo)

    # Verify it's detected as true merge
    assert _is_true_merge_commit(merge_sha, run=lambda *cmd: (True, _run_git(*cmd, cwd=repo))), \
        "Should detect true merge commit with 2 parents"


def test_estimator_accuracy_detects_ff_commit(tmp_path):
    """AC1: Detect fast-forward commit as single-parent."""
    repo = _setup_test_repo(tmp_path)

    # Create and switch to feature branch
    _run_git("git", "checkout", "-b", "feature/ff", cwd=repo)
    _commit_file(repo, "feature1.txt", "content1", "Feature commit")

    # Fast-forward merge (no --no-ff)
    _run_git("git", "checkout", "develop", cwd=repo)
    _run_git("git", "merge", "feature/ff", cwd=repo)

    merge_sha = _run_git("git", "rev-parse", "HEAD", cwd=repo)

    # Verify it's detected as single-parent (ff)
    assert not _is_true_merge_commit(merge_sha, run=lambda *cmd: (True, _run_git(*cmd, cwd=repo))), \
        "Should detect ff commit as single-parent"


def test_estimator_accuracy_detects_squash_commit(tmp_path):
    """AC1: Detect squash commit as single-parent."""
    repo = _setup_test_repo(tmp_path)

    # Create feature branch with multiple commits
    _run_git("git", "checkout", "-b", "feature/squash", cwd=repo)
    _commit_file(repo, "feature1.txt", "content1", "Feature commit 1")
    _commit_file(repo, "feature2.txt", "content2", "Feature commit 2")

    # Squash merge
    _run_git("git", "checkout", "develop", cwd=repo)
    _run_git("git", "merge", "--squash", "feature/squash", cwd=repo)
    _run_git("git", "commit", "-m", "Squash merge", cwd=repo)

    merge_sha = _run_git("git", "rev-parse", "HEAD", cwd=repo)

    # Verify it's detected as single-parent (squash)
    assert not _is_true_merge_commit(merge_sha, run=lambda *cmd: (True, _run_git(*cmd, cwd=repo))), \
        "Should detect squash commit as single-parent"


# ===== AC2: True merge returns all changed files =====

def test_estimator_accuracy_true_merge_returns_all_files(tmp_path):
    """AC2: True merge commit returns all files changed in the merged feature branch."""
    repo = _setup_test_repo(tmp_path)

    # Create feature branch with 2 files
    _run_git("git", "checkout", "-b", "feature/test", cwd=repo)
    _commit_file(repo, "feature1.txt", "content1", "Feature commit 1")
    _commit_file(repo, "feature2.txt", "content2", "Feature commit 2")

    # Merge with --no-ff (true merge)
    _run_git("git", "checkout", "develop", cwd=repo)
    _run_git("git", "merge", "--no-ff", "feature/test", "-m", "Merge feature", cwd=repo)

    merge_sha = _run_git("git", "rev-parse", "HEAD", cwd=repo)

    # Get changed files using the actual implementation
    def run(*cmd):
        return (True, _run_git(*cmd, cwd=repo))

    files = _changed_files_for_merge(merge_sha, "develop", run=run)

    # True merge should show both files that were added in the feature branch
    assert set(files) == {"feature1.txt", "feature2.txt"}, \
        f"True merge should return all files. Got: {files}"


# ===== AC3/AC4: ff/squash falls back to merge-base diff =====

def test_estimator_accuracy_ff_returns_all_files(tmp_path):
    """AC3/AC4: ff commit returns all files changed across entire feature branch."""
    repo = _setup_test_repo(tmp_path)

    # Create feature branch with 3 commits
    _run_git("git", "checkout", "-b", "feature/ff", cwd=repo)
    _commit_file(repo, "feature1.txt", "content1", "Feature commit 1")
    _commit_file(repo, "feature2.txt", "content2", "Feature commit 2")
    _commit_file(repo, "feature3.txt", "content3", "Feature commit 3")

    # Fast-forward merge
    _run_git("git", "checkout", "develop", cwd=repo)
    _run_git("git", "merge", "feature/ff", cwd=repo)

    merge_sha = _run_git("git", "rev-parse", "HEAD", cwd=repo)

    # Get changed files using the actual implementation
    def run(*cmd):
        return (True, _run_git(*cmd, cwd=repo))

    files = _changed_files_for_merge(merge_sha, "develop", run=run)

    # ff should show all 3 files from the feature branch, not just the tip
    assert set(files) == {"feature1.txt", "feature2.txt", "feature3.txt"}, \
        f"ff merge should show all files. Got: {files}"


def test_estimator_accuracy_squash_returns_all_files(tmp_path):
    """AC3/AC4: squash commit returns all files changed across entire feature branch."""
    repo = _setup_test_repo(tmp_path)

    # Create feature branch with 3 commits
    _run_git("git", "checkout", "-b", "feature/squash", cwd=repo)
    _commit_file(repo, "feature1.txt", "content1", "Feature commit 1")
    _commit_file(repo, "feature2.txt", "content2", "Feature commit 2")
    _commit_file(repo, "feature3.txt", "content3", "Feature commit 3")

    # Squash merge
    _run_git("git", "checkout", "develop", cwd=repo)
    _run_git("git", "merge", "--squash", "feature/squash", cwd=repo)
    _run_git("git", "commit", "-m", "Squash merge", cwd=repo)

    merge_sha = _run_git("git", "rev-parse", "HEAD", cwd=repo)

    # Get changed files using the actual implementation
    def run(*cmd):
        return (True, _run_git(*cmd, cwd=repo))

    files = _changed_files_for_merge(merge_sha, "develop", run=run)

    # squash should show all 3 files from the feature branch, not just the tip
    assert set(files) == {"feature1.txt", "feature2.txt", "feature3.txt"}, \
        f"squash merge should show all files. Got: {files}"


# ===== AC5: Verify metrics are not skewed for ff/squash =====

def test_estimator_accuracy_metrics_reflect_full_branch(tmp_path):
    """AC5: Accuracy metrics correctly reflect full branch, not just tip commit."""
    from services.sprint_manager.estimate_accuracy import compute_metrics

    # Branch changed 3 files across multiple commits
    predicted = ["file1.txt", "file2.txt", "file3.txt"]
    actual_full = ["file1.txt", "file2.txt", "file3.txt"]
    precision, recall = compute_metrics(predicted, actual_full)

    # With full file list, metrics should be accurate
    assert precision == 1.0, "Perfect prediction has precision = 1.0"
    assert recall == 1.0, "Perfect prediction has recall = 1.0"
    # Key: actual includes ALL 3 files from branch, not just tip
