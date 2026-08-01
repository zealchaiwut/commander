"""Tests for issue #2074: AC tests must not write to the production GitHub repo.

AC1: Identified offending test: tests/test_milestone_support__877.py — all write
     tests targeted TEST_REPO_SLUG='commander' which routes through the UAT server
     to zealchaiwut/commander.  Also test_877__uat__github_milestone_support.py's
     test_877__endpoints_exist_and_respond POSTed a real {"title":"test"} payload.

AC2: test_milestone_support__877.py write tests now resolve repo from
     GITHUB_ISSUE_TEST_REPO (skipping when it is not set) instead of using the
     hardcoded slug 'commander'.

AC3: conftest.py has a session-scoped _gh_no_prod_write_guard autouse fixture that
     intercepts httpx POST/PATCH/DELETE calls targeting api.github.com/repos/
     zealchaiwut/commander and raises AssertionError immediately, mirroring the
     git_no_mutation fixture pattern.

AC4: Stale test-fixture milestones cleaned up interactively (see ticket comment).

AC5: Audit of other production GitHub write patterns — see findings in ticket comment.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
TESTS_DIR = REPO_ROOT / "tests"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest-2074.db")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_test_module(name: str, env_overrides: dict | None = None):
    """Load a test file as a module with optional env overrides.

    Clears any prior import so env changes take effect.
    """
    module_path = TESTS_DIR / f"{name}.py"
    if name in sys.modules:
        del sys.modules[name]

    saved = {}
    for key, val in (env_overrides or {}).items():
        saved[key] = os.environ.get(key)
        if val is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = val

    try:
        spec = importlib.util.spec_from_file_location(name, module_path)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except RuntimeError:
            # UAT tests raise RuntimeError if UAT_BASE_URL is not set.  That is
            # expected and fine — we only care about the module-level constants.
            pass
        return mod
    finally:
        # Restore env
        for key, orig in saved.items():
            if orig is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = orig


# ── AC2: write tests use GITHUB_ISSUE_TEST_REPO, not hardcoded 'commander' ────

class TestAC2WriteTestsTargetTestRepo:
    def test_test_repo_slug_resolved_from_env_var(self):
        """AC2: With GITHUB_ISSUE_TEST_REPO set, TEST_REPO_SLUG is NOT 'commander'."""
        mod = _load_test_module(
            "test_milestone_support__877",
            env_overrides={
                "GITHUB_ISSUE_TEST_REPO": "owner/my-test-repo",
                "UAT_BASE_URL": "http://localhost:9999",
            },
        )
        assert hasattr(mod, "TEST_REPO_SLUG"), (
            "test_milestone_support__877 must expose TEST_REPO_SLUG"
        )
        assert mod.TEST_REPO_SLUG != "commander", (
            "test_milestone_support__877 must NOT hardcode TEST_REPO_SLUG='commander'; "
            "it must resolve it from GITHUB_ISSUE_TEST_REPO"
        )
        # The slug should be the repo name part of 'owner/my-test-repo'
        assert mod.TEST_REPO_SLUG == "my-test-repo", (
            f"Expected TEST_REPO_SLUG='my-test-repo' from env 'owner/my-test-repo', "
            f"got {mod.TEST_REPO_SLUG!r}"
        )

    def test_write_tests_skip_when_env_var_absent(self, monkeypatch):
        """AC2: Without GITHUB_ISSUE_TEST_REPO, write tests skip rather than target prod."""
        monkeypatch.delenv("GITHUB_ISSUE_TEST_REPO", raising=False)
        monkeypatch.setenv("UAT_BASE_URL", "http://localhost:9999")

        mod = _load_test_module(
            "test_milestone_support__877",
            env_overrides={
                "GITHUB_ISSUE_TEST_REPO": None,
                "UAT_BASE_URL": "http://localhost:9999",
            },
        )
        # Module must expose a skip-guard mechanism (empty WRITE_REPO or explicit skip)
        write_repo = getattr(mod, "_WRITE_REPO", None)
        assert not write_repo or write_repo.strip() == "", (
            "When GITHUB_ISSUE_TEST_REPO is absent, _WRITE_REPO must be empty so "
            "write tests skip — found a non-empty value: "
            f"{write_repo!r}.  Either the env var leaked or the guard is broken."
        )


# ── AC3: conftest guard intercepts httpx writes to production repo ────────────

class TestAC3ConftestGuard:
    def test_guard_factory_exists_in_conftest(self):
        """AC3: conftest.py exports the _make_gh_write_guard factory."""
        conftest_path = TESTS_DIR / "conftest.py"
        assert "_make_gh_write_guard" in conftest_path.read_text(), (
            "conftest.py must define _make_gh_write_guard (AC3, issue #2074)"
        )

    def test_guard_raises_for_prod_repo_post(self):
        """AC3: _make_gh_write_guard blocks httpx POST to zealchaiwut/commander."""
        # Import the factory from conftest (it's on sys.path as a test-time module)
        if "conftest" in sys.modules:
            conftest = sys.modules["conftest"]
        else:
            spec = importlib.util.spec_from_file_location(
                "conftest", TESTS_DIR / "conftest.py"
            )
            conftest = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(conftest)

        make_guard = conftest._make_gh_write_guard
        fake_real_post = MagicMock(return_value=MagicMock(status_code=201))
        guard = make_guard(fake_real_post, "POST")

        with pytest.raises(AssertionError, match="SAFETY ABORT"):
            guard(
                "https://api.github.com/repos/zealchaiwut/commander/milestones",
                headers={},
                json={"title": "oops"},
            )
        # The real function must NOT have been called
        fake_real_post.assert_not_called()

    def test_guard_passes_through_for_test_repo(self):
        """AC3: Guard does not interfere with legitimate test-repo writes."""
        if "conftest" in sys.modules:
            conftest = sys.modules["conftest"]
        else:
            spec = importlib.util.spec_from_file_location(
                "conftest", TESTS_DIR / "conftest.py"
            )
            conftest = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(conftest)

        make_guard = conftest._make_gh_write_guard
        fake_real_post = MagicMock(return_value=MagicMock(status_code=201))
        guard = make_guard(fake_real_post, "POST")

        # Call with a non-prod URL — must pass through
        guard(
            "https://api.github.com/repos/zealchaiwut/commander-issue-test/milestones",
            headers={},
            json={"title": "fine"},
        )
        fake_real_post.assert_called_once()

    def test_guard_blocks_patch_to_prod_repo(self):
        """AC3: Guard blocks httpx PATCH (edit) to zealchaiwut/commander."""
        if "conftest" in sys.modules:
            conftest = sys.modules["conftest"]
        else:
            spec = importlib.util.spec_from_file_location(
                "conftest", TESTS_DIR / "conftest.py"
            )
            conftest = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(conftest)

        make_guard = conftest._make_gh_write_guard
        guard = make_guard(MagicMock(), "PATCH")

        with pytest.raises(AssertionError, match="SAFETY ABORT"):
            guard(
                "https://api.github.com/repos/zealchaiwut/commander/milestones/42",
                json={"title": "evil edit"},
            )

    def test_guard_blocks_delete_to_prod_repo(self):
        """AC3: Guard blocks httpx DELETE (close) to zealchaiwut/commander."""
        if "conftest" in sys.modules:
            conftest = sys.modules["conftest"]
        else:
            spec = importlib.util.spec_from_file_location(
                "conftest", TESTS_DIR / "conftest.py"
            )
            conftest = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(conftest)

        make_guard = conftest._make_gh_write_guard
        guard = make_guard(MagicMock(), "DELETE")

        with pytest.raises(AssertionError, match="SAFETY ABORT"):
            guard(
                "https://api.github.com/repos/zealchaiwut/commander/milestones/7",
            )

    def test_guard_is_registered_as_autouse_session_fixture(self):
        """AC3: _gh_no_prod_write_guard must be session-scoped and autouse=True in conftest."""
        conftest_src = (TESTS_DIR / "conftest.py").read_text()
        # Verify the fixture is declared
        assert "_gh_no_prod_write_guard" in conftest_src, (
            "conftest.py must define _gh_no_prod_write_guard (AC3, issue #2074)"
        )
        # Verify it's autouse and session-scoped
        assert 'scope="session"' in conftest_src, (
            "_gh_no_prod_write_guard must be scope='session'"
        )
        assert "autouse=True" in conftest_src, (
            "_gh_no_prod_write_guard must be autouse=True"
        )
