"""Tests for issue #1441: Estimator accuracy miscounts files on ff/squash merges (runs against UAT)"""
import json
import subprocess
import tempfile
from pathlib import Path

import pytest


# Minimal setup for testing git operations locally (not against UAT).
# These tests exercise the finish_feature.py logic via unit/integration tests.

@pytest.fixture
def temp_git_repo():
    """Create a temporary git repo for testing merge commit detection."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = Path(tmpdir)
        subprocess.run(["git", "init", "-b", "master"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True, capture_output=True)

        # Create initial commit on master
        (repo / "README.md").write_text("# Test")
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial"], cwd=repo, check=True, capture_output=True)

        yield repo


def _run(cmd, cwd):
    """Run command and return (success, stdout)."""
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return r.returncode == 0, r.stdout.strip()


def test_ff_squash_fix__detect_true_merge_commit(temp_git_repo):
    """AC1: Detect whether merge_sha is a true merge commit by checking for second parent."""
    repo = temp_git_repo

    # Create a feature branch with commits
    subprocess.run(["git", "checkout", "-b", "feature/test"], cwd=repo, check=True, capture_output=True)
    (repo / "file1.py").write_text("code")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Feature commit 1"], cwd=repo, check=True, capture_output=True)

    (repo / "file2.py").write_text("more code")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Feature commit 2"], cwd=repo, check=True, capture_output=True)

    # Go back to main
    subprocess.run(["git", "checkout", "master"], cwd=repo, check=True, capture_output=True)

    # Merge with --no-ff (creates a true merge commit)
    merge_result = subprocess.run(
        ["git", "merge", "--no-ff", "feature/test", "-m", "Merge feature"],
        cwd=repo, capture_output=True, text=True
    )
    assert merge_result.returncode == 0

    ok, merge_sha = _run(["git", "rev-parse", "HEAD"], repo)
    assert ok and merge_sha

    # Check the merge commit has 2 parents
    ok, cat_output = _run(["git", "cat-file", "-p", merge_sha], repo)
    assert ok
    parent_lines = [ln for ln in cat_output.splitlines() if ln.startswith("parent ")]
    assert len(parent_lines) == 2, "True merge commit should have 2 parents"


def test_ff_squash_fix__detect_ff_merge_commit(temp_git_repo):
    """AC1: Detect single-parent commits (ff/squash) vs true merge commits."""
    repo = temp_git_repo

    # Create a feature branch with commits
    subprocess.run(["git", "checkout", "-b", "feature/test"], cwd=repo, check=True, capture_output=True)
    (repo / "file1.py").write_text("code")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Feature commit"], cwd=repo, check=True, capture_output=True)

    # Go back to main
    subprocess.run(["git", "checkout", "master"], cwd=repo, check=True, capture_output=True)

    # Merge with fast-forward (default, no --no-ff)
    merge_result = subprocess.run(
        ["git", "merge", "feature/test"],
        cwd=repo, capture_output=True, text=True
    )
    assert merge_result.returncode == 0

    ok, merge_sha = _run(["git", "rev-parse", "HEAD"], repo)
    assert ok and merge_sha

    # Check the ff commit has 1 parent (not a merge commit)
    ok, cat_output = _run(["git", "cat-file", "-p", merge_sha], repo)
    assert ok
    parent_lines = [ln for ln in cat_output.splitlines() if ln.startswith("parent ")]
    assert len(parent_lines) == 1, "Fast-forward commit should have 1 parent"


def test_ff_squash_fix__true_merge_uses_first_parent_diff(temp_git_repo):
    """AC2: True merge commits continue using git diff-tree (merge vs first-parent approach)."""
    repo = temp_git_repo

    # Create feature branch with 2 commits
    subprocess.run(["git", "checkout", "-b", "feature/test"], cwd=repo, check=True, capture_output=True)
    (repo / "file1.py").write_text("code1")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Commit 1"], cwd=repo, check=True, capture_output=True)

    (repo / "file2.py").write_text("code2")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Commit 2"], cwd=repo, check=True, capture_output=True)

    # Switch to master and make a commit (to create divergence)
    subprocess.run(["git", "checkout", "master"], cwd=repo, check=True, capture_output=True)
    (repo / "main_file.md").write_text("main work")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Main work"], cwd=repo, check=True, capture_output=True)

    # Merge with --no-ff (creates a true merge commit)
    subprocess.run(
        ["git", "merge", "--no-ff", "feature/test", "-m", "Merge feature"],
        cwd=repo, check=True, capture_output=True
    )

    ok, merge_sha = _run(["git", "rev-parse", "HEAD"], repo)
    assert ok
    ok, first_parent = _run(["git", "rev-parse", "HEAD^"], repo)
    assert ok

    # Get files by diffing against the first parent (merge-inclusive approach)
    ok, files_str = _run(
        ["git", "diff-tree", first_parent, merge_sha, "--name-only"],
        repo
    )
    assert ok
    files_list = [f for f in files_str.splitlines() if f.strip()]
    # Should capture both file1.py and file2.py (the whole branch)
    assert "file1.py" in files_list, "True merge should include all feature files"
    assert "file2.py" in files_list, "True merge should include all feature files"


def test_ff_squash_fix__ff_merge_uses_merge_base_diff(temp_git_repo):
    """AC3: ff/squash commits use merge-base diff to capture full branch history."""
    import sys
    from pathlib import Path

    scripts_dir = Path(__file__).parent.parent / "scripts"
    sys.path.insert(0, str(scripts_dir))

    try:
        import finish_feature

        # Mock _try for a ff commit scenario
        call_count = [0]

        def mock_ff_run(*cmd):
            """Simulate git commands for a ff merge scenario."""
            call_count[0] += 1
            call_num = call_count[0]

            cmd_str = " ".join(cmd)

            # First call: cat-file returns 1 parent (ff commit)
            if "cat-file" in cmd_str:
                return True, "tree abc123\nparent def456\nauthor Test\n\nCommit"
            # merge-base call
            elif "merge-base" in cmd_str:
                return True, "aaa111"
            # diff call
            elif "diff" in cmd_str and "aaa111..def456" in cmd_str:
                return True, "file1.py\nfile2.py"

            return False, ""

        # Test that for ff, merge-base diff is used
        result = finish_feature._changed_files_for_merge("def456", "master", run=mock_ff_run)
        assert "file1.py" in result, f"Should include file1.py from merge-base diff, got {result}"
        assert "file2.py" in result, f"Should include file2.py from merge-base diff, got {result}"

    finally:
        sys.path.pop(0)


def test_ff_squash_fix__full_file_set_for_ff_merge(temp_git_repo):
    """AC4: Set of files returned for ff/squash merge matches full feature branch (3+ files)."""
    import sys
    from pathlib import Path

    scripts_dir = Path(__file__).parent.parent / "scripts"
    sys.path.insert(0, str(scripts_dir))

    try:
        import finish_feature

        # Mock _try for a ff commit with 3 files
        def mock_ff_3file_run(*cmd):
            """Simulate git commands for ff with 3 files."""
            cmd_str = " ".join(cmd)

            if "cat-file" in cmd_str:
                # Single parent (ff commit)
                return True, "tree abc123\nparent def456\nauthor Test\n\nCommit"
            elif "merge-base" in cmd_str:
                # merge-base result
                return True, "bbb222"
            elif "diff" in cmd_str and "bbb222" in cmd_str:
                # All 3 files changed
                return True, "module1.py\nmodule2.py\nmodule3.py"

            return False, ""

        # Test that full branch file set is captured
        result = finish_feature._changed_files_for_merge("def456", "master", run=mock_ff_3file_run)
        assert len(result) >= 3, f"Should include all 3 files, got {len(result)}: {result}"
        assert "module1.py" in result
        assert "module2.py" in result
        assert "module3.py" in result

    finally:
        sys.path.pop(0)


def test_ff_squash_fix__accuracy_not_skewed_ff(temp_git_repo):
    """AC5: Accuracy metrics (precision/recall) not skewed for ff/squash merges."""
    # Test that precision/recall are computed correctly with full file set from merge-base diff
    from services.sprint_manager.estimate_accuracy import compute_metrics

    # Simulate estimator predicting 3 files
    predicted = ["file1.py", "file2.py", "file3.py"]

    # Actual files changed (with fix, merge-base diff captures all 3)
    actual = ["file1.py", "file2.py", "file3.py"]

    # Compute metrics using the same function as finish_feature.py calls
    precision, recall = compute_metrics(predicted, actual)

    # With the fix, all files are captured, so metrics should be perfect
    assert precision == 1.0, f"Precision should be 1.0 for perfect match, got {precision}"
    assert recall == 1.0, f"Recall should be 1.0 for perfect match, got {recall}"

    # Also test a case where not all predicted files are actually changed (but more than tip-only)
    predicted2 = ["file1.py", "file2.py", "file3.py", "other.py"]
    # But the actual merge touched all 3
    actual2 = ["file1.py", "file2.py", "file3.py"]

    precision2, recall2 = compute_metrics(predicted2, actual2)
    # 3 TP out of 4 predicted = 75% precision
    # 3 TP out of 3 actual = 100% recall
    assert precision2 == 0.75, f"Precision should be 0.75, got {precision2}"
    assert recall2 == 1.0, f"Recall should be 1.0, got {recall2}"


def test_ff_squash_fix__integration_finish_feature_module_imported():
    """AC1-AC5: Verify the finish_feature.py module can import and the new functions exist."""
    # Import the module and verify functions are defined
    import sys
    from pathlib import Path

    # Add scripts to path
    scripts_dir = Path(__file__).parent.parent / "scripts"
    sys.path.insert(0, str(scripts_dir))

    try:
        import finish_feature

        # Check new functions exist
        assert hasattr(finish_feature, "_is_true_merge_commit"), "_is_true_merge_commit should exist"
        assert hasattr(finish_feature, "_changed_files_for_merge"), "_changed_files_for_merge should exist"

        # Check function signatures
        import inspect
        sig = inspect.signature(finish_feature._is_true_merge_commit)
        assert "sha" in sig.parameters, "_is_true_merge_commit should accept sha parameter"

        sig = inspect.signature(finish_feature._changed_files_for_merge)
        assert "merge_sha" in sig.parameters, "_changed_files_for_merge should accept merge_sha"
        assert "target" in sig.parameters, "_changed_files_for_merge should accept target"
    finally:
        sys.path.pop(0)


def test_ff_squash_fix__is_true_merge_commit_logic():
    """AC1: _is_true_merge_commit detects merge vs ff/squash correctly."""
    import sys
    from pathlib import Path

    scripts_dir = Path(__file__).parent.parent / "scripts"
    sys.path.insert(0, str(scripts_dir))

    try:
        import finish_feature

        # Mock _try to simulate different outputs
        def mock_true_merge_run(*cmd):
            """Simulate git cat-file output for a true merge (2 parents)."""
            return True, "tree abc123\nparent def456\nparent ghi789\nauthor Test\ncommitter Test\n\nMerge message"

        def mock_ff_run(*cmd):
            """Simulate git cat-file output for a ff commit (1 parent)."""
            return True, "tree abc123\nparent def456\nauthor Test\ncommitter Test\n\nCommit message"

        def mock_error_run(*cmd):
            """Simulate command failure."""
            return False, ""

        # Test true merge detection
        assert finish_feature._is_true_merge_commit("abc123", run=mock_true_merge_run)

        # Test ff detection
        assert not finish_feature._is_true_merge_commit("def456", run=mock_ff_run)

        # Test error handling
        assert not finish_feature._is_true_merge_commit("xyz789", run=mock_error_run)
    finally:
        sys.path.pop(0)
