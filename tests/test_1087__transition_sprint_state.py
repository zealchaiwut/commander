"""Tests for issue #1087: guard sprint lifecycle state with single authorized writer.

Each test is anchored to a specific acceptance criterion.

AC1  `transition_sprint_state(label, to_state, actor, end_reason=None)` exists in db.py.
AC2  Legal edges pass: draft→running→{ready_to_merge, needs_rework},
     ready_to_merge→completed, any→deleted. (`planned` was deprecated in
     issue #1686 — draft→planned is no longer a legal edge; a legacy row
     stored as "planned" canonicalizes to "draft" before edge lookup.)
AC3  Illegal edges are rejected (no DB mutation, rejection result returned).
AC4  running→{ready_to_merge, needs_rework, completed} with actor!="manager" is rejected;
     DB state remains "running"; rejection is logged.
AC5  All four writers (record_sprint_start, record_sprint_finish,
     record_sprint_needs_rework, record_sprint_ready_to_merge) route through
     transition_sprint_state; _set_sprint_terminal is not called directly.
AC6  canonical_lifecycle and LIFECYCLE_STATES are the sole vocabulary source;
     no new state constants in the new code.
AC7  Unit tests confirm every legal edge passes, ≥2 illegal edges rejected,
     and running→needs_rework with actor="reconcile" is rejected leaving state "running".
AC8  No database schema changes (no new columns, no table mutations).
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))

import db as _db


@pytest.fixture
def fresh_db(tmp_path):
    db_file = tmp_path / "test_1087.db"
    original = _db.DB_PATH
    _db.DB_PATH = db_file
    _db.init_db()
    yield _db
    _db.DB_PATH = original


def _set_state_direct(db, label: str, state: str, project: str = "") -> None:
    """Bypass the guard and write state directly — only for test setup."""
    with db.get_conn() as conn:
        db._create_sprint_lifecycle_tables(conn)
        conn.execute(
            "INSERT INTO sprints (label, project, state) VALUES (?, ?, ?) "
            "ON CONFLICT(label, project) DO UPDATE SET state = excluded.state",
            (label, project, state),
        )
        conn.commit()


def _get_state(db, label: str) -> str | None:
    row = db.get_sprint(label)
    return row["state"] if row else None


# ── AC1: function exists and is importable ────────────────────────────────────

def test_ac1_transition_sprint_state_exists():
    assert hasattr(_db, "transition_sprint_state"), \
        "db.transition_sprint_state not found"
    assert callable(_db.transition_sprint_state)


def test_ac1_transition_result_has_accepted_field(fresh_db):
    _set_state_direct(fresh_db, "sprint-1", "draft")
    result = fresh_db.transition_sprint_state("sprint-1", "running", actor="manager")
    assert hasattr(result, "accepted"), "TransitionResult must have .accepted"
    assert result.accepted is True


# ── AC2: legal edges pass ────────────────────────────────────────────────────

def test_ac2_draft_to_planned_is_now_illegal(fresh_db):
    """`planned` was deprecated (issue #1686) — draft can no longer target it."""
    _set_state_direct(fresh_db, "s", "draft")
    r = fresh_db.transition_sprint_state("s", "planned", actor="manager")
    assert r.accepted is False
    assert _get_state(fresh_db, "s") == "draft"


def test_ac2_legacy_planned_row_canonicalizes_to_draft_then_running(fresh_db):
    """A legacy row stored as "planned" canonicalizes to "draft" for the edge
    lookup, so draft's legal targets (e.g. running) still apply to it."""
    _set_state_direct(fresh_db, "s", "planned")
    r = fresh_db.transition_sprint_state("s", "running", actor="manager")
    assert r.accepted is True
    assert _get_state(fresh_db, "s") == "running"


def test_ac2_running_to_ready_to_merge(fresh_db):
    _set_state_direct(fresh_db, "s", "running")
    r = fresh_db.transition_sprint_state("s", "ready_to_merge", actor="manager")
    assert r.accepted is True
    assert _get_state(fresh_db, "s") == "ready_to_merge"


def test_ac2_running_to_needs_rework(fresh_db):
    _set_state_direct(fresh_db, "s", "running")
    r = fresh_db.transition_sprint_state("s", "needs_rework", actor="manager")
    assert r.accepted is True
    assert _get_state(fresh_db, "s") == "needs_rework"


def test_ac2_ready_to_merge_to_completed(fresh_db):
    _set_state_direct(fresh_db, "s", "ready_to_merge")
    r = fresh_db.transition_sprint_state("s", "completed", actor="manager")
    assert r.accepted is True
    assert _get_state(fresh_db, "s") == "completed"


def test_ac2_running_to_deleted(fresh_db):
    _set_state_direct(fresh_db, "s", "running")
    r = fresh_db.transition_sprint_state("s", "deleted", actor="manager")
    assert r.accepted is True
    # `deleted` is not stored in sprints (CHECK constraint); row is removed
    assert fresh_db.get_sprint("s") is None


def test_ac2_no_row_to_running_allowed(fresh_db):
    """Sprint with no prior DB row is treated as draft; draft→running is allowed."""
    r = fresh_db.transition_sprint_state("brand-new", "running", actor="manager")
    assert r.accepted is True
    assert _get_state(fresh_db, "brand-new") == "running"


# ── AC3: illegal edges are rejected, DB not mutated ──────────────────────────

def test_ac3_draft_to_completed_rejected(fresh_db):
    _set_state_direct(fresh_db, "s", "draft")
    r = fresh_db.transition_sprint_state("s", "completed", actor="manager")
    assert r.accepted is False
    assert _get_state(fresh_db, "s") == "draft", "DB must not be mutated on illegal edge"


def test_ac3_completed_to_running_rejected(fresh_db):
    _set_state_direct(fresh_db, "s", "completed")
    r = fresh_db.transition_sprint_state("s", "running", actor="manager")
    assert r.accepted is False
    assert _get_state(fresh_db, "s") == "completed"


def test_ac3_rejection_result_has_reason(fresh_db):
    _set_state_direct(fresh_db, "s", "draft")
    r = fresh_db.transition_sprint_state("s", "completed", actor="manager")
    assert r.accepted is False
    assert hasattr(r, "reason") and r.reason, "rejection must carry a non-empty reason"


# ── AC4: actor guard on running→terminal ─────────────────────────────────────

def test_reconcile_promotes_needs_rework_to_ready_to_merge(fresh_db):
    """Reconciler may promote a mis-tagged natural success back to ready_to_merge."""
    fresh_db.record_sprint_start("s", project="p")
    fresh_db.record_sprint_ready_to_merge("s", end_reason="natural")
    fresh_db.record_sprint_needs_rework("s", end_reason="natural")
    r = fresh_db.transition_sprint_state("s", "ready_to_merge", actor="reconcile")
    assert r.accepted is True
    assert fresh_db.get_sprint("s")["state"] == "ready_to_merge"


def test_ac4_running_to_needs_rework_reconcile_rejected(fresh_db):
    """Core guard case from the bug report."""
    _set_state_direct(fresh_db, "s", "running")
    r = fresh_db.transition_sprint_state("s", "needs_rework", actor="reconcile")
    assert r.accepted is False
    assert _get_state(fresh_db, "s") == "running", "DB must stay running"


def test_ac4_running_to_needs_rework_read_rejected(fresh_db):
    _set_state_direct(fresh_db, "s", "running")
    r = fresh_db.transition_sprint_state("s", "needs_rework", actor="read")
    assert r.accepted is False
    assert _get_state(fresh_db, "s") == "running"


def test_ac4_running_to_ready_to_merge_reconcile_rejected(fresh_db):
    _set_state_direct(fresh_db, "s", "running")
    r = fresh_db.transition_sprint_state("s", "ready_to_merge", actor="reconcile")
    assert r.accepted is False
    assert _get_state(fresh_db, "s") == "running"


def test_ac4_running_to_completed_non_manager_rejected(fresh_db):
    _set_state_direct(fresh_db, "s", "running")
    r = fresh_db.transition_sprint_state("s", "completed", actor="dashboard")
    assert r.accepted is False
    assert _get_state(fresh_db, "s") == "running"


def test_ac4_rejection_is_logged(fresh_db, caplog):
    _set_state_direct(fresh_db, "s", "running")
    with caplog.at_level(logging.WARNING, logger="db.sprint_state"):
        fresh_db.transition_sprint_state("s", "needs_rework", actor="reconcile")
    assert any("reconcile" in r.message or "rejected" in r.message.lower()
               for r in caplog.records), "rejection must be logged"


def test_ac4_manager_succeeds_running_to_needs_rework(fresh_db):
    _set_state_direct(fresh_db, "s", "running")
    r = fresh_db.transition_sprint_state("s", "needs_rework", actor="manager")
    assert r.accepted is True
    assert _get_state(fresh_db, "s") == "needs_rework"


# ── AC5: existing writers route through transition_sprint_state ───────────────

def test_ac5_record_sprint_finish_routes_through_guard(fresh_db):
    """record_sprint_finish must invoke transition_sprint_state."""
    with patch.object(fresh_db, "transition_sprint_state",
                      wraps=fresh_db.transition_sprint_state) as mock_fn:
        fresh_db.record_sprint_start("s", project="p")
        fresh_db.record_sprint_finish("s")
    calls = [c for c in mock_fn.call_args_list
             if c.args[1] == "completed" or (c.args[1:2] and "completed" in c.args)]
    assert calls, "record_sprint_finish did not call transition_sprint_state with 'completed'"


def test_ac5_record_sprint_needs_rework_routes_through_guard(fresh_db):
    with patch.object(fresh_db, "transition_sprint_state",
                      wraps=fresh_db.transition_sprint_state) as mock_fn:
        fresh_db.record_sprint_start("s", project="p")
        fresh_db.record_sprint_needs_rework("s", end_reason="test")
    calls = [c for c in mock_fn.call_args_list
             if "needs_rework" in c.args]
    assert calls, "record_sprint_needs_rework did not call transition_sprint_state"


def test_ac5_record_sprint_ready_to_merge_routes_through_guard(fresh_db):
    with patch.object(fresh_db, "transition_sprint_state",
                      wraps=fresh_db.transition_sprint_state) as mock_fn:
        fresh_db.record_sprint_start("s", project="p")
        fresh_db.record_sprint_ready_to_merge("s")
    calls = [c for c in mock_fn.call_args_list
             if "ready_to_merge" in c.args]
    assert calls, "record_sprint_ready_to_merge did not call transition_sprint_state"


def test_ac5_record_sprint_start_routes_through_guard(fresh_db):
    with patch.object(fresh_db, "transition_sprint_state",
                      wraps=fresh_db.transition_sprint_state) as mock_fn:
        fresh_db.record_sprint_start("s", project="p")
    calls = [c for c in mock_fn.call_args_list if "running" in c.args]
    assert calls, "record_sprint_start did not call transition_sprint_state with 'running'"


# ── AC6: no new state constants; only LIFECYCLE_STATES / canonical_lifecycle ──

def test_ac6_lifecycle_states_unchanged():
    # `planned` removed from the UI-exposed enum (issue #1686 deprecation) —
    # it remains legacy-readable via canonical_lifecycle() only.
    expected = (
        "draft", "running", "ready_to_merge", "needs_rework",
        "partial_finished", "completed", "deleted",
    )
    assert _db.LIFECYCLE_STATES == expected


def test_ac6_canonical_lifecycle_still_maps_legacy():
    assert _db.canonical_lifecycle("planning") == "draft"
    assert _db.canonical_lifecycle("finished") == "completed"
    assert _db.canonical_lifecycle("cancelled") == "needs_rework"


# ── AC7: comprehensive edge coverage (summary assertions) ────────────────────

LEGAL_TRANSITIONS = [
    ("draft",          "running"),
    ("running",        "ready_to_merge"),
    ("running",        "needs_rework"),
    ("ready_to_merge", "completed"),
]

LEGAL_DELETED_TRANSITIONS = [
    ("draft",   "deleted"),
    ("running", "deleted"),
]

ILLEGAL_TRANSITIONS = [
    ("draft",        "completed"),       # must earn completed through full lifecycle
    ("completed",    "running"),         # completed sprint cannot restart
    ("needs_rework", "completed"),       # must go via running→ready_to_merge→completed
    ("needs_rework", "ready_to_merge"),  # must go via running first
]


@pytest.mark.parametrize("from_state,to_state", LEGAL_TRANSITIONS)
def test_ac7_legal_edge_passes(fresh_db, from_state, to_state):
    _set_state_direct(fresh_db, "s", from_state)
    r = fresh_db.transition_sprint_state("s", to_state, actor="manager")
    assert r.accepted is True, f"legal edge {from_state}→{to_state} was rejected: {r.reason}"
    assert _get_state(fresh_db, "s") == to_state


@pytest.mark.parametrize("from_state,to_state", LEGAL_DELETED_TRANSITIONS)
def test_ac7_legal_deleted_edge_passes(fresh_db, from_state, to_state):
    _set_state_direct(fresh_db, "s", from_state)
    r = fresh_db.transition_sprint_state("s", to_state, actor="manager")
    assert r.accepted is True, f"legal edge {from_state}→{to_state} was rejected: {r.reason}"
    assert fresh_db.get_sprint("s") is None, "deleted sprint row should be removed"


@pytest.mark.parametrize("from_state,to_state", ILLEGAL_TRANSITIONS)
def test_ac7_illegal_edge_rejected(fresh_db, from_state, to_state):
    _set_state_direct(fresh_db, "s", from_state)
    r = fresh_db.transition_sprint_state("s", to_state, actor="manager")
    assert r.accepted is False, f"illegal edge {from_state}→{to_state} should be rejected"
    assert _get_state(fresh_db, "s") == from_state, "DB must not be mutated on illegal edge"


def test_ac7_running_needs_rework_reconcile_rejected_state_unchanged(fresh_db):
    _set_state_direct(fresh_db, "s", "running")
    r = fresh_db.transition_sprint_state("s", "needs_rework", actor="reconcile")
    assert r.accepted is False
    assert _get_state(fresh_db, "s") == "running"


# ── AC8: no schema changes ───────────────────────────────────────────────────

def test_ac8_no_new_columns_in_sprints(fresh_db):
    """AC8: no schema changes — required columns must exist; new ones are forbidden."""
    with fresh_db.get_conn() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(sprints)").fetchall()}
    required_cols = {
        "label", "project", "state", "created_at",
        "started_at", "ended_at", "end_reason", "parent_label",
    }
    assert required_cols <= cols, f"missing columns: {required_cols - cols}"
    # Columns added by this issue (#1087) specifically — there should be none.
    issue_new_cols = cols - required_cols - {
        # Pre-existing extras added by prior issues (not introduced by #1087):
        "summary_path", "pr_number", "reconciliation_json", "tokens",
        "issues_json", "summary_issue_url", "post_sprint_json",
        "estimate_accuracy", "wall_clock_secs", "run_ingested_at",
        # Run-artifact denormalized counts added later by lifecycle P3:
        "summary_settled_done", "summary_uat_count", "summary_failure_count",
    }
    assert not issue_new_cols, f"new columns added by this issue: {issue_new_cols}"


# ── Idempotent same-state no-op (bulk-complete resume) ───────────────────────

def test_same_state_completed_is_idempotent_noop(fresh_db):
    """Re-completing an already-`completed` sprint is an accepted no-op, not an
    illegal edge. Guards bulk-complete resume: a prior run may have completed a
    member; re-running must skip it, not reject `completed→completed` and wedge."""
    _set_state_direct(fresh_db, "sprint-99", "completed")
    r = fresh_db.transition_sprint_state("sprint-99", "completed", actor="manager")
    assert r.accepted is True, f"same-state must be accepted; reason={r.reason!r}"
    assert _get_state(fresh_db, "sprint-99") == "completed"


def test_same_state_noop_does_not_raise_or_change(fresh_db):
    """needs_rework→needs_rework (and any same-state) is a no-op success too."""
    _set_state_direct(fresh_db, "sprint-100", "needs_rework")
    r = fresh_db.transition_sprint_state("sprint-100", "needs_rework", actor="manager")
    assert r.accepted is True
    assert _get_state(fresh_db, "sprint-100") == "needs_rework"
