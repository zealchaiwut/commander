"""Tests for issue #1839: wire rerun_dispatch_error into the run_sprint exit path.

The preflight already sets rerun_dispatch_error=True on the result when a
rerun manifest with all_moved=True produces 0 dispatchable tickets.  This
issue wires that signal into run_sprint() so the sprint exits non-zero and
plan.json/DB are marked needs_rework instead of being reset to a runnable
draft.

AC coverage:
- AC-1: When run_sprint_preflight returns early_exit=True + rerun_dispatch_error=True,
        run_sprint() raises SystemExit(1) (not a normal return).
- AC-2: plan.json is written with state="needs_rework" and
        end_reason="rerun-dispatch-error" before the exit.
- AC-3: A regular early_exit (rerun_dispatch_error=False) still returns
        normally and does NOT raise.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

from services.sprint_manager.sprint_manager import (  # noqa: E402
    _SprintPreflightResult,
    run_sprint,
)
from services.sprint_manager.alerts import AlertMode  # noqa: E402
from services.sprint_manager.state import SprintState, SprintSummary  # noqa: E402

_SM = "services.sprint_manager.sprint_manager"
_LABEL = "sprint-4.1"
_REPO = "owner/proj"


def _make_pf(early_exit: bool, rerun_dispatch_error: bool) -> _SprintPreflightResult:
    """Build a minimal _SprintPreflightResult for testing the early-exit branches."""
    import time
    from pathlib import Path

    state = SprintState(
        sprint_label=_LABEL,
        sprint_number=4,
        project=_REPO,
        start_timestamp="2026-01-01T00:00:00Z",
    )
    summary = SprintSummary()
    return _SprintPreflightResult(
        state=state,
        state_path=Path("/tmp/fake-state.json"),
        summary=summary,
        sprint_num=4,
        sprint_branch=f"sprint/{_LABEL}",
        target_branch=f"sprint/{_LABEL}",
        eff_repo=_REPO,
        api_url=None,
        run_id="run-test",
        rerun_decisions={},
        eff_sprints_dir=Path("/tmp"),
        dispatch_levels=[],
        level_nums_by_idx=[],
        pipeline_on=False,
        start_time=time.monotonic(),
        early_exit=early_exit,
        rerun_dispatch_error=rerun_dispatch_error,
    )


def _common_patches(stack, pf: _SprintPreflightResult) -> None:
    """Enter shared patches for run_sprint calls in this test module."""
    stack.enter_context(
        patch(f"{_SM}.run_sprint_preflight", return_value=pf)
    )
    stack.enter_context(patch(f"{_SM}.AlertMode"))
    stack.enter_context(patch(f"{_SM}.structured_log"))


# ── AC-1: dispatch error → SystemExit(1) ──────────────────────────────────────

class TestDispatchErrorRaisesSystemExit:
    """AC-1: run_sprint() must exit non-zero when rerun_dispatch_error is True."""

    def test_dispatch_error_raises_system_exit(self, tmp_path):
        """run_sprint() raises SystemExit when preflight returns rerun_dispatch_error=True."""
        import contextlib

        pf = _make_pf(early_exit=True, rerun_dispatch_error=True)
        with contextlib.ExitStack() as stack:
            _common_patches(stack, pf)
            stack.enter_context(patch(f"{_SM}._plan_json_set_state_sm"))
            stack.enter_context(patch(f"{_SM}._sprint_db_set_state_sm"))
            with pytest.raises(SystemExit) as exc_info:
                run_sprint(
                    label=_LABEL,
                    skip_gates=False,
                    gate_pytest=True,
                    gate_lint=True,
                    gate_merge_preview=True,
                    alert_modes=[AlertMode.DASHBOARD_BANNER],
                    repo_name=_REPO,
                    dry_run=True,
                )
        assert exc_info.value.code == 1, (
            "run_sprint must raise SystemExit(1) when rerun_dispatch_error=True, "
            f"got SystemExit({exc_info.value.code})"
        )

    def test_dispatch_error_does_not_return_normally(self, tmp_path):
        """run_sprint() must not return (summary, state) when rerun_dispatch_error=True."""
        import contextlib

        pf = _make_pf(early_exit=True, rerun_dispatch_error=True)
        raised = False
        with contextlib.ExitStack() as stack:
            _common_patches(stack, pf)
            stack.enter_context(patch(f"{_SM}._plan_json_set_state_sm"))
            stack.enter_context(patch(f"{_SM}._sprint_db_set_state_sm"))
            try:
                run_sprint(
                    label=_LABEL,
                    skip_gates=False,
                    gate_pytest=True,
                    gate_lint=True,
                    gate_merge_preview=True,
                    alert_modes=[AlertMode.DASHBOARD_BANNER],
                    repo_name=_REPO,
                    dry_run=True,
                )
            except SystemExit:
                raised = True

        assert raised, (
            "run_sprint must raise SystemExit (not return normally) "
            "when preflight rerun_dispatch_error=True"
        )


# ── AC-2: plan.json written with needs_rework + rerun-dispatch-error ───────────

class TestDispatchErrorWritesPlanJson:
    """AC-2: plan.json must be written with needs_rework and rerun-dispatch-error
    end_reason before the process exits."""

    def test_plan_json_set_needs_rework(self, tmp_path):
        """_plan_json_set_state_sm called with state='needs_rework'."""
        import contextlib

        pf = _make_pf(early_exit=True, rerun_dispatch_error=True)
        mock_plan_json = MagicMock()
        with contextlib.ExitStack() as stack:
            _common_patches(stack, pf)
            stack.enter_context(
                patch(f"{_SM}._plan_json_set_state_sm", mock_plan_json)
            )
            stack.enter_context(patch(f"{_SM}._sprint_db_set_state_sm"))
            with pytest.raises(SystemExit):
                run_sprint(
                    label=_LABEL,
                    skip_gates=False,
                    gate_pytest=True,
                    gate_lint=True,
                    gate_merge_preview=True,
                    alert_modes=[AlertMode.DASHBOARD_BANNER],
                    repo_name=_REPO,
                    dry_run=True,
                )

        assert mock_plan_json.called, "_plan_json_set_state_sm must be called"
        args, kwargs = mock_plan_json.call_args
        assert args[1] == "needs_rework", (
            f"_plan_json_set_state_sm must be called with state='needs_rework', "
            f"got {args[1]!r}"
        )

    def test_plan_json_end_reason_is_rerun_dispatch_error(self, tmp_path):
        """_plan_json_set_state_sm called with end_reason='rerun-dispatch-error'."""
        import contextlib

        pf = _make_pf(early_exit=True, rerun_dispatch_error=True)
        mock_plan_json = MagicMock()
        with contextlib.ExitStack() as stack:
            _common_patches(stack, pf)
            stack.enter_context(
                patch(f"{_SM}._plan_json_set_state_sm", mock_plan_json)
            )
            stack.enter_context(patch(f"{_SM}._sprint_db_set_state_sm"))
            with pytest.raises(SystemExit):
                run_sprint(
                    label=_LABEL,
                    skip_gates=False,
                    gate_pytest=True,
                    gate_lint=True,
                    gate_merge_preview=True,
                    alert_modes=[AlertMode.DASHBOARD_BANNER],
                    repo_name=_REPO,
                    dry_run=True,
                )

        _, kwargs = mock_plan_json.call_args
        assert kwargs.get("end_reason") == "rerun-dispatch-error", (
            "end_reason must be 'rerun-dispatch-error', "
            f"got {kwargs.get('end_reason')!r}"
        )

    def test_db_set_needs_rework(self, tmp_path):
        """_sprint_db_set_state_sm also called with needs_rework so DB is consistent."""
        import contextlib

        pf = _make_pf(early_exit=True, rerun_dispatch_error=True)
        mock_db = MagicMock()
        with contextlib.ExitStack() as stack:
            _common_patches(stack, pf)
            stack.enter_context(patch(f"{_SM}._plan_json_set_state_sm"))
            stack.enter_context(patch(f"{_SM}._sprint_db_set_state_sm", mock_db))
            with pytest.raises(SystemExit):
                run_sprint(
                    label=_LABEL,
                    skip_gates=False,
                    gate_pytest=True,
                    gate_lint=True,
                    gate_merge_preview=True,
                    alert_modes=[AlertMode.DASHBOARD_BANNER],
                    repo_name=_REPO,
                    dry_run=True,
                )

        assert mock_db.called, "_sprint_db_set_state_sm must be called"
        args, kwargs = mock_db.call_args
        assert args[1] == "needs_rework", (
            f"_sprint_db_set_state_sm must be called with state='needs_rework', "
            f"got {args[1]!r}"
        )
        assert kwargs.get("end_reason") == "rerun-dispatch-error", (
            f"end_reason must be 'rerun-dispatch-error', got {kwargs.get('end_reason')!r}"
        )


# ── AC-3: normal early_exit (no dispatch error) still returns normally ─────────

class TestNormalEarlyExitUnchanged:
    """AC-3: A plain early_exit without rerun_dispatch_error must still return
    (summary, state) — the fix must not change non-error early exits."""

    def test_normal_early_exit_returns_tuple(self, tmp_path):
        """run_sprint returns (summary, state) for a plain early_exit=True."""
        import contextlib

        pf = _make_pf(early_exit=True, rerun_dispatch_error=False)
        mock_plan_json = MagicMock()
        with contextlib.ExitStack() as stack:
            _common_patches(stack, pf)
            stack.enter_context(
                patch(f"{_SM}._plan_json_set_state_sm", mock_plan_json)
            )
            stack.enter_context(patch(f"{_SM}._sprint_db_set_state_sm"))
            result = run_sprint(
                label=_LABEL,
                skip_gates=False,
                gate_pytest=True,
                gate_lint=True,
                gate_merge_preview=True,
                alert_modes=[AlertMode.DASHBOARD_BANNER],
                repo_name=_REPO,
                dry_run=True,
            )

        assert isinstance(result, tuple) and len(result) == 2, (
            "Normal early_exit must return (summary, state) tuple, "
            f"got {type(result)}"
        )

    def test_normal_early_exit_does_not_call_plan_json(self, tmp_path):
        """Normal early_exit must NOT call _plan_json_set_state_sm (no state write)."""
        import contextlib

        pf = _make_pf(early_exit=True, rerun_dispatch_error=False)
        mock_plan_json = MagicMock()
        with contextlib.ExitStack() as stack:
            _common_patches(stack, pf)
            stack.enter_context(
                patch(f"{_SM}._plan_json_set_state_sm", mock_plan_json)
            )
            stack.enter_context(patch(f"{_SM}._sprint_db_set_state_sm"))
            run_sprint(
                label=_LABEL,
                skip_gates=False,
                gate_pytest=True,
                gate_lint=True,
                gate_merge_preview=True,
                alert_modes=[AlertMode.DASHBOARD_BANNER],
                repo_name=_REPO,
                dry_run=True,
            )

        mock_plan_json.assert_not_called()
