"""Tests for issue #301: Add GITHUB_ISSUE_TEST_REPO so tester uses throwaway repo."""
import importlib
import os
import sys
import subprocess
import unittest.mock as mock
from pathlib import Path

import pytest

# Ensure we can import dashboard modules
_DASHBOARD_DIR = Path(__file__).parent.parent
_REPO_ROOT = _DASHBOARD_DIR.parent.parent
sys.path.insert(0, str(_DASHBOARD_DIR))
sys.path.insert(0, str(_REPO_ROOT))


class _EnvPatch:
    """Context manager to temporarily set/unset env vars."""

    def __init__(self, **kwargs):
        self._vars = kwargs
        self._originals = {}

    def __enter__(self):
        for key, val in self._vars.items():
            self._originals[key] = os.environ.get(key)
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        return self

    def __exit__(self, *_):
        for key, orig in self._originals.items():
            if orig is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = orig


# ── AC: GITHUB_ISSUE_TEST_REPO is read by sprint_manager at tester-dispatch time ──

def test_dispatch_tester_injects_github_issue_test_repo_into_env():
    """AC: GITHUB_ISSUE_TEST_REPO is injected into tester subprocess environment."""
    import services.sprint_manager.sprint_manager as sm

    captured_envs = []

    def fake_popen(cmd, stdout, stderr, cwd, env):
        captured_envs.append(env.copy())

        class FakeProc:
            def wait(self):
                return 0

        return FakeProc()

    with _EnvPatch(
        GITHUB_ISSUE_TEST_REPO="testorg/issue-sandbox",
        COMMANDER_ALLOW_STUB_SUCCESS=None,
    ):
        with mock.patch("subprocess.Popen", side_effect=fake_popen):
            with mock.patch.object(sm, "_issue_log_path") as mock_log:
                mock_log.return_value = Path("/tmp/test-issue-301.log")
                Path("/tmp/test-issue-301.log").touch()
                with mock.patch.object(sm, "HangDetector") as MockHD:
                    hd = mock.MagicMock()
                    hd.killed = False
                    MockHD.return_value = hd
                    sm._dispatch_tester(
                        issue_num=999,
                        alert_modes=[],
                        repo_name="testorg/work-repo",
                    )

    assert captured_envs, "Popen was never called"
    env = captured_envs[0]
    assert env.get("GITHUB_ISSUE_TEST_REPO") == "testorg/issue-sandbox", (
        f"GITHUB_ISSUE_TEST_REPO not injected into tester env, got: {env.get('GITHUB_ISSUE_TEST_REPO')!r}"
    )


def test_dispatch_tester_prompt_includes_work_repo_and_test_repo_distinction():
    """AC: Tester prompt explicitly distinguishes work repo from issue-test repo."""
    import services.sprint_manager.sprint_manager as sm

    captured_cmds = []

    def fake_popen(cmd, stdout, stderr, cwd, env):
        captured_cmds.append(cmd[:])

        class FakeProc:
            def wait(self):
                return 0

        return FakeProc()

    with _EnvPatch(
        GITHUB_ISSUE_TEST_REPO="testorg/issue-sandbox",
        COMMANDER_ALLOW_STUB_SUCCESS=None,
    ):
        with mock.patch("subprocess.Popen", side_effect=fake_popen):
            with mock.patch.object(sm, "_issue_log_path") as mock_log:
                mock_log.return_value = Path("/tmp/test-issue-301b.log")
                Path("/tmp/test-issue-301b.log").touch()
                with mock.patch.object(sm, "HangDetector") as MockHD:
                    hd = mock.MagicMock()
                    hd.killed = False
                    MockHD.return_value = hd
                    sm._dispatch_tester(
                        issue_num=999,
                        alert_modes=[],
                        repo_name="testorg/work-repo",
                    )

    assert captured_cmds, "Popen was never called"
    # The prompt is the last element in the cmd list (after -p)
    prompt = captured_cmds[0][-1]
    assert "testorg/issue-sandbox" in prompt, (
        "Tester prompt must mention GITHUB_ISSUE_TEST_REPO value"
    )
    assert "testorg/work-repo" in prompt, (
        "Tester prompt must mention the work repo for distinction"
    )


def test_dispatch_tester_prompt_skip_note_when_no_test_repo():
    """AC: When GITHUB_ISSUE_TEST_REPO unset, prompt contains skip note."""
    import services.sprint_manager.sprint_manager as sm

    captured_cmds = []

    def fake_popen(cmd, stdout, stderr, cwd, env):
        captured_cmds.append(cmd[:])

        class FakeProc:
            def wait(self):
                return 0

        return FakeProc()

    with _EnvPatch(
        GITHUB_ISSUE_TEST_REPO=None,
        COMMANDER_ALLOW_STUB_SUCCESS=None,
    ):
        with mock.patch("subprocess.Popen", side_effect=fake_popen):
            with mock.patch.object(sm, "_issue_log_path") as mock_log:
                mock_log.return_value = Path("/tmp/test-issue-301c.log")
                Path("/tmp/test-issue-301c.log").touch()
                with mock.patch.object(sm, "HangDetector") as MockHD:
                    hd = mock.MagicMock()
                    hd.killed = False
                    MockHD.return_value = hd
                    sm._dispatch_tester(
                        issue_num=999,
                        alert_modes=[],
                        repo_name="testorg/work-repo",
                    )

    assert captured_cmds, "Popen was never called"
    prompt = captured_cmds[0][-1]
    assert "GITHUB_ISSUE_TEST_REPO not configured" in prompt, (
        "Prompt must include skip note when GITHUB_ISSUE_TEST_REPO is not set"
    )
    assert "skipped live issue/label verification" in prompt, (
        "Prompt must indicate live issue/label verification will be skipped"
    )


def test_dispatch_tester_no_test_repo_not_injected_into_env_when_unset():
    """AC: When GITHUB_ISSUE_TEST_REPO unset, env var is not injected (not empty string)."""
    import services.sprint_manager.sprint_manager as sm

    captured_envs = []

    def fake_popen(cmd, stdout, stderr, cwd, env):
        captured_envs.append(env.copy())

        class FakeProc:
            def wait(self):
                return 0

        return FakeProc()

    with _EnvPatch(
        GITHUB_ISSUE_TEST_REPO=None,
        COMMANDER_ALLOW_STUB_SUCCESS=None,
    ):
        with mock.patch("subprocess.Popen", side_effect=fake_popen):
            with mock.patch.object(sm, "_issue_log_path") as mock_log:
                mock_log.return_value = Path("/tmp/test-issue-301d.log")
                Path("/tmp/test-issue-301d.log").touch()
                with mock.patch.object(sm, "HangDetector") as MockHD:
                    hd = mock.MagicMock()
                    hd.killed = False
                    MockHD.return_value = hd
                    sm._dispatch_tester(
                        issue_num=999,
                        alert_modes=[],
                        repo_name="testorg/work-repo",
                    )

    assert captured_envs, "Popen was never called"
    env = captured_envs[0]
    # Should not be injected at all when unset
    assert "GITHUB_ISSUE_TEST_REPO" not in env or not env["GITHUB_ISSUE_TEST_REPO"], (
        "GITHUB_ISSUE_TEST_REPO should not be injected into env when unset"
    )


# ── AC: No hardcoded repo names in config.py ──────────────────────────────────

def test_config_test_github_repo_has_no_hardcoded_value_by_default():
    """AC: TEST_GITHUB_REPO has no hardcoded default — comes from env only."""
    with _EnvPatch(COMMANDER_TEST_REPO=None, GITHUB_ISSUE_TEST_REPO=None):
        import config
        importlib.reload(config)
        assert config.TEST_GITHUB_REPO == "", (
            "TEST_GITHUB_REPO must be empty when no env vars are set — "
            f"got {config.TEST_GITHUB_REPO!r}"
        )


def test_config_test_github_repo_reads_github_issue_test_repo():
    """AC: TEST_GITHUB_REPO is populated from GITHUB_ISSUE_TEST_REPO env var."""
    with _EnvPatch(COMMANDER_TEST_REPO=None, GITHUB_ISSUE_TEST_REPO="myorg/test-repo"):
        import config
        importlib.reload(config)
        assert config.TEST_GITHUB_REPO == "myorg/test-repo", (
            f"TEST_GITHUB_REPO should come from GITHUB_ISSUE_TEST_REPO, got {config.TEST_GITHUB_REPO!r}"
        )


def test_config_commander_test_repo_takes_precedence():
    """AC: COMMANDER_TEST_REPO takes precedence over GITHUB_ISSUE_TEST_REPO."""
    with _EnvPatch(
        COMMANDER_TEST_REPO="myorg/override-repo",
        GITHUB_ISSUE_TEST_REPO="myorg/test-repo",
    ):
        import config
        importlib.reload(config)
        assert config.TEST_GITHUB_REPO == "myorg/override-repo", (
            f"COMMANDER_TEST_REPO should take precedence, got {config.TEST_GITHUB_REPO!r}"
        )


# ── AC: Dashboard startup validates GITHUB_REPO accessibility ─────────────────

def test_validate_github_repos_fails_on_inaccessible_work_repo():
    """AC: Dashboard startup fails with exact error message when GITHUB_REPO is inaccessible."""
    # Import server module to test _validate_github_repos
    import server

    with mock.patch.object(server.github_client, "repo", return_value="myorg/missing-repo"):
        with mock.patch.object(server, "_check_repo_accessible", return_value=False):
            with pytest.raises(SystemExit) as exc_info:
                server._validate_github_repos()

    error_msg = str(exc_info.value)
    assert "myorg/missing-repo" in error_msg, (
        f"Error must name the bad GITHUB_REPO value, got: {error_msg!r}"
    )
    assert "does not exist or is inaccessible" in error_msg, (
        f"Error must use exact wording, got: {error_msg!r}"
    )


def test_validate_github_repos_warns_on_inaccessible_issue_test_repo(capsys):
    """AC: Dashboard startup warns (does not fail) when GITHUB_ISSUE_TEST_REPO is bad."""
    import server

    def fake_check(repo):
        return repo != "myorg/bad-test-repo"

    with _EnvPatch(GITHUB_ISSUE_TEST_REPO="myorg/bad-test-repo"):
        with mock.patch.object(server.github_client, "repo", return_value="myorg/good-work-repo"):
            with mock.patch.object(server, "_check_repo_accessible", side_effect=fake_check):
                # Should NOT raise SystemExit
                server._validate_github_repos()

    captured = capsys.readouterr()
    assert "myorg/bad-test-repo" in captured.out, (
        "Warning must name the bad GITHUB_ISSUE_TEST_REPO value"
    )
    assert "does not exist or is inaccessible" in captured.out, (
        "Warning must use exact wording"
    )
    assert "tester issue/label tests will be skipped" in captured.out, (
        "Warning must explain consequence"
    )


def test_validate_github_repos_no_warning_when_issue_test_repo_accessible(capsys):
    """AC: No warning when GITHUB_ISSUE_TEST_REPO is accessible."""
    import server

    with _EnvPatch(GITHUB_ISSUE_TEST_REPO="myorg/accessible-test-repo"):
        with mock.patch.object(server.github_client, "repo", return_value="myorg/work-repo"):
            with mock.patch.object(server, "_check_repo_accessible", return_value=True):
                server._validate_github_repos()

    captured = capsys.readouterr()
    assert "inaccessible" not in captured.out, (
        "Should not warn when GITHUB_ISSUE_TEST_REPO is accessible"
    )


def test_validate_github_repos_no_issue_test_repo_check_when_unset(capsys):
    """AC: No GITHUB_ISSUE_TEST_REPO warning when env var is not set."""
    import server

    with _EnvPatch(GITHUB_ISSUE_TEST_REPO=None):
        with mock.patch.object(server.github_client, "repo", return_value="myorg/work-repo"):
            with mock.patch.object(server, "_check_repo_accessible", return_value=True):
                server._validate_github_repos()

    captured = capsys.readouterr()
    assert "GITHUB_ISSUE_TEST_REPO" not in captured.out, (
        "Should not mention GITHUB_ISSUE_TEST_REPO when it is unset"
    )
