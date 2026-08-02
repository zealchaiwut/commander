"""Tests for issue #2141: broaden GitHub prod-write guard to cover httpx.Client and gh subprocess.

AC1: conftest._make_client_send_guard wraps httpx.Client.send and blocks write
     requests to api.github.com/repos/zealchaiwut/commander made via Client instances.
     The _gh_no_prod_write_guard session fixture patches httpx.Client.send.

AC2: conftest._is_gh_api_prod_write detects gh api write commands targeting the
     production repo; conftest._gh_no_prod_write_guard patches subprocess.run and
     subprocess.Popen to block those calls.

AC3: Existing module-level httpx.post/patch/delete guards are not regressed.
"""
from __future__ import annotations

import importlib
import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

REPO_ROOT = Path(__file__).parent.parent
TESTS_DIR = REPO_ROOT / "tests"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_conftest():
    if "conftest" in sys.modules:
        return sys.modules["conftest"]
    spec = importlib.util.spec_from_file_location("conftest", TESTS_DIR / "conftest.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_PROD_URL = "https://api.github.com/repos/zealchaiwut/commander/milestones"
_SAFE_URL = "https://api.github.com/repos/zealchaiwut/commander-issue-test/milestones"


# ── AC1: httpx.Client.send guard ─────────────────────────────────────────────

class TestAC1ClientSendGuardFactory:
    """Unit tests for _make_client_send_guard factory in conftest."""

    def test_factory_exists_in_conftest(self):
        """AC1: conftest.py must export _make_client_send_guard."""
        conftest_src = (TESTS_DIR / "conftest.py").read_text()
        assert "_make_client_send_guard" in conftest_src, (
            "conftest.py must define _make_client_send_guard (AC1, issue #2141)"
        )

    def test_blocks_post_to_prod_repo(self):
        """AC1: guard raises AssertionError on POST to zealchaiwut/commander."""
        conftest = _load_conftest()
        mock_original = MagicMock()
        guard = conftest._make_client_send_guard(mock_original)

        mock_self = MagicMock()
        request = httpx.Request("POST", _PROD_URL)

        with pytest.raises(AssertionError, match="SAFETY ABORT"):
            guard(mock_self, request)
        mock_original.assert_not_called()

    def test_blocks_patch_to_prod_repo(self):
        """AC1: guard raises AssertionError on PATCH to zealchaiwut/commander."""
        conftest = _load_conftest()
        guard = conftest._make_client_send_guard(MagicMock())
        mock_self = MagicMock()
        with pytest.raises(AssertionError, match="SAFETY ABORT"):
            guard(mock_self, httpx.Request("PATCH", _PROD_URL + "/1"))

    def test_blocks_delete_to_prod_repo(self):
        """AC1: guard raises AssertionError on DELETE to zealchaiwut/commander."""
        conftest = _load_conftest()
        guard = conftest._make_client_send_guard(MagicMock())
        mock_self = MagicMock()
        with pytest.raises(AssertionError, match="SAFETY ABORT"):
            guard(mock_self, httpx.Request("DELETE", _PROD_URL + "/2"))

    def test_allows_get_to_prod_repo(self):
        """AC1: guard does NOT block GET requests — reads are safe."""
        conftest = _load_conftest()
        mock_original = MagicMock(return_value=MagicMock())
        guard = conftest._make_client_send_guard(mock_original)
        mock_self = MagicMock()
        guard(mock_self, httpx.Request("GET", _PROD_URL))
        mock_original.assert_called_once()

    def test_allows_post_to_non_prod_repo(self):
        """AC1: guard passes through writes to non-production repos."""
        conftest = _load_conftest()
        mock_original = MagicMock(return_value=MagicMock())
        guard = conftest._make_client_send_guard(mock_original)
        mock_self = MagicMock()
        guard(mock_self, httpx.Request("POST", _SAFE_URL))
        mock_original.assert_called_once()


class TestAC1ClientSendBehavioral:
    """Behavioral tests: verify the autouse session fixture blocks httpx.Client writes."""

    def test_httpx_client_post_to_prod_raises(self):
        """AC1: httpx.Client().post(prod_url) raises SAFETY ABORT — guard is live."""
        with pytest.raises(AssertionError, match="SAFETY ABORT"):
            with httpx.Client() as client:
                client.post(_PROD_URL)

    def test_httpx_client_patch_to_prod_raises(self):
        """AC1: httpx.Client().patch(prod_url) raises SAFETY ABORT — guard is live."""
        with pytest.raises(AssertionError, match="SAFETY ABORT"):
            with httpx.Client() as client:
                client.patch(_PROD_URL + "/1", json={"title": "evil"})

    def test_httpx_client_delete_to_prod_raises(self):
        """AC1: httpx.Client().delete(prod_url) raises SAFETY ABORT — guard is live."""
        with pytest.raises(AssertionError, match="SAFETY ABORT"):
            with httpx.Client() as client:
                client.delete(_PROD_URL + "/1")


# ── AC2: subprocess guard ─────────────────────────────────────────────────────

class TestAC2SubprocessGuardHelper:
    """Unit tests for the _is_gh_api_prod_write helper function."""

    def test_helper_exists_in_conftest(self):
        """AC2: conftest.py must export _is_gh_api_prod_write."""
        conftest_src = (TESTS_DIR / "conftest.py").read_text()
        assert "_is_gh_api_prod_write" in conftest_src, (
            "conftest.py must define _is_gh_api_prod_write (AC2, issue #2141)"
        )

    def test_detects_post_with_dash_x(self):
        """AC2: -X POST form is detected as a prod write."""
        conftest = _load_conftest()
        args = ["gh", "api", "-X", "POST", "/repos/zealchaiwut/commander/issues"]
        assert conftest._is_gh_api_prod_write(args) is True

    def test_detects_post_with_method_flag(self):
        """AC2: --method POST form is detected as a prod write."""
        conftest = _load_conftest()
        args = ["gh", "api", "--method", "POST", "/repos/zealchaiwut/commander/issues"]
        assert conftest._is_gh_api_prod_write(args) is True

    def test_detects_delete(self):
        """AC2: DELETE method is detected as a prod write."""
        conftest = _load_conftest()
        args = ["gh", "api", "-X", "DELETE", "/repos/zealchaiwut/commander/milestones/7"]
        assert conftest._is_gh_api_prod_write(args) is True

    def test_detects_patch(self):
        """AC2: PATCH method is detected as a prod write."""
        conftest = _load_conftest()
        args = ["gh", "api", "--method", "PATCH", "/repos/zealchaiwut/commander/issues/1"]
        assert conftest._is_gh_api_prod_write(args) is True

    def test_allows_get_method(self):
        """AC2: GET is not a write — should return False."""
        conftest = _load_conftest()
        args = ["gh", "api", "-X", "GET", "/repos/zealchaiwut/commander/issues"]
        assert conftest._is_gh_api_prod_write(args) is False

    def test_allows_no_method_flag(self):
        """AC2: gh api with no explicit method defaults to GET — should return False."""
        conftest = _load_conftest()
        args = ["gh", "api", "/repos/zealchaiwut/commander/issues"]
        assert conftest._is_gh_api_prod_write(args) is False

    def test_allows_non_prod_repo(self):
        """AC2: write to a different repo is not blocked."""
        conftest = _load_conftest()
        args = ["gh", "api", "-X", "POST", "/repos/zealchaiwut/commander-issue-test/issues"]
        assert conftest._is_gh_api_prod_write(args) is False

    def test_allows_non_api_gh_command(self):
        """AC2: gh issue list is not a gh api call — should return False."""
        conftest = _load_conftest()
        args = ["gh", "issue", "list", "--repo", "zealchaiwut/commander"]
        assert conftest._is_gh_api_prod_write(args) is False

    def test_returns_false_for_non_gh_command(self):
        """AC2: non-gh commands return False."""
        conftest = _load_conftest()
        assert conftest._is_gh_api_prod_write(["curl", "-X", "POST", _PROD_URL]) is False

    def test_returns_false_for_short_args(self):
        """AC2: too-short args list returns False."""
        conftest = _load_conftest()
        assert conftest._is_gh_api_prod_write(["gh"]) is False
        assert conftest._is_gh_api_prod_write([]) is False


class TestAC2SubprocessGuardFactory:
    """Unit tests for the subprocess guard factory."""

    def test_make_gh_subprocess_guard_factory_exists(self):
        """AC2: conftest.py must export _make_gh_subprocess_guard."""
        conftest_src = (TESTS_DIR / "conftest.py").read_text()
        assert "_make_gh_subprocess_guard" in conftest_src, (
            "conftest.py must define _make_gh_subprocess_guard (AC2, issue #2141)"
        )

    def test_factory_blocks_prod_write(self):
        """AC2: factory guard raises AssertionError on gh api write to prod repo."""
        conftest = _load_conftest()
        mock_original = MagicMock()
        guard = conftest._make_gh_subprocess_guard(mock_original)
        with pytest.raises(AssertionError, match="SAFETY ABORT"):
            guard(["gh", "api", "-X", "POST", "/repos/zealchaiwut/commander/issues"])
        mock_original.assert_not_called()

    def test_factory_passes_through_read(self):
        """AC2: factory guard does NOT block gh api GET calls."""
        conftest = _load_conftest()
        mock_original = MagicMock(return_value=MagicMock())
        guard = conftest._make_gh_subprocess_guard(mock_original)
        guard(["gh", "api", "/repos/zealchaiwut/commander/issues"])
        mock_original.assert_called_once()

    def test_factory_passes_through_non_prod_write(self):
        """AC2: factory guard does NOT block writes to non-prod repos."""
        conftest = _load_conftest()
        mock_original = MagicMock(return_value=MagicMock())
        guard = conftest._make_gh_subprocess_guard(mock_original)
        guard(["gh", "api", "-X", "POST", "/repos/zealchaiwut/commander-test/issues"])
        mock_original.assert_called_once()


class TestAC2SubprocessBehavioral:
    """Behavioral tests: verify autouse session fixture blocks subprocess gh api writes."""

    def test_subprocess_run_gh_api_post_to_prod_raises(self):
        """AC2: subprocess.run gh api POST to prod raises SAFETY ABORT."""
        with pytest.raises(AssertionError, match="SAFETY ABORT"):
            subprocess.run(
                ["gh", "api", "-X", "POST", "/repos/zealchaiwut/commander/issues"],
                check=False,
            )

    def test_subprocess_run_gh_api_delete_to_prod_raises(self):
        """AC2: subprocess.run gh api DELETE to prod raises SAFETY ABORT."""
        with pytest.raises(AssertionError, match="SAFETY ABORT"):
            subprocess.run(
                ["gh", "api", "--method", "DELETE", "/repos/zealchaiwut/commander/milestones/1"],
                check=False,
            )


# ── AC3: no regression on existing module-level guards ───────────────────────

class TestAC3NoRegression:
    """Verify existing httpx.post/patch/delete guards still work after AC1+AC2 additions."""

    def test_httpx_post_to_prod_still_raises(self):
        """AC3: httpx.post(prod_url) still raises SAFETY ABORT — no regression."""
        with pytest.raises(AssertionError, match="SAFETY ABORT"):
            httpx.post(_PROD_URL, json={"title": "oops"})

    def test_httpx_patch_to_prod_still_raises(self):
        """AC3: httpx.patch(prod_url) still raises SAFETY ABORT — no regression."""
        with pytest.raises(AssertionError, match="SAFETY ABORT"):
            httpx.patch(_PROD_URL + "/1", json={"title": "evil"})

    def test_httpx_delete_to_prod_still_raises(self):
        """AC3: httpx.delete(prod_url) still raises SAFETY ABORT — no regression."""
        with pytest.raises(AssertionError, match="SAFETY ABORT"):
            httpx.delete(_PROD_URL + "/99")

    def test_guard_registered_in_fixture_docstring(self):
        """AC3: _gh_no_prod_write_guard docstring mentions the new coverage."""
        conftest_src = (TESTS_DIR / "conftest.py").read_text()
        assert "Client.send" in conftest_src or "httpx.Client" in conftest_src, (
            "_gh_no_prod_write_guard docstring/body must mention httpx.Client coverage"
        )
        assert "subprocess" in conftest_src, (
            "_gh_no_prod_write_guard must mention subprocess coverage"
        )
