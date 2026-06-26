"""Tests for #659: Fix false failure when tester subprocess exits 0.

AC items verified:
  AC-1  Tester subprocess exit code 0 is mapped to passed status — never failed
  AC-2  finish_feature.py auto-merge proceeds when tester exits 0
  AC-3  Tester marks feature as failed only when exit code is non-zero
  AC-4  No regression: non-zero exit codes still block merge correctly
  AC-5  Log output clearly states pass/fail reason with the actual exit code
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import services.sprint_manager.sprint_manager as sm  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_popen_mock(exit_code: int) -> MagicMock:
    """Return a Popen mock whose wait() returns exit_code."""
    proc = MagicMock()
    proc.wait.return_value = exit_code
    proc.kill.side_effect = ProcessLookupError("already dead")
    return proc


def _make_killed_detector(issue_num: int = 1) -> sm.HangDetector:
    """Return a HangDetector whose _killed flag is True (simulates race condition)."""
    proc = MagicMock()
    proc.kill.side_effect = ProcessLookupError("already dead")
    detector = sm.HangDetector(
        issue_num=issue_num,
        log_path=None,
        proc=proc,
    )
    detector._killed = True  # force as if the race fired
    return detector


# ---------------------------------------------------------------------------
# AC-1: exit code 0 → passed status, never failed
# (race: HangDetector sets _killed=True after process already exited 0)
# ---------------------------------------------------------------------------

class TestExitZeroIsAlwaysPassed:
    """AC-1: _dispatch_tester exit 0 → (0, None), even when detector.killed."""

    def test_exit_0_returns_passed_tuple(self, tmp_path):
        """Exit code 0 must return (0, None) — the 'passed' signal."""
        log_path = tmp_path / "tester.log"
        log_path.write_text("", encoding="utf-8")

        proc = _make_popen_mock(exit_code=0)

        with (
            patch.object(sm.subprocess, "Popen", return_value=proc),
            patch.object(sm, "_r", return_value="owner/repo"),
            patch.object(sm, "_issue_log_path", return_value=log_path),
            patch.object(sm, "_post_agent_event"),
            patch.object(sm, "dispatch_alerts"),
            patch.object(sm, "_add_blocked_label"),
        ):
            rc, category = sm._dispatch_tester(
                issue_num=1,
                alert_modes=[],
                sprint_label="sprint-51",
            )

        assert rc == 0, f"exit 0 must return rc=0, got rc={rc}"
        assert category is None, f"exit 0 must return category=None, got {category}"

    def test_exit_0_with_killed_detector_returns_passed(self, tmp_path):
        """AC-1 race condition: detector.killed=True but process already exited 0.

        Hang detector fires in the window between proc.wait() returning 0 and
        detector.stop() being called.  Exit code 0 must win — status is passed.
        """
        log_path = tmp_path / "tester.log"
        log_path.write_text("output present", encoding="utf-8")

        proc = _make_popen_mock(exit_code=0)

        detector_instance = _make_killed_detector(issue_num=1)

        with (
            patch.object(sm.subprocess, "Popen", return_value=proc),
            patch.object(sm, "_r", return_value="owner/repo"),
            patch.object(sm, "_issue_log_path", return_value=log_path),
            patch.object(sm, "_post_agent_event"),
            patch.object(sm, "dispatch_alerts"),
            patch.object(sm, "_add_blocked_label"),
            patch.object(sm, "HangDetector", return_value=detector_instance),
        ):
            rc, category = sm._dispatch_tester(
                issue_num=1,
                alert_modes=[],
                sprint_label="sprint-51",
            )

        assert rc == 0, (
            "Exit code 0 must override a stale detector.killed=True — "
            f"got rc={rc}, category={category}"
        )
        assert category is None, (
            f"Exit 0 must return category=None even when detector.killed, got {category}"
        )

    def test_exit_0_never_returns_hang_category(self, tmp_path):
        """AC-1: _dispatch_tester exit 0 must NEVER return FailureCategory.HANG."""
        log_path = tmp_path / "tester.log"
        log_path.write_text("", encoding="utf-8")

        proc = _make_popen_mock(exit_code=0)
        detector_instance = _make_killed_detector(issue_num=2)

        with (
            patch.object(sm.subprocess, "Popen", return_value=proc),
            patch.object(sm, "_r", return_value="owner/repo"),
            patch.object(sm, "_issue_log_path", return_value=log_path),
            patch.object(sm, "_post_agent_event"),
            patch.object(sm, "dispatch_alerts"),
            patch.object(sm, "_add_blocked_label"),
            patch.object(sm, "HangDetector", return_value=detector_instance),
        ):
            rc, category = sm._dispatch_tester(
                issue_num=2,
                alert_modes=[],
                sprint_label="sprint-51",
            )

        assert category != sm.FailureCategory.HANG, (
            "FailureCategory.HANG must not be returned when process exited 0"
        )


# ---------------------------------------------------------------------------
# AC-2: finish_feature auto-merge proceeds when tester exits 0
# ---------------------------------------------------------------------------

class TestAutoMergeOnExitZero:
    """AC-2: handle_post_tester merges after gates when tester exits 0."""

    def test_merge_after_gates_when_branch_not_merged(self, tmp_path):
        """Gate-first: exit=0 + unmerged branch → gates run, then finish_feature."""
        cfg_stub = MagicMock()
        cfg_stub.worktree_tester = tmp_path / "tester"
        cfg_stub.worktree_tester_app = tmp_path / "tester" / "apps" / "dashboard"
        cfg_stub.repo_name = None
        cfg_stub.api_url = None
        cfg_stub.documentor_enabled = False
        cfg_stub.logs_dir = tmp_path / "logs"

        call_order: list[str] = []

        def track_finish(*a, **kw):
            call_order.append("finish")
            return (True, [])  # (merge_ok, conflict_files)

        def track_gates(*a, **kw):
            call_order.append("gates")
            return [sm.GateResult(gate="pytest", passed=True, skipped=False)]

        with (
            patch.object(sm, "_find_feature_branch", return_value="feature/1-slug"),
            patch.object(sm, "_is_branch_merged_into", return_value=False),
            patch.object(sm, "_call_finish_feature", side_effect=track_finish),
            patch.object(sm, "_run_quality_gates", side_effect=track_gates),
            patch.object(sm, "_transition_safe"),
            patch.object(sm, "_post_success_comment"),
            patch.object(sm, "_post_agent_event"),
            patch.object(sm, "sidecar_path", return_value=tmp_path / "sidecar.json"),
            patch.object(sm, "REPO_ROOT", tmp_path),
        ):
            ok, msg, cat = sm.handle_post_tester(
                issue_num=1,
                tester_exit_code=0,
                skip_gates=False,
                gate_pytest=True,
                gate_lint=False,
                gate_merge_preview=False,
                gate_typecheck=False,
                gate_design=False,
                cfg=cfg_stub,
                target_branch="sprint/sprint-51",
            )

        assert call_order == ["gates", "finish"], f"expected gates before merge, got {call_order}"
        assert ok, f"Expected merge success but got: msg={msg}"

    def test_auto_merge_attempted_when_branch_not_merged_skip_gates(self, tmp_path):
        """When --skip-gates and branch exists but not merged, _call_finish_feature is called."""
        cfg_stub = MagicMock()
        cfg_stub.worktree_tester = tmp_path / "tester"
        cfg_stub.worktree_tester_app = tmp_path / "tester" / "apps" / "dashboard"
        cfg_stub.repo_name = None
        cfg_stub.api_url = None
        cfg_stub.documentor_enabled = False
        cfg_stub.logs_dir = tmp_path / "logs"

        with (
            patch.object(sm, "_find_feature_branch", return_value="feature/1-slug"),
            patch.object(sm, "_is_branch_merged_into", return_value=False),
            patch.object(sm, "_call_finish_feature", return_value=(True, [])) as mock_finish,
            patch.object(sm, "_was_feature_merged_via_log", return_value=True),
            patch.object(sm, "_run_quality_gates", return_value=[
                sm.GateResult(gate="pytest", passed=True, skipped=True),
            ]),
            patch.object(sm, "_transition_safe"),
            patch.object(sm, "_post_success_comment"),
            patch.object(sm, "_post_agent_event"),
            patch.object(sm, "sidecar_path", return_value=tmp_path / "sidecar.json"),
            patch.object(sm, "REPO_ROOT", tmp_path),
        ):
            ok, msg, cat = sm.handle_post_tester(
                issue_num=1,
                tester_exit_code=0,
                skip_gates=True,
                gate_pytest=False,
                gate_lint=False,
                gate_merge_preview=False,
                gate_typecheck=False,
                gate_design=False,
                cfg=cfg_stub,
                target_branch="sprint/sprint-51",
            )

        mock_finish.assert_called_once()
        assert ok, f"Expected merge success but got: msg={msg}"

    def test_exit_0_branch_already_merged_succeeds(self, tmp_path):
        """AC-2: When exit=0 and branch already merged, handle_post_tester returns True."""
        cfg_stub = MagicMock()
        cfg_stub.worktree_tester = tmp_path / "tester"
        cfg_stub.worktree_tester_app = tmp_path / "tester" / "apps" / "dashboard"
        cfg_stub.repo_name = None
        cfg_stub.api_url = None
        cfg_stub.documentor_enabled = False
        cfg_stub.logs_dir = tmp_path / "logs"

        with (
            patch.object(sm, "_find_feature_branch", return_value=None),
            patch.object(sm, "_was_feature_merged_via_log", return_value=True),
            patch.object(sm, "_run_quality_gates", return_value=[
                sm.GateResult(gate="pytest", passed=True, skipped=True),
            ]),
            patch.object(sm, "_transition_safe"),
            patch.object(sm, "_post_success_comment"),
            patch.object(sm, "_post_agent_event"),
            patch.object(sm, "sidecar_path", return_value=tmp_path / "sidecar.json"),
            patch.object(sm, "REPO_ROOT", tmp_path),
        ):
            ok, msg, cat = sm.handle_post_tester(
                issue_num=637,
                tester_exit_code=0,
                skip_gates=True,
                gate_pytest=False,
                gate_lint=False,
                gate_merge_preview=False,
                gate_typecheck=False,
                gate_design=False,
                cfg=cfg_stub,
                target_branch="sprint/sprint-51",
            )

        assert ok, f"Exit 0 + branch already merged must succeed. msg={msg}"
        assert cat is None


# ---------------------------------------------------------------------------
# AC-3: failed only on non-zero exit or explicit test failure
# ---------------------------------------------------------------------------

class TestFailedOnlyOnNonZeroOrTestFailure:
    """AC-3: handle_post_tester returns failure ONLY for non-zero exit or gate fail."""

    def test_nonzero_exit_returns_failure(self):
        """Tester exit code 1 + NO work landed → (False, ..., CRASH).

        Defensive update: a non-zero exit only CRASHes when no feature branch /
        merge commit exists (genuine crash). When the work landed despite the
        exit code, handle_post_tester runs gates instead — see
        TestNonZeroSalvageWhenWorkLanded.
        """
        with (
            patch.object(sm, "_try"),
            patch.object(sm, "_find_feature_branch", return_value=None),
            patch.object(sm, "_was_feature_merged_via_log", return_value=False),
        ):
            ok, msg, cat = sm.handle_post_tester(
                issue_num=10,
                tester_exit_code=1,
                skip_gates=False,
                gate_pytest=True,
                gate_lint=False,
                gate_merge_preview=False,
                gate_typecheck=False,
                gate_design=False,
            )
        assert not ok, "Non-zero exit with no work landed must return ok=False"
        assert cat == sm.FailureCategory.CRASH
        assert "1" in msg, f"Exit code must appear in message: {msg!r}"

    def test_exit_0_gate_fail_returns_failure(self, tmp_path):
        """AC-3: Even with exit 0, a failed gate marks the feature as failed."""
        cfg_stub = MagicMock()
        cfg_stub.worktree_tester = tmp_path / "tester"
        cfg_stub.worktree_tester_app = tmp_path / "tester" / "apps" / "dashboard"
        cfg_stub.repo_name = None
        cfg_stub.api_url = None
        cfg_stub.documentor_enabled = False
        cfg_stub.logs_dir = tmp_path / "logs"

        with (
            patch.object(sm, "_find_feature_branch", return_value=None),
            patch.object(sm, "_was_feature_merged_via_log", return_value=True),
            patch.object(sm, "_run_quality_gates", return_value=[
                sm.GateResult(gate="pytest", passed=False, skipped=False),
            ]),
            patch.object(sm, "_revert_to_sit"),
            patch.object(sm, "_transition_safe"),
            patch.object(sm, "_post_agent_event"),
            patch.object(sm, "sidecar_path", return_value=tmp_path / "sidecar.json"),
            patch.object(sm, "REPO_ROOT", tmp_path),
        ):
            ok, msg, cat = sm.handle_post_tester(
                issue_num=10,
                tester_exit_code=0,
                skip_gates=False,
                gate_pytest=True,
                gate_lint=False,
                gate_merge_preview=False,
                gate_typecheck=False,
                gate_design=False,
                cfg=cfg_stub,
            )

        assert not ok, "Gate failure must return ok=False"
        assert cat in (sm.FailureCategory.PYTEST_FAIL, sm.FailureCategory.GATE_FAIL)


# ---------------------------------------------------------------------------
# AC-4: Non-zero exit codes still block merge correctly
# ---------------------------------------------------------------------------

class TestNonZeroBlocksMerge:
    """AC-4: Regression — non-zero exit codes must block merge."""

    @pytest.mark.parametrize("exit_code", [1, 2, 127, -1, -9])
    def test_nonzero_exit_blocks_merge(self, exit_code):
        """Non-zero exit + no work landed must block merge (ok=False, CRASH)."""
        with (
            patch.object(sm, "_try"),
            patch.object(sm, "_find_feature_branch", return_value=None),
            patch.object(sm, "_was_feature_merged_via_log", return_value=False),
        ):
            ok, msg, cat = sm.handle_post_tester(
                issue_num=20,
                tester_exit_code=exit_code,
                skip_gates=False,
                gate_pytest=False,
                gate_lint=False,
                gate_merge_preview=False,
                gate_typecheck=False,
                gate_design=False,
            )
        assert not ok, f"Exit code {exit_code} must block merge, but ok=True"
        assert cat == sm.FailureCategory.CRASH

    def test_dispatch_tester_nonzero_returns_nonzero(self, tmp_path):
        """_dispatch_tester exit code 1 must return (1, None), not (0, None)."""
        log_path = tmp_path / "tester.log"
        log_path.write_text("FAILED test_something", encoding="utf-8")

        proc = _make_popen_mock(exit_code=1)
        proc.kill.side_effect = None  # allow kill for non-zero path

        detector_instance = MagicMock()
        detector_instance.killed = False

        with (
            patch.object(sm.subprocess, "Popen", return_value=proc),
            patch.object(sm, "_r", return_value="owner/repo"),
            patch.object(sm, "_issue_log_path", return_value=log_path),
            patch.object(sm, "_post_agent_event"),
            patch.object(sm, "dispatch_alerts"),
            patch.object(sm, "_add_blocked_label"),
            patch.object(sm, "HangDetector", return_value=detector_instance),
            patch.object(sm, "_is_rate_limit_error", return_value=(False, None)),
        ):
            rc, category = sm._dispatch_tester(
                issue_num=20,
                alert_modes=[],
                sprint_label="sprint-51",
            )

        assert rc != 0, f"Non-zero exit must not return rc=0, got {rc}"

    def test_dispatch_tester_hang_with_nonzero_returns_hang(self, tmp_path):
        """Genuine hang (killed=True, rc=-9) must return HANG category."""
        log_path = tmp_path / "tester.log"
        log_path.write_text("", encoding="utf-8")

        proc = MagicMock()
        proc.wait.return_value = -9  # simulates kill signal
        proc.kill.return_value = None

        detector_instance = MagicMock()
        detector_instance.killed = True

        with (
            patch.object(sm.subprocess, "Popen", return_value=proc),
            patch.object(sm, "_r", return_value="owner/repo"),
            patch.object(sm, "_issue_log_path", return_value=log_path),
            patch.object(sm, "_post_agent_event"),
            patch.object(sm, "dispatch_alerts"),
            patch.object(sm, "_add_blocked_label"),
            patch.object(sm, "HangDetector", return_value=detector_instance),
        ):
            rc, category = sm._dispatch_tester(
                issue_num=20,
                alert_modes=[],
                sprint_label="sprint-51",
            )

        assert rc == -1, f"Genuine hang must return rc=-1, got {rc}"
        assert category == sm.FailureCategory.HANG


# ---------------------------------------------------------------------------
# AC-5: Log output clearly states pass/fail reason with actual exit code
# ---------------------------------------------------------------------------

class TestLogOutputIncludesExitCode:
    """AC-5: The summary message from handle_post_tester includes the exit code."""

    def test_nonzero_message_includes_exit_code(self):
        """Non-zero exit + no work landed: summary message includes the exit code."""
        for exit_code in [1, 2, 42]:
            with (
                patch.object(sm, "_try"),
                patch.object(sm, "_find_feature_branch", return_value=None),
                patch.object(sm, "_was_feature_merged_via_log", return_value=False),
            ):
                ok, msg, cat = sm.handle_post_tester(
                    issue_num=30,
                    tester_exit_code=exit_code,
                    skip_gates=False,
                    gate_pytest=False,
                    gate_lint=False,
                    gate_merge_preview=False,
                    gate_typecheck=False,
                    gate_design=False,
                )
            assert not ok
            assert str(exit_code) in msg, (
                f"Exit code {exit_code} must appear in failure message: {msg!r}"
            )

    def test_exit_0_dispatch_logs_passed(self, tmp_path, capsys):
        """AC-5: _dispatch_tester exit 0 must emit a log line indicating passed."""
        log_path = tmp_path / "tester.log"
        log_path.write_text("", encoding="utf-8")

        proc = _make_popen_mock(exit_code=0)

        with (
            patch.object(sm.subprocess, "Popen", return_value=proc),
            patch.object(sm, "_r", return_value="owner/repo"),
            patch.object(sm, "_issue_log_path", return_value=log_path),
            patch.object(sm, "_post_agent_event"),
            patch.object(sm, "dispatch_alerts"),
            patch.object(sm, "_add_blocked_label"),
        ):
            rc, category = sm._dispatch_tester(
                issue_num=30,
                alert_modes=[],
                sprint_label="sprint-51",
            )

        assert rc == 0
        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "pass" in output.lower() or "0" in output, (
            f"Expected pass/exit-0 indication in tester output, got: {output!r}"
        )


# ---------------------------------------------------------------------------
# AC-2 (regression): finish_feature.py must use origin/<branch> for merge
# so that a branch locked in the coder worktree doesn't block the merge.
# ---------------------------------------------------------------------------

class TestFinishFeatureUsesRemoteRef:
    """finish_feature.py merges origin/<branch>, not the local branch ref.

    The feature branch is always checked out in the coder worktree while the
    tester runs.  git refuses `git checkout <branch>` when that branch is locked
    in another worktree, causing finish_feature.py to exit non-zero and sprint_manager
    to return TESTER_REJECTED even though all tests passed.

    Regression guard: the merge command must reference the remote tracking ref.
    """

    def test_finish_feature_merges_origin_ref(self, tmp_path):
        """finish_feature.py subprocess call must use `origin/<branch>` not `<branch>`."""
        import importlib.util
        import subprocess as _subprocess

        ff_path = REPO_ROOT / "scripts" / "finish_feature.py"
        assert ff_path.exists(), f"finish_feature.py not found at {ff_path}"

        spec = importlib.util.spec_from_file_location("finish_feature", ff_path)
        ff = importlib.util.module_from_spec(spec)

        calls: list = []

        def fake_run(*cmd):
            calls.append(list(cmd))
            if cmd == ("git", "rev-parse", "HEAD"):
                raise _subprocess.CalledProcessError(1, cmd)
            return ""

        def fake_try(*cmd):
            if cmd[0] == "git" and cmd[1] == "show-ref":
                # target branch exists locally
                return True, ""
            calls.append(list(cmd))
            return True, "abc123"

        def fake_find_branch(issue_num):
            return f"feature/{issue_num}-slug"

        with (
            patch("builtins.print"),
            patch("sys.exit"),
            patch("sys.argv", ["finish_feature.py", "--issue", "659", "--target-branch", "sprint/sprint-52"]),
        ):
            spec.loader.exec_module(ff)

            with (
                patch.object(ff, "_run", side_effect=fake_run),
                patch.object(ff, "_try", side_effect=fake_try),
                patch.object(ff, "find_branch", side_effect=fake_find_branch),
                patch.object(ff, "github_client", MagicMock()),
                patch("subprocess.run") as mock_subrun,
            ):
                mock_subrun.return_value = MagicMock(returncode=0, stdout="", stderr="")

                try:
                    ff.main()
                except (SystemExit, Exception):
                    pass

        # The subprocess.run call for git merge must use origin/<branch>
        merge_calls = [c for c in mock_subrun.call_args_list if c.args and "merge" in c.args[0]]
        assert merge_calls, "git merge was not called via subprocess.run"
        merge_cmd = merge_calls[0].args[0]
        assert any("origin/" in arg for arg in merge_cmd), (
            f"merge command must use origin/<branch>, got: {merge_cmd}"
        )
        assert not any(arg == "feature/659-slug" for arg in merge_cmd), (
            f"merge command must NOT use bare local branch ref, got: {merge_cmd}"
        )


# ---------------------------------------------------------------------------
# AC-2 (regression): handle_post_tester must git fetch --prune before checking
# branch state so stale remote-tracking refs don't cause false TESTER_REJECTED.
# ---------------------------------------------------------------------------

class TestFetchBeforeBranchCheck:
    """handle_post_tester fetches remote state before checking branch presence.

    Without this fetch, stale remote-tracking refs can mis-detect merge state
    after a legacy tester finish_feature.py run (issue #659).
    """

    def test_fetch_called_before_find_branch(self, tmp_path):
        """git fetch --prune origin is called before _find_feature_branch."""
        cfg_stub = MagicMock()
        cfg_stub.worktree_tester = tmp_path / "tester"
        cfg_stub.worktree_tester_app = tmp_path / "tester" / "apps" / "dashboard"
        cfg_stub.repo_name = None
        cfg_stub.api_url = None
        cfg_stub.documentor_enabled = False
        cfg_stub.logs_dir = tmp_path / "logs"

        call_order: list[str] = []

        def track_find(issue_num):
            call_order.append("find")
            return None  # branch gone after fetch

        def track_try(*args, **kw):
            if tuple(args[:3]) == ("git", "fetch", "--prune"):
                call_order.append("fetch")
            return (True, "", "")

        with (
            patch.object(sm, "_try", side_effect=track_try),
            patch.object(sm, "_find_feature_branch", side_effect=track_find),
            patch.object(sm, "_was_feature_merged_via_log", return_value=True),
            patch.object(sm, "_run_quality_gates", return_value=[
                sm.GateResult(gate="pytest", passed=True, skipped=True),
            ]),
            patch.object(sm, "_transition_safe"),
            patch.object(sm, "_post_success_comment"),
            patch.object(sm, "_post_agent_event"),
            patch.object(sm, "sidecar_path", return_value=tmp_path / "sidecar.json"),
            patch.object(sm, "REPO_ROOT", tmp_path),
        ):
            ok, msg, cat = sm.handle_post_tester(
                issue_num=659,
                tester_exit_code=0,
                skip_gates=True,
                gate_pytest=False,
                gate_lint=False,
                gate_merge_preview=False,
                gate_typecheck=False,
                gate_design=False,
                cfg=cfg_stub,
                target_branch="sprint/sprint-52",
            )

        assert "fetch" in call_order, (
            "handle_post_tester must call git fetch --prune origin before checking branches"
        )
        assert "find" in call_order, "handle_post_tester must call _find_feature_branch"
        fetch_idx = call_order.index("fetch")
        find_idx = call_order.index("find")
        assert fetch_idx < find_idx, (
            f"fetch must precede _find_feature_branch; call order: {call_order}"
        )
        assert ok, f"Expected success; msg={msg}"


# ---------------------------------------------------------------------------
# Defensive salvage: flaky `claude -p` exit but the work landed
# ---------------------------------------------------------------------------

class TestNonZeroSalvageWhenWorkLanded:
    """A non-zero tester exit must NOT false-CRASH a ticket whose work landed.

    The tester sometimes completes (tests pass, feature branch pushed) yet the
    CLI returns non-zero. When a `feature/<N>-*` branch is present (or a merge
    commit is already in target), handle_post_tester runs the quality gates and
    merges instead of crashing — the gates still protect quality.
    """

    def _cfg(self, tmp_path):
        cfg_stub = MagicMock()
        cfg_stub.worktree_tester = tmp_path / "tester"
        cfg_stub.worktree_tester_app = tmp_path / "tester" / "apps" / "dashboard"
        cfg_stub.repo_name = None
        cfg_stub.api_url = None
        cfg_stub.documentor_enabled = False
        cfg_stub.logs_dir = tmp_path / "logs"
        return cfg_stub

    def test_nonzero_with_branch_present_runs_gates_and_merges(self, tmp_path):
        """exit=1 + feature branch present + gates pass → ok=True, gates before merge."""
        call_order: list[str] = []

        def track_finish(*a, **kw):
            call_order.append("finish")
            return (True, [])  # (merge_ok, conflict_files)

        def track_gates(*a, **kw):
            call_order.append("gates")
            return [sm.GateResult(gate="pytest", passed=True, skipped=False)]

        with (
            patch.object(sm, "_try"),
            patch.object(sm, "_find_feature_branch", return_value="feature/30-slug"),
            patch.object(sm, "_is_branch_merged_into", return_value=False),
            patch.object(sm, "_is_issue_merged_into_target", return_value=False),
            patch.object(sm, "_was_feature_merged_via_log", return_value=False),
            patch.object(sm, "_call_finish_feature", side_effect=track_finish),
            patch.object(sm, "_run_quality_gates", side_effect=track_gates),
            patch.object(sm, "_transition_safe"),
            patch.object(sm, "_post_success_comment"),
            patch.object(sm, "_post_agent_event"),
            patch.object(sm, "sidecar_path", return_value=tmp_path / "sidecar.json"),
            patch.object(sm, "REPO_ROOT", tmp_path),
        ):
            ok, msg, cat = sm.handle_post_tester(
                issue_num=30,
                tester_exit_code=1,
                skip_gates=False,
                gate_pytest=True,
                gate_lint=False,
                gate_merge_preview=False,
                gate_typecheck=False,
                gate_design=False,
                cfg=self._cfg(tmp_path),
                target_branch="sprint/sprint-51",
            )

        assert ok, f"Non-zero exit + work landed + gates pass must succeed; msg={msg}"
        assert cat is None
        assert call_order == ["gates", "finish"], (
            f"gates must run before merge on the salvage path; got {call_order}"
        )

    def test_nonzero_with_branch_present_but_gates_fail_still_fails(self, tmp_path):
        """exit=1 + branch present + gate FAIL → still ok=False (quality enforced)."""
        with (
            patch.object(sm, "_try"),
            patch.object(sm, "_find_feature_branch", return_value="feature/31-slug"),
            patch.object(sm, "_is_branch_merged_into", return_value=False),
            patch.object(sm, "_is_issue_merged_into_target", return_value=False),
            patch.object(sm, "_was_feature_merged_via_log", return_value=False),
            patch.object(sm, "_run_quality_gates", return_value=[
                sm.GateResult(gate="pytest", passed=False, skipped=False),
            ]),
            patch.object(sm, "_revert_to_sit"),
            patch.object(sm, "_transition_safe"),
            patch.object(sm, "_post_agent_event"),
            patch.object(sm, "sidecar_path", return_value=tmp_path / "sidecar.json"),
            patch.object(sm, "REPO_ROOT", tmp_path),
        ):
            ok, msg, cat = sm.handle_post_tester(
                issue_num=31,
                tester_exit_code=1,
                skip_gates=False,
                gate_pytest=True,
                gate_lint=False,
                gate_merge_preview=False,
                gate_typecheck=False,
                gate_design=False,
                cfg=self._cfg(tmp_path),
                target_branch="sprint/sprint-51",
            )

        assert not ok, "A failed gate must still block the salvage path"
        assert cat in (sm.FailureCategory.PYTEST_FAIL, sm.FailureCategory.GATE_FAIL)
