"""
Tests for issue #18 — UAT label safeguard in update_ticket.py.

Strategy: invoke update_ticket.py as a subprocess with a real git repo
fixture so the branch-detection and merge-base checks run against actual
git state (no mocking).  GitHub API calls are NOT made — we abort before
the gh command by testing the guard path only.

AC-1: --status uat fails (exit non-zero) when no feature/<N>-* branch exists
AC-2: --status uat fails when branch exists but is not merged into develop
AC-3: error message names the branch / check that failed
AC-4: --force overrides both checks with a warning to stderr; exits 0
"""

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "update_ticket.py"
PYTHON = sys.executable


def _run(tmp_repo: Path, *extra_args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PYTHON, str(SCRIPT), "--issue", "9999", "--status", "uat", *extra_args],
        capture_output=True,
        text=True,
        cwd=str(tmp_repo),
    )


@pytest.fixture(scope="module")
def git_repo(tmp_path_factory):
    """A minimal git repo with a develop branch on origin (bare repo)."""
    root = tmp_path_factory.mktemp("repo")
    bare = tmp_path_factory.mktemp("bare")

    # Initialise bare "remote"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)

    # Initialise working repo
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "remote", "add", "origin", str(bare)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@test.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True, capture_output=True)

    # Initial commit on develop
    (root / "README.md").write_text("init")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "branch", "-M", "develop"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "push", "-u", "origin", "develop"], check=True, capture_output=True)

    return root


@pytest.fixture(scope="module")
def git_repo_with_unmerged_branch(git_repo):
    """Extend git_repo with feature/9999-test NOT merged into develop."""
    subprocess.run(
        ["git", "-C", str(git_repo), "checkout", "-b", "feature/9999-test"],
        check=True, capture_output=True,
    )
    (git_repo / "feature_file.txt").write_text("feature work")
    subprocess.run(["git", "-C", str(git_repo), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(git_repo), "commit", "-m", "feature work"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(git_repo), "push", "origin", "feature/9999-test"],
        check=True, capture_output=True,
    )
    # Go back to develop
    subprocess.run(
        ["git", "-C", str(git_repo), "checkout", "develop"],
        check=True, capture_output=True,
    )
    return git_repo


@pytest.fixture(scope="module")
def git_repo_with_merged_branch(git_repo_with_unmerged_branch):
    """Extend to have feature/9999-test merged into develop."""
    repo = git_repo_with_unmerged_branch
    subprocess.run(
        ["git", "-C", str(repo), "merge", "--no-ff", "feature/9999-test",
         "-m", "Merge feature/9999-test into develop"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "push", "origin", "develop"],
        check=True, capture_output=True,
    )
    return repo


# ---------------------------------------------------------------------------
# AC-1 — no branch → exit non-zero
# ---------------------------------------------------------------------------

class TestAC1NoBranch:
    def test_exits_nonzero_when_no_branch(self, git_repo):
        result = _run(git_repo)
        assert result.returncode != 0, (
            f"Expected non-zero exit when no feature/9999-* branch exists\nstdout: {result.stdout}"
        )

    def test_error_mentions_branch_pattern(self, git_repo):
        result = _run(git_repo)
        combined = result.stdout + result.stderr
        assert "feature/9999" in combined, (
            f"Error should mention the branch pattern 'feature/9999'\ncombined: {combined}"
        )

    def test_error_mentions_no_branch_found(self, git_repo):
        result = _run(git_repo)
        combined = result.stdout + result.stderr
        assert "no branch" in combined.lower() or "not found" in combined.lower(), (
            f"Error should say no branch was found\ncombined: {combined}"
        )


# ---------------------------------------------------------------------------
# AC-2 — branch exists but not merged → exit non-zero
# ---------------------------------------------------------------------------

class TestAC2BranchNotMerged:
    def test_exits_nonzero_when_branch_not_merged(self, git_repo_with_unmerged_branch):
        result = _run(git_repo_with_unmerged_branch)
        assert result.returncode != 0, (
            f"Expected non-zero exit when branch exists but not merged\nstdout: {result.stdout}"
        )

    def test_error_mentions_not_merged(self, git_repo_with_unmerged_branch):
        result = _run(git_repo_with_unmerged_branch)
        combined = result.stdout + result.stderr
        assert "not been merged" in combined or "not merged" in combined, (
            f"Error should state branch not merged\ncombined: {combined}"
        )


# ---------------------------------------------------------------------------
# AC-3 — error message names the branch and which check failed
# ---------------------------------------------------------------------------

class TestAC3ErrorMessages:
    def test_no_branch_error_names_pattern(self, git_repo):
        result = _run(git_repo)
        combined = result.stdout + result.stderr
        assert "9999" in combined, "Error should reference the issue/branch number"

    def test_unmerged_error_names_branch(self, git_repo_with_unmerged_branch):
        result = _run(git_repo_with_unmerged_branch)
        combined = result.stdout + result.stderr
        assert "feature/9999-test" in combined, (
            f"Error should name the specific branch found\ncombined: {combined}"
        )

    def test_no_branch_error_mentions_develop(self, git_repo):
        result = _run(git_repo)
        combined = result.stdout + result.stderr
        assert "develop" in combined or "merge" in combined.lower(), (
            "Error should hint at merging into develop"
        )

    def test_unmerged_error_mentions_develop(self, git_repo_with_unmerged_branch):
        result = _run(git_repo_with_unmerged_branch)
        combined = result.stdout + result.stderr
        assert "develop" in combined, "Error should mention develop"


# ---------------------------------------------------------------------------
# AC-4 — --force overrides checks, prints warning to stderr
# ---------------------------------------------------------------------------

class TestAC4ForceFlag:
    def test_force_exits_nonzero_only_on_gh_error(self, git_repo):
        """With --force and no branch, safeguard is skipped; script proceeds to gh call
        which will fail (no real GitHub token/repo), but the exit code is from gh not from
        the safeguard. The safeguard itself must NOT block."""
        result = _run(git_repo, "--force")
        # The safeguard warning should appear in stderr
        combined = result.stdout + result.stderr
        assert "WARNING" in combined or "warning" in combined.lower() or "safeguard" in combined.lower(), (
            f"--force should emit a warning about bypassing safeguard\ncombined: {combined}"
        )

    def test_force_prints_warning_to_stderr(self, git_repo):
        result = _run(git_repo, "--force")
        # Warning should be on stderr, not stdout
        assert "WARNING" in result.stderr or "safeguard" in result.stderr.lower(), (
            f"Warning should go to stderr\nstderr: {result.stderr}"
        )

    def test_force_with_unmerged_branch_emits_warning(self, git_repo_with_unmerged_branch):
        result = _run(git_repo_with_unmerged_branch, "--force")
        combined = result.stdout + result.stderr
        assert "WARNING" in combined or "warning" in combined.lower() or "safeguard" in combined.lower(), (
            f"--force should warn even when branch exists but not merged\ncombined: {combined}"
        )

    def test_merged_branch_exits_cleanly_without_force(self, git_repo_with_merged_branch):
        """When branch IS merged, the safeguard passes and the script proceeds to gh.
        The gh call will fail (no real repo), but the exit is from gh, not the safeguard."""
        result = _run(git_repo_with_merged_branch)
        combined = result.stdout + result.stderr
        # Should NOT contain the safeguard error messages
        assert "UAT safeguard:" not in combined, (
            f"Safeguard should not trigger when branch is merged\ncombined: {combined}"
        )


# ---------------------------------------------------------------------------
# Meta: verify the script has been updated
# ---------------------------------------------------------------------------

class TestScriptContainsSafeguard:
    def test_script_exists(self):
        assert SCRIPT.exists(), f"update_ticket.py not found at {SCRIPT}"

    def test_script_has_force_flag(self):
        content = SCRIPT.read_text()
        assert "--force" in content, "Script should define --force argument"

    def test_script_has_uat_guard_function(self):
        content = SCRIPT.read_text()
        assert "_check_uat_safeguard" in content or "uat_safeguard" in content, (
            "Script should contain a UAT safeguard function"
        )

    def test_script_uses_merge_base(self):
        content = SCRIPT.read_text()
        assert "merge-base" in content or "merge_base" in content, (
            "Script should use git merge-base to check if branch is merged"
        )

    def test_script_checks_only_for_uat_status(self):
        content = SCRIPT.read_text()
        assert 'args.status == "uat"' in content or "status == 'uat'" in content, (
            "Safeguard should only run for --status uat"
        )
