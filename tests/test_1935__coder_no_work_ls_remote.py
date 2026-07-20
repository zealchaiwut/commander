"""Tests for issue #1935: CODER_NO_WORK false-fails when _find_feature_branch
uses stale remote-tracking refs instead of a live ls-remote query.

AC items verified:
  AC1  Before declaring CODER_NO_WORK, git ls-remote is used (not git branch -r)
       — _ls_remote_feature_branch calls 'git ls-remote origin refs/heads/feature/N-*'.
  AC2  A fetch failure (ls-remote fails) surfaces as ENV_ERROR / infra skip,
       NOT as CODER_NO_WORK — the ticket is not incorrectly retried.
  AC3  Behavioral: simulate pushed-but-unfetched branch (git branch -r empty,
       git ls-remote returns the branch) — gate must find it via ls-remote.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import services.sprint_manager.sprint_manager as sm


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_sprint_coder_ok_ls_remote(
    tmp_path: Path,
    *,
    ls_remote_return: tuple,
):
    """Run a sprint where the coder exits 0, then _ls_remote_feature_branch
    returns *ls_remote_return*.  All other dependencies are stubbed out.

    Returns (summary, state) so callers can assert on skipped/merged etc.
    """

    def fake_coder(issue_num, alert_modes, on_running=None, **kwargs):
        if on_running:
            on_running()
        return True, None  # coder exited 0 — no error

    with (
        patch.object(sm, "_create_sprint_branch",
                     lambda b, parent_ref="develop", fallback_ref="": None),
        patch.object(sm, "list_backlog_issues",
                     lambda label, repo_name=None: [{"number": 42, "title": "T"}]),
        patch.object(sm, "_dispatch_coder", fake_coder),
        patch.object(sm, "_dispatch_tester", lambda *a, **k: (0, None)),
        patch.object(sm, "handle_post_tester", lambda **k: (True, "merged", None)),
        patch.object(sm, "_post_sprint_status", lambda *a, **kw: None),
        patch.object(sm, "_neon_ticket_status", lambda *a, **kw: None),
        patch.object(sm, "_warn_file_conflicts", lambda i: None),
        patch.object(sm, "_setup_pid_file", lambda n: None),
        patch.object(sm, "_find_feature_branch", lambda n: f"feature/{n}-stub"),
        patch.object(sm, "_is_branch_merged_into", lambda *a, **kw: False),
        patch.object(sm, "_was_feature_merged_via_log", lambda *a, **kw: False),
        patch.object(sm, "_transition_safe", lambda *a, **k: None),
        patch.object(sm, "record_failure", lambda *a, **k: None),
        patch.object(sm, "dispatch_alerts", lambda *a, **kw: None),
        patch.object(sm, "_design_docs_guard", lambda p: None),
        patch.object(sm, "_emit_sprint_lifecycle_event", lambda *a, **kw: None),
        patch.object(sm, "_load_estimate", lambda n: None),
        patch.object(sm, "_publish_gate_failure_analyses", lambda *a, **kw: None),
        patch.object(sm, "_ls_remote_feature_branch",
                     lambda issue_num: ls_remote_return),
        patch.object(sm.SprintState, "save", lambda self, p: None),
    ):
        summary, state = sm.run_sprint(
            label="sprint-99",
            skip_gates=False,
            gate_pytest=False,
            gate_lint=False,
            gate_merge_preview=False,
            gate_typecheck=False,
            gate_design=False,
        )

    return summary, state


# ---------------------------------------------------------------------------
# AC1 — _ls_remote_feature_branch uses 'git ls-remote', not 'git branch -r'
# ---------------------------------------------------------------------------

class TestAC1LsRemoteIsUsed:
    """AC1: _ls_remote_feature_branch must call git ls-remote, not git branch -r."""

    def test_ls_remote_feature_branch_calls_git_ls_remote(self):
        """_ls_remote_feature_branch issues 'git ls-remote origin refs/heads/feature/N-*'."""
        calls: list[tuple] = []

        def capturing_try(*cmd, cwd=None):
            calls.append(cmd)
            if len(cmd) >= 2 and cmd[1] == "ls-remote":
                return True, "abc123\trefs/heads/feature/99-test-slug", ""
            return True, "", ""

        with patch.object(sm, "_try", capturing_try):
            branch, ok = sm._ls_remote_feature_branch(99)

        ls_remote_calls = [c for c in calls if len(c) >= 2 and c[1] == "ls-remote"]
        assert ls_remote_calls, "Expected at least one 'git ls-remote' call"
        first = ls_remote_calls[0]
        assert first[2] == "origin", f"Expected 'origin' as ls-remote target, got {first[2]!r}"
        assert "refs/heads/feature/99-*" in first[3], (
            f"Expected refs/heads/feature/99-* pattern, got {first[3]!r}"
        )

    def test_ls_remote_feature_branch_returns_branch_on_match(self):
        """When ls-remote finds a branch, returns (branch_name, True)."""

        def fake_try(*cmd, cwd=None):
            if "ls-remote" in cmd:
                return True, "deadbeef\trefs/heads/feature/42-some-slug", ""
            return True, "", ""

        with patch.object(sm, "_try", fake_try):
            branch, ok = sm._ls_remote_feature_branch(42)

        assert ok is True
        assert branch == "feature/42-some-slug"

    def test_ls_remote_feature_branch_returns_none_true_when_no_match(self):
        """When ls-remote succeeds but finds no branch, returns (None, True)."""

        def fake_try(*cmd, cwd=None):
            if "ls-remote" in cmd:
                return True, "", ""  # success, no output
            return True, "", ""

        with patch.object(sm, "_try", fake_try):
            branch, ok = sm._ls_remote_feature_branch(7)

        assert ok is True
        assert branch is None

    def test_no_git_branch_r_call_in_ls_remote_helper(self):
        """_ls_remote_feature_branch must not call git branch -r internally."""
        calls: list[tuple] = []

        def capturing_try(*cmd, cwd=None):
            calls.append(cmd)
            if "ls-remote" in cmd:
                return True, "abc\trefs/heads/feature/5-slug", ""
            return True, "", ""

        with patch.object(sm, "_try", capturing_try):
            sm._ls_remote_feature_branch(5)

        branch_r_calls = [
            c for c in calls
            if len(c) >= 3 and c[1] == "branch" and "-r" in c
        ]
        assert not branch_r_calls, (
            "ls_remote_feature_branch must not call 'git branch -r'; "
            f"found: {branch_r_calls}"
        )


# ---------------------------------------------------------------------------
# AC2 — ls-remote failure → ENV_ERROR infra skip, NOT CODER_NO_WORK
# ---------------------------------------------------------------------------

class TestAC2FetchFailureBecomesEnvError:
    """AC2: when ls-remote fails, the gate must classify as ENV_ERROR."""

    def test_ls_remote_failure_returns_none_false(self):
        """_ls_remote_feature_branch returns (None, False) when git ls-remote fails."""

        def failing_try(*cmd, cwd=None):
            if "ls-remote" in cmd:
                return False, "", "fatal: unable to connect to origin"
            return True, "", ""

        with patch.object(sm, "_try", failing_try):
            branch, ok = sm._ls_remote_feature_branch(10)

        assert ok is False, "Expected ok=False when ls-remote fails"
        assert branch is None

    def test_ls_remote_failure_causes_infra_skip_not_coder_no_work(self, tmp_path):
        """Gate: ls-remote failure → issue skipped (ENV_ERROR), not CODER_NO_WORK retry."""
        summary, state = _run_sprint_coder_ok_ls_remote(
            tmp_path,
            ls_remote_return=(None, False),  # ls-remote failed
        )

        assert any("42" in s for s in summary.skipped), (
            f"Expected issue #42 in skipped (ENV_ERROR infra path), got: {summary.skipped}"
        )
        assert not any("42" in str(m) for m in summary.merged), (
            "Issue must not be merged when ls-remote fails"
        )

    def test_ls_remote_failure_does_not_record_coder_no_work(self, tmp_path):
        """Gate: ls-remote failure must not record FailureCategory.CODER_NO_WORK."""
        recorded: list[dict] = []

        def capture_record(issue_num, failure_class, detail=None, **kw):
            recorded.append({"issue": issue_num, "class": failure_class})

        with patch.object(sm, "record_failure", capture_record):
            _run_sprint_coder_ok_ls_remote(
                tmp_path,
                ls_remote_return=(None, False),
            )

        coder_no_work_calls = [
            r for r in recorded
            if r["class"] == sm.FailureCategory.CODER_NO_WORK
        ]
        assert not coder_no_work_calls, (
            f"CODER_NO_WORK must not be recorded on ls-remote failure; got: {coder_no_work_calls}"
        )


# ---------------------------------------------------------------------------
# AC3 — pushed-but-unfetched branch: ls-remote finds it, gate must not miss it
# ---------------------------------------------------------------------------

class TestAC3PushedButUnfetchedBranch:
    """AC3: _find_feature_branch returns the branch when ls-remote finds it
    even when git branch -r (stale tracking refs) returns nothing."""

    def test_find_feature_branch_uses_ls_remote_before_tracking_refs(self):
        """When ls-remote finds the branch and tracking refs are empty, branch is returned."""
        calls: list[tuple] = []

        def fake_try(*cmd, cwd=None):
            calls.append(cmd)
            if "ls-remote" in cmd:
                return True, "cafebabe\trefs/heads/feature/77-pushed-not-fetched", ""
            if "branch" in cmd and "-r" in cmd:
                return True, "", ""  # stale: no tracking refs
            return True, "", ""

        with patch.object(sm, "_try", fake_try):
            result = sm._find_feature_branch(77)

        assert result == "feature/77-pushed-not-fetched", (
            f"Expected to find branch via ls-remote, got {result!r}"
        )
        ls_remote_used = any("ls-remote" in c for c in calls)
        assert ls_remote_used, "Must use git ls-remote to find the pushed branch"

    def test_find_feature_branch_returns_none_when_both_empty(self):
        """When both ls-remote and tracking refs are empty, returns None."""

        def fake_try(*cmd, cwd=None):
            if "ls-remote" in cmd:
                return True, "", ""
            if "branch" in cmd:
                return True, "", ""
            return True, "", ""

        with patch.object(sm, "_try", fake_try):
            result = sm._find_feature_branch(88)

        assert result is None

    def test_find_feature_branch_fallback_when_ls_remote_fails(self):
        """When ls-remote fails (network error), falls back to tracking refs gracefully."""

        def fake_try(*cmd, cwd=None):
            if "ls-remote" in cmd:
                return False, "", "fatal: Could not read from remote repository."
            if "branch" in cmd and "-r" in cmd:
                return True, "  origin/feature/33-old-tracking-ref\n", ""
            return True, "", ""

        with patch.object(sm, "_try", fake_try):
            result = sm._find_feature_branch(33)

        assert result == "feature/33-old-tracking-ref", (
            f"Expected fallback to tracking refs when ls-remote fails, got {result!r}"
        )

    def test_gate_finds_branch_via_ls_remote_when_tracking_refs_stale(self, tmp_path):
        """Sprint gate: coder pushes branch; gate finds it via ls-remote even if unfetched."""
        summary, state = _run_sprint_coder_ok_ls_remote(
            tmp_path,
            ls_remote_return=("feature/42-pushed-not-fetched", True),
        )

        assert not any("42" in s for s in summary.skipped), (
            f"Issue #42 must not be skipped when ls-remote finds the pushed branch; "
            f"skipped={summary.skipped}"
        )
