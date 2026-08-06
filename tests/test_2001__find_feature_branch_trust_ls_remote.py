"""Tests for issue #2001: _find_feature_branch must trust ls-remote's authoritative
'no branch' result instead of falling back to stale tracking refs.

AC items verified:
  AC1  When ls-remote succeeds and finds NO branch (ls_ok=True, ls_branch=None),
       _find_feature_branch returns None immediately — it MUST NOT consult
       git branch -r (stale tracking refs that may still show a deleted branch).
  AC2  When ls-remote fails (ls_ok=False), the tracking-ref fallback is still
       consulted (graceful degradation for network errors — existing behaviour
       must be preserved).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import services.sprint_manager.sprint_manager as sm


class TestAC1TrustLsRemoteNoResult:
    """AC1: when ls-remote succeeds but finds no branch, return None immediately."""

    def test_returns_none_without_consulting_tracking_refs(self):
        """ls-remote OK + no branch → None; git branch -r must NOT be called."""
        branch_r_called = []

        def fake_try(*cmd, cwd=None):
            if "ls-remote" in cmd:
                # ls-remote succeeds, finds nothing
                return True, "", ""
            if "branch" in cmd:
                # Record that git branch was consulted (it must NOT be)
                branch_r_called.append(cmd)
                return True, "  origin/feature/99-stale-deleted-branch\n", ""
            return True, "", ""

        with patch.object(sm, "_try", fake_try):
            result = sm._find_feature_branch(99)

        assert result is None, (
            f"Expected None when ls-remote authoritatively reports no branch, got {result!r}"
        )
        assert not branch_r_called, (
            f"git branch must NOT be consulted when ls-remote succeeded; "
            f"was called with: {branch_r_called}"
        )

    def test_stale_tracking_ref_not_returned_when_ls_remote_authoritative(self):
        """A branch deleted on origin but still in local tracking refs must not surface."""
        def fake_try(*cmd, cwd=None):
            if "ls-remote" in cmd:
                # Branch was deleted on origin — ls-remote finds nothing
                return True, "", ""
            if "branch" in cmd and "-r" in cmd:
                # Local tracking ref is stale — still shows the deleted branch
                return True, "  origin/feature/55-deleted-on-origin\n", ""
            if "branch" in cmd:
                return True, "", ""
            return True, "", ""

        with patch.object(sm, "_try", fake_try):
            result = sm._find_feature_branch(55)

        assert result is None, (
            f"Stale tracking ref must not be returned when ls-remote reports no branch; "
            f"got {result!r}"
        )


class TestAC2FallbackPreservedOnLsRemoteFailure:
    """AC2: when ls-remote fails, tracking-ref fallback must still run."""

    def test_fallback_to_tracking_refs_when_ls_remote_network_error(self):
        """When ls-remote fails (network error), git branch -r is consulted."""
        def fake_try(*cmd, cwd=None):
            if "ls-remote" in cmd:
                # Simulate network / infra failure
                return False, "", "fatal: Could not read from remote repository."
            if "branch" in cmd and "-r" in cmd:
                return True, "  origin/feature/33-fallback-branch\n", ""
            if "branch" in cmd:
                return True, "", ""
            return True, "", ""

        with patch.object(sm, "_try", fake_try):
            result = sm._find_feature_branch(33)

        assert result == "feature/33-fallback-branch", (
            f"Must fall back to tracking refs when ls-remote fails; got {result!r}"
        )

    def test_fallback_to_local_branch_when_ls_remote_fails_and_no_tracking_ref(self):
        """When ls-remote fails and remote tracking refs are empty, local branches checked."""
        def fake_try(*cmd, cwd=None):
            if "ls-remote" in cmd:
                return False, "", "fatal: Could not read from remote repository."
            if "branch" in cmd and "-r" in cmd:
                return True, "", ""  # no remote tracking refs
            if "branch" in cmd and "--list" in cmd:
                return True, "  feature/77-local-only\n", ""
            return True, "", ""

        with patch.object(sm, "_try", fake_try):
            result = sm._find_feature_branch(77)

        assert result == "feature/77-local-only", (
            f"Must fall back to local branch when ls-remote fails; got {result!r}"
        )
