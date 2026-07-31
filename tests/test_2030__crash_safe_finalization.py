"""Behavioral tests for issue #2030 — Non-fatal sprint finalization on hard crash.

AC-1  Wrap the sprint-end documenter + worktree-pool acquisition so a RuntimeError
      marks the sprint needs_rework and STILL runs sprint PR / reviewer / reconcile
      and writes a terminal lifecycle state.
AC-2  A sprint is never left stuck "running" after a hard crash — it always reaches
      a terminal state (ready_to_merge or needs_rework).
AC-3  Behavioral tests: inject a documenter/worktree RuntimeError → assert a
      terminal state was written AND downstream steps still ran.

All tests use mocked boundaries and assert observed behavior, not source-text
patterns.  Per CLAUDE.md issue #1746.

Git-isolation guarantee
-----------------------
Every test in this module is guarded by the ``git_no_mutation`` autouse fixture.
That fixture records ``git rev-parse HEAD`` before each test and asserts it is
identical afterward.  Any test that inadvertently lets the sprint-end machinery
run ``git commit`` or ``git add`` against the repo will FAIL with a clear message
showing the before/after SHAs.

The root cause that motivated this guard: ``_generate_code_state_snapshot``
(and ``_write_sprint_brief``) were not stubbed in the first revision of the test
file.  Because ``_CODE_STATE_AVAILABLE`` is True in this repo, the snapshot step
ran for real on every documenter-crash test and committed a new
``docs/architecture/code-state.md`` revision, leaving 3 junk commits per run.
Both functions are now stubbed in ``_run_main_with_patches``.

How these tests FAIL against pre-fix code
------------------------------------------
TestRunSprintCrashTerminalState
    Before the fix, the ``except Exception as _crash_exc`` block ends with
    ``raise``, which re-raises the RuntimeError out of main().  The test would
    see RuntimeError propagate instead of SystemExit(1), making
    pytest.raises(SystemExit) fail.  Even if the exit were somehow intercepted,
    'needs_rework' would never appear in written_plan_states because the
    terminal-state block (after the try/except) is never reached.

TestDocumenterCrashDownstreamSteps
    Before the fix, the ``except RuntimeError as e_doc`` block ends with
    ``raise``, which re-raises the RuntimeError.  The reviewer block
    (after the documenter) is never reached, so reviewer_called stays False,
    failing the assertion.  Similarly, needs_rework is never written by the
    documenter except block (the call wasn't there), so the terminal state
    remains whatever was written from ticket outcomes (possibly ready_to_merge),
    not needs_rework from the documenter crash.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).parent.parent
for _p in (
    str(REPO_ROOT),
    str(REPO_ROOT / "apps" / "dashboard"),
    str(REPO_ROOT / "services" / "sprint_manager"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "apps" / "dashboard" / "commander.db"))

# Stub github_client before any sprint_manager imports.
if "github_client" not in sys.modules:
    sys.modules["github_client"] = MagicMock()  # type: ignore[assignment]

from services.sprint_manager.state import (  # noqa: E402
    IssueState,
    SprintState,
    SprintSummary,
)


# ── git-isolation guard ───────────────────────────────────────────────────────

def _git_head_sha() -> str:
    """Return current HEAD SHA for the working repo."""
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=str(REPO_ROOT),
        text=True,
    ).strip()


@pytest.fixture(autouse=True)
def git_no_mutation():
    """Assert that no test in this module commits to the repository.

    Records ``git rev-parse HEAD`` before each test and asserts it is
    unchanged afterward.  If HEAD moved, the fixture fails loudly with the
    before/after SHAs so the offending test is immediately obvious.

    This guard catches any future regression where a code-path in the sprint
    finalization machinery (e.g. _generate_code_state_snapshot, _write_sprint_brief,
    or any other git-touching step) is accidentally left unmocked.
    """
    sha_before = _git_head_sha()
    yield
    sha_after = _git_head_sha()
    assert sha_before == sha_after, (
        f"Test mutated the git repository!\n"
        f"  HEAD before: {sha_before}\n"
        f"  HEAD after:  {sha_after}\n"
        "An unmocked code path ran 'git commit' or 'git add'.\n"
        "Ensure _generate_code_state_snapshot and _write_sprint_brief are\n"
        "stubbed in _run_main_with_patches."
    )


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_clean_state() -> tuple[SprintSummary, SprintState]:
    """Minimal sprint outcome: one ticket that merged cleanly."""
    issues = [IssueState(number=1, title="Ticket 1", status="done")]
    state = SprintState(sprint_label="sprint-99", sprint_number=99, issues=issues)
    summary = SprintSummary(processed=["#1"], merged=["#1"])
    return summary, state


def _run_main_with_patches(monkeypatch, tmp_path: Path, *, extra_patches: dict) -> None:
    """Call sm.main() after applying the minimal complete set of boundary mocks.

    Callers pass ``extra_patches`` to override specific behaviours
    (e.g. run_sprint crashing, documenter crashing).

    IMPORTANT — every git-touching sprint-end step is stubbed here so no test
    in this module can accidentally commit to the repository:

      _generate_code_state_snapshot — writes docs/architecture/code-state.md
        and makes a git commit; _CODE_STATE_AVAILABLE is True in this repo so
        without this stub it runs for real on every documenter-crash test.

      _write_sprint_brief — may write docs/sprint-briefs/* and commit; default-
        disabled via COMMANDER_DISABLE_BRIEF=1 in the environment, but stubbed
        here defensively.

      SprintState.save — writes a JSON state file to disk; stubbed to prevent
        spurious file creation in tmp_path siblings.
    """
    import services.sprint_manager.sprint_manager as sm

    monkeypatch.setattr(sys, "argv", ["sm", "sprint-99"])
    monkeypatch.setattr(sm, "install_orchestrator_stdout_timestamps", lambda: None)
    monkeypatch.setattr(sm, "discover_config", lambda: None)
    monkeypatch.setattr(sm, "_default_config", lambda: None)
    monkeypatch.setattr(sm, "_acquire_pid_lock",
                        lambda *a, **kw: tmp_path / "test.pid")
    monkeypatch.setattr(sm, "_release_pid_lock", lambda *a, **kw: None)
    monkeypatch.setattr(sm, "_regenerate_status_md", lambda **kw: None)
    monkeypatch.setattr(sm, "_sprint_db_ingest_run_sm", lambda *a, **kw: None)
    monkeypatch.setattr(sm, "_db_agent_start_sm", lambda *a, **kw: None)
    monkeypatch.setattr(sm, "_db_agent_finish_sm", lambda *a, **kw: None)
    monkeypatch.setattr(sm, "_state_path",
                        lambda *a, **kw: tmp_path / "sprint-99-state.json")

    # ── git-touching sprint-end steps: MUST be stubbed to keep the repo clean ──
    # _generate_code_state_snapshot shells out to git-add + git-commit under
    # docs/architecture/code-state.md.  Without this mock, every documenter-crash
    # test commits a new code-state revision to the branch (_CODE_STATE_AVAILABLE
    # is True in this repo).
    monkeypatch.setattr(sm, "_generate_code_state_snapshot", lambda **kw: None)
    # _write_sprint_brief may also commit to docs/sprint-briefs/; stub it
    # defensively even though COMMANDER_DISABLE_BRIEF=1 normally skips it.
    monkeypatch.setattr(sm, "_write_sprint_brief", lambda **kw: None)
    # SprintState.save writes a JSON file; stub it to avoid tmp_path side-effects.
    monkeypatch.setattr(sm.SprintState, "save", lambda self, p: None)

    for attr, val in extra_patches.items():
        monkeypatch.setattr(sm, attr, val)

    sm.main()


# ── AC-2 / AC-3: run_sprint() hard crash ─────────────────────────────────────

class TestRunSprintCrashTerminalState:
    """AC-2/AC-3a: RuntimeError inside run_sprint → needs_rework terminal state.

    Simulates the sprint-1003 scenario where 7/7 tickets crashed, leaving the
    sprint stuck "running" forever.  The worktree-pool acquisition raises
    RuntimeError mid-run; that propagates out of run_sprint() and was previously
    re-raised by the crash catch-all in main(), skipping the terminal-state write.
    """

    def test_crash_writes_needs_rework_plan_state(self, monkeypatch, tmp_path):
        """AC-2: plan state is written as needs_rework after run_sprint crash.

        Pre-fix failure: RuntimeError propagates out of main() → pytest.raises(SystemExit)
        fails because the wrong exception type is raised.  And needs_rework is never
        written to written_plan_states because the terminal-state block is never reached.
        """
        written_plan_states: list[str] = []

        def _crash_run_sprint(**kw):
            raise RuntimeError("worktree pool slot missing")

        with pytest.raises(SystemExit) as exc_info:
            _run_main_with_patches(
                monkeypatch, tmp_path,
                extra_patches={
                    "run_sprint": _crash_run_sprint,
                    "_plan_json_set_state_sm": lambda label, state, **kw:
                        written_plan_states.append(state),
                    "_sprint_db_set_state_sm": lambda *a, **kw: None,
                },
            )

        assert exc_info.value.code == 1, (
            "crashed sprint must exit with code 1; "
            f"got code={exc_info.value.code}"
        )
        assert "needs_rework" in written_plan_states, (
            "terminal state must be written as needs_rework when run_sprint crashes; "
            f"got written states: {written_plan_states}"
        )

    def test_crash_writes_needs_rework_db_state(self, monkeypatch, tmp_path):
        """AC-2: DB state is written as needs_rework after run_sprint crash.

        Pre-fix failure: same as above — RuntimeError re-raises, DB write skipped.
        """
        written_db_states: list[str] = []

        def _crash_run_sprint(**kw):
            raise RuntimeError("worktree pool slot missing")

        with pytest.raises(SystemExit) as exc_info:
            _run_main_with_patches(
                monkeypatch, tmp_path,
                extra_patches={
                    "run_sprint": _crash_run_sprint,
                    "_plan_json_set_state_sm": lambda *a, **kw: None,
                    "_sprint_db_set_state_sm": lambda label, state, **kw:
                        written_db_states.append(state),
                },
            )

        assert exc_info.value.code == 1
        assert "needs_rework" in written_db_states, (
            "DB terminal state must be needs_rework after run_sprint crash; "
            f"got: {written_db_states}"
        )

    def test_crash_end_reason_is_hard_crash(self, monkeypatch, tmp_path):
        """AC-2: end_reason='hard-crash' distinguishes programmatic crashes from ticket failures.

        Pre-fix failure: terminal-state block never reached → end_reason never set.
        """
        captured_kwargs: list[dict] = []

        def _capture_plan(label, state, **kw):
            if state == "needs_rework":
                captured_kwargs.append(kw)

        def _crash_run_sprint(**kw):
            raise RuntimeError("worktree pool slot missing")

        with pytest.raises(SystemExit):
            _run_main_with_patches(
                monkeypatch, tmp_path,
                extra_patches={
                    "run_sprint": _crash_run_sprint,
                    "_plan_json_set_state_sm": _capture_plan,
                    "_sprint_db_set_state_sm": lambda *a, **kw: None,
                },
            )

        assert any(kw.get("end_reason") == "hard-crash" for kw in captured_kwargs), (
            "end_reason must be 'hard-crash' on a hard crash; "
            f"got captured kwargs: {captured_kwargs}"
        )

    def test_sprint_crashed_event_still_emitted(self, monkeypatch, tmp_path):
        """Hard rule: diagnostic sprint_crashed event must still be emitted.

        Pre-fix: sprint_crashed WAS emitted (the log was there), then re-raised.
        Post-fix: sprint_crashed is still emitted, then falls through.
        This test verifies the fix does not hide the crash.
        """
        crash_events: list[str] = []

        def _crash_run_sprint(**kw):
            raise RuntimeError("worktree pool slot missing")

        with pytest.raises(SystemExit):
            _run_main_with_patches(
                monkeypatch, tmp_path,
                extra_patches={
                    "run_sprint": _crash_run_sprint,
                    "_plan_json_set_state_sm": lambda *a, **kw: None,
                    "_sprint_db_set_state_sm": lambda *a, **kw: None,
                    "structured_log": MagicMock(
                        error=lambda name, *a, **kw: crash_events.append(name),
                        warn=lambda *a, **kw: None,
                    ),
                },
            )

        assert "sprint_crashed" in crash_events, (
            "sprint_crashed event must be emitted even when the crash is handled; "
            f"got events: {crash_events}"
        )


# ── AC-1 / AC-3: documenter RuntimeError ─────────────────────────────────────

class TestDocumenterCrashDownstreamSteps:
    """AC-1/AC-3b: documenter RuntimeError → needs_rework AND reviewer still runs.

    The documenter re-raises RuntimeError by design (AC6 of issue #165).
    Before issue #2030 that re-raise skipped sprint PR / reviewer / reconcile
    and left those post-sprint steps permanently skipped for that run.
    """

    def test_documenter_crash_writes_needs_rework(self, monkeypatch, tmp_path):
        """AC-1: documenter RuntimeError overwrites terminal state to needs_rework.

        Pre-fix failure: the except RuntimeError block re-raises, so the
        _plan_json_set_state_sm('needs_rework', end_reason='documenter-crash')
        call inside the block is never reached.  The initial terminal state
        (ready_to_merge from clean ticket run) remains, and 'documenter-crash'
        never appears.
        """
        mock_summary, mock_state = _make_clean_state()
        written_plan_states: list[str] = []

        _run_main_with_patches(
            monkeypatch, tmp_path,
            extra_patches={
                "run_sprint": lambda **kw: (mock_summary, mock_state),
                "write_sprint_summary": lambda **kw: None,
                "_dispatch_documenter": _make_doc_crash(),
                "_dispatch_reviewer": lambda **kw: None,
                "_plan_json_set_state_sm": lambda label, state, **kw:
                    written_plan_states.append(state),
                "_sprint_db_set_state_sm": lambda *a, **kw: None,
            },
        )

        assert "needs_rework" in written_plan_states, (
            "terminal state must be overwritten to needs_rework after documenter crash; "
            f"got plan states: {written_plan_states}"
        )

    def test_documenter_crash_reviewer_still_runs(self, monkeypatch, tmp_path):
        """AC-1: reviewer still runs after documenter RuntimeError.

        Pre-fix failure: RuntimeError re-raises from the documenter except block,
        causing the reviewer block to be skipped entirely.  reviewer_calls stays
        empty, failing the assertion.
        """
        mock_summary, mock_state = _make_clean_state()
        reviewer_calls: list[bool] = []

        _run_main_with_patches(
            monkeypatch, tmp_path,
            extra_patches={
                "run_sprint": lambda **kw: (mock_summary, mock_state),
                "write_sprint_summary": lambda **kw: None,
                "_dispatch_documenter": _make_doc_crash(),
                "_dispatch_reviewer": lambda **kw: reviewer_calls.append(True),
                "_plan_json_set_state_sm": lambda *a, **kw: None,
                "_sprint_db_set_state_sm": lambda *a, **kw: None,
            },
        )

        assert reviewer_calls, (
            "_dispatch_reviewer must be called even after documenter RuntimeError; "
            "downstream steps must still run (AC-1)"
        )

    def test_documenter_crash_end_reason_is_documenter_crash(self, monkeypatch, tmp_path):
        """AC-1: end_reason='documenter-crash' distinguishes documenter failures.

        Pre-fix failure: the end_reason kwarg is never passed because the
        _plan_json_set_state_sm call inside the except block was not there.
        """
        mock_summary, mock_state = _make_clean_state()
        captured_kwargs: list[dict] = []

        def _capture_plan(label, state, **kw):
            if state == "needs_rework":
                captured_kwargs.append(kw)

        _run_main_with_patches(
            monkeypatch, tmp_path,
            extra_patches={
                "run_sprint": lambda **kw: (mock_summary, mock_state),
                "write_sprint_summary": lambda **kw: None,
                "_dispatch_documenter": _make_doc_crash(),
                "_dispatch_reviewer": lambda **kw: None,
                "_plan_json_set_state_sm": _capture_plan,
                "_sprint_db_set_state_sm": lambda *a, **kw: None,
            },
        )

        assert any(kw.get("end_reason") == "documenter-crash" for kw in captured_kwargs), (
            "end_reason must be 'documenter-crash' when documenter raises RuntimeError; "
            f"got: {captured_kwargs}"
        )


# ── helpers for documenter crash ──────────────────────────────────────────────

def _make_doc_crash():
    """Return a callable that raises RuntimeError, simulating documenter failure."""
    def _crash(**kw):
        raise RuntimeError("documenter agent timed out")
    return _crash
