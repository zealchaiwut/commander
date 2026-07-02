"""Tests for issue #754: enforce label lock during a sprint run (RUN_MUTABLE_LABELS).

Each test maps to an acceptance criterion:

- AC1: RUN_MUTABLE_LABELS == STATUS_LABELS, defined in state_machine.py
- AC2: state_machine.transition() raises TransitionError when
       COMMANDER_SPRINT_RUNNING=1 and the add/remove sets are not a subset of
       RUN_MUTABLE_LABELS
- AC3: sprint_manager.py sets COMMANDER_SPRINT_RUNNING=1 in its own process and
       propagates it to dispatched agent subprocesses (os.environ.copy())
- AC4: sprint_manager.py clears COMMANDER_SPRINT_RUNNING on exit, via atexit and
       signal handler paths
- AC5: scripts that mutate non-status labels (sprint assignment, delete-sprint
       strip) refuse with a clear error when COMMANDER_SPRINT_RUNNING=1
- AC6: with COMMANDER_SPRINT_RUNNING unset, all existing label mutation behavior
       is unchanged
- AC7: unit tests cover both locked mode (non-status change → error) and unlocked
       mode (same change → succeeds)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

from services.sprint_manager.state_machine import (
    RUN_MUTABLE_LABELS,
    STATUS_LABELS,
    TicketState,
    TransitionError,
    assert_run_mutable,
    run_lock_active,
    transition,
)

_FAKE_REPO = "zealchaiwut/commander"
_FAKE_ISSUE = 999
_ENV = "COMMANDER_SPRINT_RUNNING"


@pytest.fixture
def locked(monkeypatch):
    """Sprint run lock held."""
    monkeypatch.setenv(_ENV, "1")


@pytest.fixture
def unlocked(monkeypatch):
    """Sprint run lock not held."""
    monkeypatch.delenv(_ENV, raising=False)


def _label_response(*names: str) -> MagicMock:
    m = MagicMock()
    m.returncode = 0
    m.stdout = json.dumps({"labels": [{"name": n} for n in names]})
    m.stderr = ""
    return m


def _edit_ok() -> MagicMock:
    m = MagicMock()
    m.returncode = 0
    m.stdout = ""
    m.stderr = ""
    return m


# ── AC1: constant ───────────────────────────────────────────────────────────────

class TestRunMutableLabelsConstant:
    def test_run_mutable_labels_equals_status_labels(self):
        assert RUN_MUTABLE_LABELS == STATUS_LABELS

    def test_run_mutable_excludes_sprint_label(self):
        assert "sprint-57" not in RUN_MUTABLE_LABELS

    def test_run_mutable_excludes_estimated_label(self):
        assert "estimated" not in RUN_MUTABLE_LABELS

    def test_run_mutable_includes_every_status_label(self):
        for lbl in ("backlog", "in-progress", "SIT", "UAT", "needs-rework", "blocked"):
            assert lbl in RUN_MUTABLE_LABELS


# ── run_lock_active helper ──────────────────────────────────────────────────────

class TestRunLockActive:
    def test_active_when_env_is_one(self, locked):
        assert run_lock_active() is True

    def test_inactive_when_unset(self, unlocked):
        assert run_lock_active() is False

    def test_active_when_env_is_zero(self, monkeypatch):
        # issue #1689: the guard now treats any non-empty value as locked
        # (matching scripts/update_ticket.py's `if sprint_label := os.environ
        # .get(...)` truthiness, where "0" is a non-empty — hence truthy —
        # string). Nothing in production ever sets this var to "0"; it is
        # either unset, "1" (orchestrator's own process), or a sprint label
        # (subprocess env).
        monkeypatch.setenv(_ENV, "0")
        assert run_lock_active() is True

    def test_inactive_when_env_is_empty_string(self, monkeypatch):
        monkeypatch.setenv(_ENV, "")
        assert run_lock_active() is False


# ── AC2 / AC7: assert_run_mutable + transition under the lock ───────────────────

class TestAssertRunMutable:
    def test_locked_non_status_label_raises(self, locked):
        # AC7 locked mode: a non-status label in the diff → TransitionError
        with pytest.raises(TransitionError):
            assert_run_mutable({"sprint-57"}, set())

    def test_locked_non_status_in_remove_raises(self, locked):
        with pytest.raises(TransitionError):
            assert_run_mutable(set(), {"estimated"})

    def test_locked_error_message_names_offending_label(self, locked):
        with pytest.raises(TransitionError, match="sprint-57"):
            assert_run_mutable({"sprint-57"}, set())

    def test_locked_status_only_change_allowed(self, locked):
        # Status-only diff is permitted even while the lock is held.
        assert_run_mutable({"SIT"}, {"in-progress"})  # no raise

    def test_unlocked_non_status_change_is_noop(self, unlocked):
        # AC7 unlocked mode: the same non-status change succeeds (no raise).
        assert_run_mutable({"sprint-57"}, {"estimated"})  # no raise


class TestTransitionUnderLock:
    def _patched(self, fetch_labels):
        """Patch subprocess so fetch returns fetch_labels, edits succeed."""
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            if "view" in cmd:
                return _label_response(*fetch_labels)
            return _edit_ok()

        return patch(
            "services.sprint_manager.state_machine.subprocess.run",
            side_effect=fake_run,
        ), calls

    def test_status_transition_succeeds_while_locked(self, locked):
        # AC2 boundary: transition only moves status labels, so it is a subset of
        # RUN_MUTABLE_LABELS and must still succeed under the lock.
        ctx, calls = self._patched(["in-progress", "sprint-57", "estimated"])
        with ctx, patch("services.sprint_manager.state_machine.time"), \
             patch("services.sprint_manager.state_machine._write_ticket_status"):
            result = transition(
                _FAKE_ISSUE, TicketState.SIT, actor="test", repo=_FAKE_REPO
            )
        assert result is True
        edits = [c for c in calls if "edit" in c]
        assert len(edits) == 1
        # Only status labels touched; sprint-57 / estimated never appear in edit.
        flat = " ".join(edits[0])
        assert "sprint-57" not in flat
        assert "estimated" not in flat

    def test_transition_calls_assert_run_mutable(self, locked):
        # AC2: transition() routes its computed diff through the run-lock guard.
        ctx, _ = self._patched(["in-progress"])
        with ctx, patch("services.sprint_manager.state_machine.time"), \
             patch("services.sprint_manager.state_machine._write_ticket_status"), \
             patch(
                 "services.sprint_manager.state_machine.assert_run_mutable"
             ) as guard:
            transition(_FAKE_ISSUE, TicketState.SIT, actor="test", repo=_FAKE_REPO)
        guard.assert_called_once()

    def test_transition_raises_when_diff_not_subset(self, locked):
        # AC2 directly: if the computed diff includes a non-status label, the
        # guard raises TransitionError. We force a non-status label into the diff
        # by patching STATE_LABELS for the target state.
        import services.sprint_manager.state_machine as sm
        ctx, _ = self._patched(["in-progress"])
        bad = {sm.TicketState.SIT: frozenset({"SIT", "sprint-57"})}
        with ctx, patch("services.sprint_manager.state_machine.time"), \
             patch.dict(sm.STATE_LABELS, bad):
            with pytest.raises(TransitionError, match="sprint-57"):
                transition(_FAKE_ISSUE, sm.TicketState.SIT, actor="t", repo=_FAKE_REPO)


# ── AC3 / AC4: sprint_manager sets and clears the lock ──────────────────────────

class TestSprintManagerLockLifecycle:
    def _sm(self):
        import services.sprint_manager.sprint_manager as sm
        return sm

    def test_setup_sets_env(self, unlocked, tmp_path, monkeypatch):
        sm = self._sm()
        monkeypatch.setattr(sm, "SPRINTS_DIR", tmp_path)
        try:
            sm._setup_pid_file(57)
            assert os.environ.get(_ENV) == "1"
        finally:
            sm._remove_pid_file()

    def test_remove_clears_env(self, locked, tmp_path, monkeypatch):
        sm = self._sm()
        monkeypatch.setattr(sm, "SPRINTS_DIR", tmp_path)
        sm._setup_pid_file(57)
        sm._remove_pid_file()
        assert _ENV not in os.environ

    def test_setup_sets_env_even_when_sprint_num_none(self, unlocked):
        # AC4: the lock must still be cleared on exit even with no PID file, so it
        # must be set (and cleanup registered) before the sprint_num guard.
        sm = self._sm()
        try:
            sm._setup_pid_file(None)
            assert os.environ.get(_ENV) == "1"
        finally:
            sm._remove_pid_file()

    def test_subprocess_env_inherits_lock(self, unlocked, tmp_path, monkeypatch):
        # AC3: dispatched agents inherit the lock because dispatch builds env from
        # os.environ.copy(); prove the var is present in that copy.
        sm = self._sm()
        monkeypatch.setattr(sm, "SPRINTS_DIR", tmp_path)
        try:
            sm._setup_pid_file(57)
            sub_env = os.environ.copy()
            assert sub_env.get(_ENV) == "1"
        finally:
            sm._remove_pid_file()

    def test_remove_registered_with_atexit(self):
        # AC4: cleanup runs on the atexit path.
        src = (
            REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"
        ).read_text()
        assert "atexit.register(_remove_pid_file)" in src

    def test_signal_handler_calls_remove(self):
        # AC4: cleanup runs on the signal path (SIGTERM/SIGINT).
        src = (
            REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"
        ).read_text()
        assert "signal.signal(signal.SIGTERM, _sig_handler)" in src
        assert "signal.signal(signal.SIGINT, _sig_handler)" in src

    def test_remove_pops_the_lock_env(self):
        # AC4: _remove_pid_file (the atexit + signal callback) clears the lock.
        src = (
            REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"
        ).read_text()
        assert 'os.environ.pop("COMMANDER_SPRINT_RUNNING", None)' in src


# ── AC5 / AC6: sprint-label mutation scripts refuse while running ───────────────

class TestSprintLabelMutationGuard:
    def _gc(self):
        import github_client as gc
        return gc

    def test_assign_sprint_refuses_while_running(self, locked):
        gc = self._gc()
        with pytest.raises(gc.SprintRunLockError):
            gc.assign_sprint(123, 57, repo_name=_FAKE_REPO)

    def test_assign_sprint_by_label_refuses_while_running(self, locked):
        gc = self._gc()
        with pytest.raises(gc.SprintRunLockError):
            gc.assign_sprint_by_label(123, "sprint-57", repo_name=_FAKE_REPO)

    def test_delete_sprint_strip_refuses_while_running(self, locked):
        # delete-sprint label strip = assign with None
        gc = self._gc()
        with pytest.raises(gc.SprintRunLockError):
            gc.assign_sprint_by_label(123, None, repo_name=_FAKE_REPO)

    def test_refuse_message_is_clear(self, locked):
        gc = self._gc()
        with pytest.raises(gc.SprintRunLockError, match="COMMANDER_SPRINT_RUNNING="):
            gc.assign_sprint(123, 57, repo_name=_FAKE_REPO)

    def test_assign_sprint_proceeds_when_unlocked(self, unlocked):
        # AC6: with the lock unset the function runs its normal body unchanged
        # (it gets past the guard and issues the gh edit).
        gc = self._gc()
        with patch.object(gc, "get_issue", return_value={"labels": []}), \
             patch.object(gc, "ensure_sprint_label"), \
             patch.object(gc, "_run") as run, \
             patch.object(gc, "invalidate"), \
             patch.object(gc, "update_labels") as upd:
            gc.assign_sprint(123, 57, repo_name=_FAKE_REPO)
        # normal path executed: either update_labels or _run was used, no raise
        assert upd.called or run.called

    def test_assign_by_label_proceeds_when_unlocked(self, unlocked):
        gc = self._gc()
        with patch.object(gc, "get_issue", return_value={"labels": []}), \
             patch.object(gc, "_run") as run, \
             patch.object(gc, "invalidate"):
            gc.assign_sprint_by_label(123, "sprint-57", repo_name=_FAKE_REPO)
        assert run.called
