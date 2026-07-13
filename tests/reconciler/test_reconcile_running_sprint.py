"""Tests for issue #1088 — reconciler must not overwrite running sprint state.

AC1: _github_reconcile_row checks manager PID liveness before writing any state change
AC2: running + live PID → reconciler returns None (no patch proposed)
AC3: running + dead PID + _has_rework_tickets True → transition to needs_rework or ready_to_merge
AC4: all reconciler writes go through transition_sprint_state; a confirmed-orphan
     running->terminal settle uses actor="manager" (db.py's edge guard requires it
     for that specific edge — actor="reconcile" was a silent no-op here for both
     the sweep AND the per-sprint button until issue #1697). Terminal<->terminal
     reconcile transitions (promotion/demotion) still use actor="reconcile".
AC5: guard at line 33 no longer treats running as eligible for re-derivation when PID is alive
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO_ROOT = Path(__file__).parent.parent.parent
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_DASHBOARD_ROOT))

os.environ.setdefault("DB_PATH", str(_REPO_ROOT / "commander.db"))

import db as _db_module  # noqa: E402
from routers import sprint_reconcile_service as _srs  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path):
    db_file = tmp_path / "test_1088.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


@pytest.fixture
def pid_dir(tmp_path):
    d = tmp_path / ".commander" / "sprints"
    d.mkdir(parents=True)
    return d


def _pid_mock(pid_dir: Path):
    """Return a side_effect that redirects _manager_pid_file to pid_dir."""
    def _mock(label: str, project: str = "") -> Path:
        return pid_dir / f"{label}-pid"
    return _mock


# ─── AC2: live PID → no patch ────────────────────────────────────────────────

def test_live_pid_no_patch(fresh_db, pid_dir):
    """Running sprint with a live manager PID must not produce any reconciler patch.

    UAT step 1: pytest tests/reconciler/test_reconcile_running_sprint.py::test_live_pid_no_patch
    Expected: green — reconciler returns None, sprint state stays 'running'.
    """
    label = "sprint-74.1"
    fresh_db.record_sprint_start(label, project="o/r")
    row = fresh_db.get_sprint(label)
    assert row["state"] == "running"

    pid_path = pid_dir / f"{label}-pid"
    pid_path.write_text(str(os.getpid()))  # current process — guaranteed alive

    with patch.object(_srs, "_manager_pid_file", side_effect=_pid_mock(pid_dir)):
        result = _srs._github_reconcile_row(label, "o/r", row)

    assert result is None, "Running sprint with live manager PID must not get a patch"


# ─── AC3: dead PID + rework tickets → transition ─────────────────────────────

def test_dead_pid_settles_state(fresh_db, pid_dir):
    """Running sprint with a dead manager PID and rework tickets transitions to needs_rework.

    UAT step 2: pytest tests/reconciler/test_reconcile_running_sprint.py::test_dead_pid_settles_state
    Expected: green — reconciler transitions via transition_sprint_state(actor='reconcile').
    """
    label = "sprint-74.2"
    fresh_db.record_sprint_start(label, project="o/r")

    dead_pid = 2 ** 22  # extremely large PID, virtually guaranteed not alive on any system
    pid_path = pid_dir / f"{label}-pid"
    pid_path.write_text(str(dead_pid))

    transition_calls: list[dict] = []

    def _capture(lbl: str, state: str, actor: str, end_reason: str | None = None, project: str = "") -> bool:
        transition_calls.append({"label": lbl, "state": state, "actor": actor})
        # actually apply the transition so downstream assertions can check DB state
        if state == "needs_rework":
            fresh_db.record_sprint_needs_rework(lbl, end_reason=end_reason, project=project)
        elif state == "ready_to_merge":
            fresh_db.record_sprint_ready_to_merge(lbl, end_reason=end_reason, project=project)
        return True

    import server as srv  # noqa: PLC0415

    with (
        patch.object(_srs, "_manager_pid_file", side_effect=_pid_mock(pid_dir)),
        patch.object(_srs, "transition_sprint_state", side_effect=_capture),
        patch.object(srv, "_has_rework_tickets", return_value=True),
    ):
        updated = _srs.reconcile_sprint_label(label, "o/r")

    assert updated, "reconcile_sprint_label must return True when state changes"
    assert len(transition_calls) == 1, "transition_sprint_state must be called exactly once"
    # issue #1697: running->terminal requires actor="manager" per db.py's edge
    # guard (_GUARD_FROM={"running"}) — actor="reconcile" here would always be
    # rejected, silently no-opping every orphan settle.
    assert transition_calls[0]["actor"] == "manager", (
        "a confirmed-orphan running->terminal settle must use actor='manager' "
        "to satisfy db.py's edge guard for that transition"
    )
    assert transition_calls[0]["state"] in (
        "needs_rework", "ready_to_merge"
    ), "State must settle to needs_rework or ready_to_merge"


# ─── AC4: all writes through transition_sprint_state ─────────────────────────

def test_reconcile_uses_transition_sprint_state_not_direct_db(fresh_db, pid_dir):
    """reconcile_sprint_label must route all DB writes through transition_sprint_state.

    Verifies AC4: direct record_sprint_needs_rework / record_sprint_ready_to_merge
    calls are removed from reconcile_sprint_label; only transition_sprint_state is used.
    """
    label = "sprint-74.3"
    fresh_db.record_sprint_start(label, project="o/r")

    pid_path = pid_dir / f"{label}-pid"
    pid_path.write_text("99999999")  # dead PID

    # Track calls to transition_sprint_state
    transition_calls: list[str] = []
    original_transition = _srs.transition_sprint_state

    def _spy(lbl, state, actor, end_reason=None, project=""):
        transition_calls.append(actor)
        return original_transition(lbl, state, actor, end_reason, project)

    import server as srv  # noqa: PLC0415

    with (
        patch.object(_srs, "_manager_pid_file", side_effect=_pid_mock(pid_dir)),
        patch.object(_srs, "transition_sprint_state", side_effect=_spy),
        patch.object(srv, "_has_rework_tickets", return_value=False),
    ):
        _srs.reconcile_sprint_label(label, "o/r")

    # issue #1697: this is the confirmed-orphan (dead PID) running->terminal
    # case, which requires actor="manager" to satisfy db.py's edge guard —
    # see the AC4 docstring update above. The point of this test (no direct
    # db.record_* calls) is unchanged; only the actor value for this specific
    # scenario is corrected.
    assert "manager" in transition_calls, (
        "reconcile_sprint_label must call transition_sprint_state (actor='manager' "
        "for this confirmed-orphan running->terminal case)"
    )


# ─── AC5: guard no longer eligible when PID is alive ─────────────────────────

def test_running_ineligible_when_pid_alive(fresh_db, pid_dir):
    """With a live PID, _github_reconcile_row exits early — running is not re-derived.

    Verifies AC5: the guard at line 33 no longer passes 'running' through when PID is alive.
    """
    label = "sprint-74.4"
    fresh_db.record_sprint_start(label, project="o/r")
    row = fresh_db.get_sprint(label)

    pid_path = pid_dir / f"{label}-pid"
    pid_path.write_text(str(os.getpid()))

    # Even with rework tickets, a live PID must block all patch proposals
    import server as srv  # noqa: PLC0415

    with (
        patch.object(_srs, "_manager_pid_file", side_effect=_pid_mock(pid_dir)),
        patch.object(srv, "_has_rework_tickets", return_value=True),
    ):
        result = _srs._github_reconcile_row(label, "o/r", row)

    assert result is None, "Live PID must prevent any re-derivation of state"
