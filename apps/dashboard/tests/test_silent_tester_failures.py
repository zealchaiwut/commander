"""Tests for silent-failure fixes in the tester → merge → UAT pipeline.

Covers three distinct failure modes:

Bug 1 (finish_feature.py): UAT label apply retries 3× with backoff; posts warning
  comment and exits 2 on final failure.

Bug 2 (sprint_manager.py): handle_post_tester posts a comment and fires dispatch_alerts
  when tester exits 0 but the UAT label is never applied.

Bug 3 (sprint_manager.py): handle_post_tester detects that the tester agent already
  merged via finish_feature.py (feature branch gone) and skips the re-merge while
  still posting the success comment.

All tests are static (no live server, no git ops required).
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

REPO_ROOT             = Path(__file__).parent.parent.parent.parent
FINISH_FEATURE_SCRIPT = REPO_ROOT / "scripts" / "finish_feature.py"
SPRINT_MANAGER_SCRIPT = REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"


# ---------------------------------------------------------------------------
# Import helpers
# ---------------------------------------------------------------------------

def _stub_common_deps() -> None:
    """Insert stubs for dotenv and github_client so imports succeed."""
    dotenv_stub = types.ModuleType("dotenv")
    dotenv_stub.load_dotenv = lambda *a, **kw: None
    sys.modules.setdefault("dotenv", dotenv_stub)


def _import_finish_feature() -> tuple:
    """Return (finish_feature_module, github_client_stub) with fresh mocks."""
    _stub_common_deps()

    gc_stub = types.ModuleType("github_client")
    gc_stub.add_comment = MagicMock()
    sys.modules["github_client"] = gc_stub  # always fresh so mock is new

    mod_key = f"finish_feature_test_{id(gc_stub)}"
    spec = importlib.util.spec_from_file_location(mod_key, FINISH_FEATURE_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_key] = mod
    spec.loader.exec_module(mod)
    return mod, gc_stub


def _import_sprint_manager(mod_key: str = "sprint_manager_silent_failures") -> object:
    """Import sprint_manager with stubbed deps; cached after first load."""
    _stub_common_deps()

    gc_stub = types.ModuleType("github_client")
    gc_stub.repo = lambda: "zealchaiwut/commander"
    gc_stub.update_labels = lambda *a, **kw: None
    gc_stub.add_comment = MagicMock()
    sys.modules.setdefault("github_client", gc_stub)

    if mod_key in sys.modules:
        return sys.modules[mod_key]

    spec = importlib.util.spec_from_file_location(mod_key, SPRINT_MANAGER_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_key] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Subprocess side-effect factory for finish_feature tests
# ---------------------------------------------------------------------------

def _make_subproc_side_effect(uat_rc_sequence: list[int]):
    """Return a subprocess.run side_effect.

    All git commands succeed.  Calls to update_ticket.py return
    uat_rc_sequence[i] on the i-th invocation (last value repeated if exhausted).
    """
    uat_call_idx = [0]

    def side_effect(cmd, **kwargs):
        result = MagicMock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""

        cmd_list = list(cmd) if isinstance(cmd, (list, tuple)) else cmd.split()

        # Detect update_ticket.py invocations by inspecting the second argument
        if len(cmd_list) >= 2 and "update_ticket" in str(cmd_list[1]):
            i = uat_call_idx[0]
            uat_call_idx[0] += 1
            rc = uat_rc_sequence[i] if i < len(uat_rc_sequence) else uat_rc_sequence[-1]
            result.returncode = rc
            result.stderr = "mocked label error" if rc != 0 else ""
            return result

        # git branch --list — return a branch name so find_branch succeeds
        if "branch" in cmd_list and "--list" in cmd_list:
            result.stdout = "  feature/42-test\n"
            return result

        # All other git commands succeed silently
        return result

    return side_effect


# ===========================================================================
# Bug 1 — finish_feature.py retries + hard failure path
# ===========================================================================

class TestBug1UATLabelRetry:

    def test_all_attempts_fail_posts_comment_and_exits_2(self):
        """When update_ticket.py fails on all 4 attempts, a warning comment is
        posted to the issue and sys.exit(2) is raised."""
        ff, gc = _import_finish_feature()

        side_effect = _make_subproc_side_effect([1, 1, 1, 1])

        with patch.object(ff, "subprocess") as mock_subproc, \
             patch.object(ff, "time") as mock_time, \
             patch.object(ff, "github_client", gc), \
             patch("sys.argv", ["finish_feature.py", "--issue", "42"]):

            mock_subproc.run.side_effect = side_effect
            mock_subproc.CalledProcessError = __import__("subprocess").CalledProcessError

            with pytest.raises(SystemExit) as exc_info:
                ff.main()

        assert exc_info.value.code == 2, "sys.exit must be called with code 2 on final failure"

        assert gc.add_comment.called, "add_comment must be called when all UAT label attempts fail"
        comment_body = gc.add_comment.call_args[0][1]
        assert "Merge succeeded but UAT label could not be applied" in comment_body, (
            "Comment body must state that the merge succeeded but label apply failed"
        )

        # sleep should have been called for retries
        assert mock_time.sleep.call_count == 3, (
            "time.sleep must be called 3 times (between the 4 attempts)"
        )

    def test_succeeds_on_third_attempt_no_comment_no_exit(self):
        """When update_ticket.py fails twice then succeeds, no warning comment
        is posted and sys.exit is not called with a non-zero code."""
        ff, gc = _import_finish_feature()

        side_effect = _make_subproc_side_effect([1, 1, 0])

        with patch.object(ff, "subprocess") as mock_subproc, \
             patch.object(ff, "time") as mock_time, \
             patch.object(ff, "github_client", gc), \
             patch("sys.argv", ["finish_feature.py", "--issue", "42"]):

            mock_subproc.run.side_effect = side_effect
            mock_subproc.CalledProcessError = __import__("subprocess").CalledProcessError

            # Should complete without raising SystemExit
            ff.main()

        gc.add_comment.assert_not_called()
        assert mock_time.sleep.call_count == 2, (
            "time.sleep must be called twice (after failures on attempts 1 and 2)"
        )


# ===========================================================================
# Bug 2 — sprint_manager posts comment + alert when tester exits 0 but no UAT
# ===========================================================================

class TestBug2TesterExit0NoUATLabel:

    def test_posts_comment_and_alert_then_returns_rejected(self):
        """handle_post_tester must post a comment and fire dispatch_alerts when
        tester exits 0 but the UAT label is absent, then return TESTER_REJECTED."""
        sm = _import_sprint_manager()

        mock_add_comment = MagicMock()

        with mock.patch.object(sm, "_get_issue_labels", return_value={"sprint-8", "backlog"}), \
             mock.patch.object(sm.github_client, "add_comment", mock_add_comment), \
             mock.patch.object(sm, "dispatch_alerts") as mock_dispatch:

            result = sm.handle_post_tester(
                issue_num=46,
                tester_exit_code=0,
                skip_gates=False,
                gate_pytest=True,
                gate_lint=True,
                gate_merge_preview=True,
                alert_modes=["dashboard-banner"],
            )

        merged, summary_line, category = result

        assert merged is False
        assert category == sm.FailureCategory.TESTER_REJECTED

        mock_add_comment.assert_called_once()
        body = mock_add_comment.call_args[0][1]
        assert "tester exited 0 but not uat" in body.lower(), (
            "Comment body must contain 'tester exited 0 but not UAT' (case-insensitive)"
        )

        mock_dispatch.assert_called_once()
        dispatch_kwargs = mock_dispatch.call_args
        # First positional arg is alert_modes; check title keyword
        title = dispatch_kwargs[1].get("title") or dispatch_kwargs[0][1]
        assert "tester exited 0 but not UAT" in title

    def test_no_alert_when_alert_modes_empty(self):
        """dispatch_alerts must not be called if alert_modes is empty/None."""
        sm = _import_sprint_manager()

        with mock.patch.object(sm, "_get_issue_labels", return_value={"backlog"}), \
             mock.patch.object(sm.github_client, "add_comment"), \
             mock.patch.object(sm, "dispatch_alerts") as mock_dispatch:

            sm.handle_post_tester(
                issue_num=46,
                tester_exit_code=0,
                skip_gates=False,
                gate_pytest=True,
                gate_lint=True,
                gate_merge_preview=True,
                alert_modes=None,
            )

        mock_dispatch.assert_not_called()


# ===========================================================================
# Bug 3 — sprint_manager skips re-merge when tester already merged
# ===========================================================================

class TestBug3AlreadyMergedByTester:

    def test_skips_finish_feature_posts_success_comment_returns_true(self):
        """When UAT label is present but the feature branch is gone (tester already
        merged), handle_post_tester must NOT call _call_finish_feature, MUST call
        _post_success_comment, and must return (True, ..., None)."""
        sm = _import_sprint_manager()

        with mock.patch.object(sm, "_get_issue_labels", return_value={"UAT", "sprint-8"}), \
             mock.patch.object(sm, "_find_feature_branch", return_value=None), \
             mock.patch.object(sm, "_call_finish_feature") as mock_ff, \
             mock.patch.object(sm, "_post_success_comment") as mock_sc, \
             mock.patch.object(sm, "_post_agent_event"):

            result = sm.handle_post_tester(
                issue_num=99,
                tester_exit_code=0,
                skip_gates=True,
                gate_pytest=True,
                gate_lint=True,
                gate_merge_preview=True,
            )

        merged, summary_line, category = result

        assert merged is True, "_merged_ must be True when already merged by tester"
        assert category is None, "failure_category must be None on success"

        mock_ff.assert_not_called()
        mock_sc.assert_called_once()

    def test_finish_feature_called_when_branch_still_exists(self):
        """Regression: when the feature branch still exists, _call_finish_feature
        must still be called (existing behaviour preserved)."""
        sm = _import_sprint_manager()

        with mock.patch.object(sm, "_get_issue_labels", return_value={"UAT", "sprint-8"}), \
             mock.patch.object(sm, "_find_feature_branch", return_value="feature/99-my-feature"), \
             mock.patch.object(sm, "_call_finish_feature") as mock_ff, \
             mock.patch.object(sm, "_post_success_comment") as mock_sc, \
             mock.patch.object(sm, "_post_agent_event"):

            result = sm.handle_post_tester(
                issue_num=99,
                tester_exit_code=0,
                skip_gates=True,
                gate_pytest=True,
                gate_lint=True,
                gate_merge_preview=True,
            )

        merged, _, category = result

        assert merged is True
        assert category is None
        mock_ff.assert_called_once()
        mock_sc.assert_called_once()
