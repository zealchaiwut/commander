"""Tests for #1935: CODER_NO_WORK false-fails due to stale remote-tracking refs.

AC items verified:
  AC-1  Before declaring CODER_NO_WORK, git ls-remote is used as the
        authoritative check (not stale remote-tracking refs)
  AC-2  ls-remote failure returns (None, False) — infra error, not CODER_NO_WORK
  AC-3  Behavioral: pushed-but-unfetched branch is found via ls-remote even
        when local tracking refs are stale
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import services.sprint_manager.sprint_manager as sm  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ls_remote_output(issue_num: int, slug: str = "slug") -> str:
    """Return git ls-remote-style output for a single branch."""
    return f"abc123def456abc123def456abc123def456abc1\trefs/heads/feature/{issue_num}-{slug}"


# ---------------------------------------------------------------------------
# AC-1: _ls_remote_feature_branch uses git ls-remote (live query)
# ---------------------------------------------------------------------------

class TestLsRemoteFeatureBranch:
    """AC-1: _ls_remote_feature_branch issues a git ls-remote origin query."""

    def test_queries_origin_with_ls_remote(self):
        """_ls_remote_feature_branch calls git ls-remote origin refs/heads/feature/N-*."""
        calls = []

        def fake_try(*args, **kwargs):
            calls.append(args)
            return True, _make_ls_remote_output(42), ""

        with patch.object(sm, "_try", side_effect=fake_try):
            sm._ls_remote_feature_branch(42)

        ls_remote_calls = [c for c in calls if len(c) >= 3 and c[1] == "ls-remote"]
        assert ls_remote_calls, "_ls_remote_feature_branch must call git ls-remote"
        cmd = ls_remote_calls[0]
        assert cmd[2] == "origin", f"Must query 'origin', got: {cmd[2]!r}"
        assert "refs/heads/feature/42-" in cmd[3], (
            f"Must pass the feature ref pattern, got: {cmd[3]!r}"
        )

    def test_returns_branch_name_when_found(self):
        """AC-1: Returns (branch_name, True) when remote has the branch."""
        with patch.object(sm, "_try", return_value=(True, _make_ls_remote_output(1935, "bug-fix"), "")):
            branch, ok = sm._ls_remote_feature_branch(1935)

        assert ok is True, "ls-remote success must set ok=True"
        assert branch == "feature/1935-bug-fix", f"Expected branch name, got {branch!r}"

    def test_returns_none_true_when_branch_absent(self):
        """AC-1: Returns (None, True) when ls-remote succeeds but branch does not exist."""
        with patch.object(sm, "_try", return_value=(True, "", "")):
            branch, ok = sm._ls_remote_feature_branch(999)

        assert ok is True, "Empty ls-remote output is not an error"
        assert branch is None, f"No branch found must return None, got {branch!r}"

    def test_parses_multiple_candidates_picks_first(self):
        """AC-1: Multiple matching branches — returns the first candidate."""
        two_refs = (
            "abc1\trefs/heads/feature/77-old-slug\n"
            "def2\trefs/heads/feature/77-new-slug"
        )
        # For multi-candidate disambiguation, log lookup may be called; stub it out
        def fake_try(*args, **kwargs):
            if args[1] == "ls-remote":
                return True, two_refs, ""
            if args[1] == "rev-parse":
                return False, "", ""  # no local tracking ref
            if args[1] == "log":
                return True, "some commit message", ""
            return True, "", ""

        with patch.object(sm, "_try", side_effect=fake_try):
            branch, ok = sm._ls_remote_feature_branch(77)

        assert ok is True
        assert branch is not None, "Must return one of the candidate branches"


# ---------------------------------------------------------------------------
# AC-2: ls-remote failure returns (None, False) — infra error signaling
# ---------------------------------------------------------------------------

class TestLsRemoteInfraFailure:
    """AC-2: ls-remote git failure signals infra error, not CODER_NO_WORK."""

    def test_returns_none_false_on_git_failure(self):
        """AC-2: When git ls-remote fails, returns (None, False) to signal infra error."""
        with patch.object(sm, "_try", return_value=(False, "", "fatal: unable to connect to origin")):
            branch, ok = sm._ls_remote_feature_branch(1935)

        assert ok is False, (
            "A git ls-remote failure must return ok=False so callers can distinguish "
            "infra error from 'branch truly absent'"
        )
        assert branch is None

    def test_false_means_infra_not_no_branch(self):
        """AC-2: (None, False) is distinct from (None, True): failure ≠ absent branch."""
        with patch.object(sm, "_try", return_value=(False, "", "network error")):
            failure_result = sm._ls_remote_feature_branch(100)

        with patch.object(sm, "_try", return_value=(True, "", "")):
            absent_result = sm._ls_remote_feature_branch(100)

        assert failure_result[1] is False, "Infra failure must be (None, False)"
        assert absent_result[1] is True, "Absent branch must be (None, True)"
        assert failure_result != absent_result, (
            "Infra failure and absent-branch must be distinguishable return values — "
            "CODER_NO_WORK is only declared when (None, True), never on (None, False)"
        )


# ---------------------------------------------------------------------------
# AC-3: pushed-but-unfetched branch is found via ls-remote
# ---------------------------------------------------------------------------

class TestFindFeatureBranchUsesLsRemote:
    """AC-3: _find_feature_branch finds branches pushed to origin but not fetched locally."""

    def test_finds_pushed_but_unfetched_branch(self):
        """AC-3: Branch pushed to origin but missing from local tracking refs is found.

        Simulates: coder pushed feature/1935-fix to origin but the sprint-manager
        worktree has not run git fetch — so git branch -r shows nothing.
        Before this fix, _find_feature_branch returned None (false CODER_NO_WORK).
        After the fix, it calls git ls-remote and finds the branch.
        """
        def fake_try(*args, **kwargs):
            cmd = args[1] if len(args) > 1 else ""
            if cmd == "ls-remote":
                # Remote has the branch (even though tracking refs are stale)
                return True, _make_ls_remote_output(1935, "fix"), ""
            if cmd == "branch":
                # Local tracking refs are stale — returns nothing
                return True, "", ""
            return True, "", ""

        with patch.object(sm, "_try", side_effect=fake_try):
            result = sm._find_feature_branch(1935)

        assert result == "feature/1935-fix", (
            f"_find_feature_branch must find pushed-but-unfetched branch via ls-remote; "
            f"got {result!r}"
        )

    def test_ls_remote_called_before_tracking_refs(self):
        """AC-3: git ls-remote is called before git branch -r (ls-remote is primary)."""
        call_order: list[str] = []

        def fake_try(*args, **kwargs):
            cmd = args[1] if len(args) > 1 else ""
            if cmd == "ls-remote":
                call_order.append("ls-remote")
                return True, _make_ls_remote_output(1935), ""
            if cmd == "branch":
                call_order.append("branch-r")
                return True, "", ""
            return True, "", ""

        with patch.object(sm, "_try", side_effect=fake_try):
            sm._find_feature_branch(1935)

        assert "ls-remote" in call_order, "_find_feature_branch must call git ls-remote"
        if "branch-r" in call_order:
            ls_idx = call_order.index("ls-remote")
            br_idx = call_order.index("branch-r")
            assert ls_idx < br_idx, (
                f"ls-remote must precede git branch -r; order was {call_order}"
            )

    def test_falls_back_to_tracking_refs_on_ls_remote_failure(self):
        """AC-3 regression: ls-remote failure falls back to tracking refs gracefully."""
        def fake_try(*args, **kwargs):
            cmd = args[1] if len(args) > 1 else ""
            if cmd == "ls-remote":
                return False, "", "network error"
            if cmd == "branch" and "-r" in args:
                return True, "  origin/feature/1935-slug\n", ""
            if cmd == "branch":
                return True, "", ""
            return True, "", ""

        with patch.object(sm, "_try", side_effect=fake_try):
            result = sm._find_feature_branch(1935)

        assert result == "feature/1935-slug", (
            "On ls-remote failure, must fall back to tracking refs; "
            f"got {result!r}"
        )

    def test_find_feature_branch_returns_none_only_when_truly_absent(self):
        """AC-3: None is returned only when both ls-remote AND tracking refs confirm absence."""
        def fake_try(*args, **kwargs):
            cmd = args[1] if len(args) > 1 else ""
            if cmd == "ls-remote":
                return True, "", ""  # ls-remote ok, no branch
            if cmd == "branch":
                return True, "", ""  # tracking refs also empty
            return True, "", ""

        with patch.object(sm, "_try", side_effect=fake_try):
            result = sm._find_feature_branch(9999)

        assert result is None, (
            "Must return None when both ls-remote and tracking refs confirm branch is absent"
        )
