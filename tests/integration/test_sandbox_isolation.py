"""Integration tests verifying GitHub operations in test mode target the sandbox repo.

Sets COMMANDER_TEST_MODE=1 and asserts that get_repo_for_operation() returns the value
of GITHUB_ISSUE_TEST_REPO (or COMMANDER_TEST_REPO), not the work repo.
"""
import os
import sys
from pathlib import Path

import pytest

_DASHBOARD_DIR = Path(__file__).parent.parent.parent / "apps" / "dashboard"
sys.path.insert(0, str(_DASHBOARD_DIR))

import github_client  # noqa: E402


class _EnvPatch:
    """Context manager to temporarily set/unset env vars."""

    def __init__(self, **kwargs: str | None):
        self._vars = kwargs
        self._originals: dict[str, str | None] = {}

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


def test_get_repo_for_operation_test_mode_uses_github_issue_test_repo():
    """COMMANDER_TEST_MODE=1 with GITHUB_ISSUE_TEST_REPO set must redirect to that repo."""
    sandbox = "myorg/sandbox-repo"
    with _EnvPatch(
        COMMANDER_TEST_MODE="1",
        COMMANDER_TEST_REPO=None,
        GITHUB_ISSUE_TEST_REPO=sandbox,
    ):
        # Reload TEST_GITHUB_REPO from env since it's module-level in config
        import importlib
        import config
        importlib.reload(config)
        github_client.TEST_GITHUB_REPO = config.TEST_GITHUB_REPO  # type: ignore[attr-defined]
        result = github_client.get_repo_for_operation()
    assert result == sandbox, (
        f"Expected sandbox repo in test mode, got: {result!r}"
    )


def test_get_repo_for_operation_production_repo_unchanged_outside_test_mode():
    """Outside test mode, any repo passes through unchanged."""
    with _EnvPatch(COMMANDER_TEST_MODE=None):
        result = github_client.get_repo_for_operation("myorg/some-repo")
    assert result == "myorg/some-repo", (
        f"Non-test-mode repo should be unchanged, got: {result!r}"
    )


def test_get_repo_for_operation_other_repo_unchanged():
    """Non-production repos outside test mode must pass through unchanged."""
    with _EnvPatch(COMMANDER_TEST_MODE=None):
        result = github_client.get_repo_for_operation("zealchaiwut/some-other-project")
    assert result == "zealchaiwut/some-other-project", (
        f"Non-production repo should be unchanged, got: {result!r}"
    )


def test_custom_test_repo_override():
    """COMMANDER_TEST_REPO env var must override the default sandbox target."""
    with _EnvPatch(COMMANDER_TEST_MODE="1", COMMANDER_TEST_REPO="myorg/my-custom-sandbox"):
        result = github_client.get_repo_for_operation()
    assert result == "myorg/my-custom-sandbox", (
        f"COMMANDER_TEST_REPO override not respected, got: {result!r}"
    )


def test_no_work_repo_in_test_mode():
    """Verify the real work repo is never returned when test mode is active and a sandbox is configured."""
    work_repo = "myorg/the-real-repo"
    sandbox = "myorg/sandbox-repo"
    with _EnvPatch(COMMANDER_TEST_MODE="1", COMMANDER_TEST_REPO=sandbox):
        result = github_client.get_repo_for_operation(work_repo)
    assert result != work_repo, (
        "Work repo must never be targeted in test mode when a sandbox is configured"
    )
    assert result == sandbox, f"Unexpected repo in test mode: {result!r}"
