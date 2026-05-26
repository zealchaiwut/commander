"""Integration tests verifying GitHub operations in test mode target the sandbox repo.

Sets COMMANDER_TEST_MODE=1 and asserts that get_repo_for_operation() returns a URL
containing 'commander-issue-test', not the real 'zealchaiwut/commander' repo.
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


def test_get_repo_for_operation_test_mode_returns_sandbox():
    """COMMANDER_TEST_MODE=1 must redirect all operations to the sandbox repo."""
    with _EnvPatch(COMMANDER_TEST_MODE="1", COMMANDER_TEST_REPO=None):
        result = github_client.get_repo_for_operation()
    assert "commander-issue-test" in result, (
        f"Expected sandbox repo in test mode, got: {result!r}"
    )
    assert result != "zealchaiwut/commander", (
        "Test mode must not target the production repo"
    )


def test_get_repo_for_operation_production_repo_redirected():
    """Passing the production repo directly must redirect to sandbox (self-referential detection)."""
    with _EnvPatch(COMMANDER_TEST_MODE=None):
        result = github_client.get_repo_for_operation("zealchaiwut/commander")
    assert "commander-issue-test" in result, (
        f"Targeting production repo should redirect to sandbox, got: {result!r}"
    )


def test_get_repo_for_operation_other_repo_unchanged():
    """Non-production repos outside test mode must pass through unchanged."""
    with _EnvPatch(COMMANDER_TEST_MODE=None):
        result = github_client.get_repo_for_operation("zealchaiwut/some-other-project")
    assert result == "zealchaiwut/some-other-project", (
        f"Non-production repo should be unchanged, got: {result!r}"
    )


def test_r_function_uses_sandbox_in_test_mode():
    """The internal _r() resolver must also apply sandbox redirection."""
    with _EnvPatch(COMMANDER_TEST_MODE="1"):
        result = github_client._r("zealchaiwut/commander")
    assert "commander-issue-test" in result, (
        f"_r() should redirect to sandbox in test mode, got: {result!r}"
    )


def test_custom_test_repo_override():
    """COMMANDER_TEST_REPO env var must override the default sandbox target."""
    with _EnvPatch(COMMANDER_TEST_MODE="1", COMMANDER_TEST_REPO="myorg/my-custom-sandbox"):
        result = github_client.get_repo_for_operation()
    assert result == "myorg/my-custom-sandbox", (
        f"COMMANDER_TEST_REPO override not respected, got: {result!r}"
    )


def test_no_production_repo_in_test_mode():
    """Verify the real commander repo is never returned when test mode is active."""
    with _EnvPatch(COMMANDER_TEST_MODE="1"):
        result = github_client.get_repo_for_operation("zealchaiwut/commander")
    assert result != "zealchaiwut/commander", (
        "Production repo must never be targeted in test mode"
    )
    assert "commander-issue-test" in result or "sandbox" in result.lower() or (
        os.environ.get("COMMANDER_TEST_REPO", "") in result
    ), f"Unexpected repo in test mode: {result!r}"
