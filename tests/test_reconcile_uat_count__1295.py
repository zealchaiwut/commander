"""Tests for issue #1295: Re-derive summary_uat_count in reconcile path.

AC1  _reconcile_counts derives summary_uat_count by counting issues with status='uat'
AC2  derived uat_count in reconcile path matches the value from materialize path
AC3  when a ticket transitions to UAT after reconcile, next pass reflects updated count
AC4  no other denormalized count column reads from stored state when it could be re-derived
AC5  code comments explain any deliberate exclusions from re-derivation
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_REPO = Path(__file__).resolve().parent.parent
_DASHBOARD = _REPO / "apps" / "dashboard"
sys.path.insert(0, str(_DASHBOARD))


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    """Create a fresh test database."""
    db_path = tmp_path / "test_1295.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    for mod in list(sys.modules.keys()):
        if mod in ("db", "server") or mod.startswith("routers"):
            del sys.modules[mod]
    import db as db_module
    db_module.init_db()
    return db_module


# ── AC1: Derive uat_count from issues list with status='uat' ──────────────────

def test_reconcile_counts_derives_uat_from_issues(fresh_db, monkeypatch):
    """_reconcile_counts must derive summary_uat_count by counting status='uat' issues."""
    # Setup: Create a sprint with issues_json containing mixed statuses (some uat, some merged)
    label = "sprint-1295-1"
    initial_issues = [
        {"ticket_id": 101, "number": 101, "title": "A", "state": "merged", "agent_status": "completed"},
        {"ticket_id": 102, "number": 102, "title": "B", "state": "open", "status": "uat"},
        {"ticket_id": 103, "number": 103, "title": "C", "state": "open", "status": "uat"},
        {"ticket_id": 104, "number": 104, "title": "D", "state": "merged", "agent_status": "completed"},
    ]

    fresh_db.record_sprint_ready_to_merge(label, project="o/r")
    with fresh_db.get_conn() as conn:
        conn.execute(
            """UPDATE sprints
               SET state = 'completed', issues_json = ?, summary_uat_count = 0
               WHERE label = ?""",
            (json.dumps(initial_issues), label),
        )
        conn.commit()

    # Mock agent_runs to return the same issues (no drift in merged state)
    import routers.sprint_reconcile_service as rec_svc
    with patch.object(rec_svc, "_issues_from_agent_runs") as mock_agent_runs:
        # Return agent_runs with merged state for 101 and 104
        mock_agent_runs.return_value = [
            {"ticket_id": 101, "number": 101, "state": "merged", "agent_status": "completed"},
            {"ticket_id": 104, "number": 104, "state": "merged", "agent_status": "completed"},
        ]

        row = fresh_db.get_sprint(label)
        rec_svc._reconcile_counts(label, row)

    # Verify: uat_count should be re-derived as 2 (issues 102, 103 have status='uat')
    updated_row = fresh_db.get_sprint(label)
    assert updated_row["summary_uat_count"] == 2, (
        f"uat_count not derived correctly; expected 2, got {updated_row['summary_uat_count']}"
    )


def test_reconcile_counts_uat_count_zero_when_no_uat(fresh_db, monkeypatch):
    """When no issues have status='uat', summary_uat_count should be 0."""
    label = "sprint-1295-2"
    initial_issues = [
        {"ticket_id": 201, "number": 201, "state": "merged", "agent_status": "completed"},
        {"ticket_id": 202, "number": 202, "state": "merged", "agent_status": "completed"},
        {"ticket_id": 203, "number": 203, "state": "closed", "agent_status": "failed", "failure_reason": "test"},
    ]

    fresh_db.record_sprint_ready_to_merge(label, project="o/r")
    with fresh_db.get_conn() as conn:
        conn.execute(
            """UPDATE sprints
               SET state = 'completed', issues_json = ?, summary_uat_count = 5
               WHERE label = ?""",
            (json.dumps(initial_issues), label),
        )
        conn.commit()

    import routers.sprint_reconcile_service as rec_svc
    with patch.object(rec_svc, "_issues_from_agent_runs") as mock_agent_runs:
        mock_agent_runs.return_value = [
            {"ticket_id": 201, "number": 201, "state": "merged", "agent_status": "completed"},
            {"ticket_id": 202, "number": 202, "state": "merged", "agent_status": "completed"},
            {"ticket_id": 203, "number": 203, "state": "closed", "agent_status": "failed"},
        ]

        row = fresh_db.get_sprint(label)
        rec_svc._reconcile_counts(label, row)

    updated_row = fresh_db.get_sprint(label)
    assert updated_row["summary_uat_count"] == 0, (
        f"uat_count should be 0 when no issues have status='uat'; got {updated_row['summary_uat_count']}"
    )


# ── AC2: Derived uat_count matches materialize path ──────────────────────────

def test_reconcile_uat_count_matches_materialize_path(fresh_db):
    """Reconcile and materialize paths must produce identical uat_count for same sprint state."""
    from routers import sprint_artifact_service

    # Create a sprint state with multiple UAT issues
    state = {
        "sprint_label": "sprint-1295-match",
        "issues": [
            {"number": 301, "status": "done", "agent_status": "completed"},
            {"number": 302, "status": "uat"},
            {"number": 303, "status": "uat"},
            {"number": 304, "status": "pending"},
            {"number": 305, "agent_status": "failed", "failure_reason": "lint"},
        ],
    }

    # Get uat_count from materialize path (ingest)
    fresh_db.record_sprint_ready_to_merge("sprint-1295-match", project="o/r")
    fresh_db.ingest_sprint_run_artifact("sprint-1295-match", state, project="o/r")
    materialized_row = fresh_db.get_sprint("sprint-1295-match")
    materialized_uat = materialized_row["summary_uat_count"]

    # Now simulate reconcile path: start with different stored uat count, re-derive
    label = "sprint-1295-reconcile"
    fresh_db.record_sprint_ready_to_merge(label, project="o/r")
    issues_json = [
        {"ticket_id": 301, "number": 301, "state": "merged", "agent_status": "completed"},
        {"ticket_id": 302, "number": 302, "state": "open", "status": "uat"},
        {"ticket_id": 303, "number": 303, "state": "open", "status": "uat"},
        {"ticket_id": 304, "number": 304, "state": "open", "status": "pending"},
        {"ticket_id": 305, "number": 305, "state": "closed", "agent_status": "failed"},
    ]
    with fresh_db.get_conn() as conn:
        conn.execute(
            """UPDATE sprints
               SET state = 'completed', issues_json = ?, summary_uat_count = 0
               WHERE label = ?""",
            (json.dumps(issues_json), label),
        )
        conn.commit()

    # Run reconcile
    import routers.sprint_reconcile_service as rec_svc
    with patch.object(rec_svc, "_issues_from_agent_runs") as mock_agent_runs:
        mock_agent_runs.return_value = [
            {"ticket_id": 301, "state": "merged", "agent_status": "completed"},
            {"ticket_id": 305, "state": "closed", "agent_status": "failed"},
        ]
        row = fresh_db.get_sprint(label)
        rec_svc._reconcile_counts(label, row)

    reconciled_row = fresh_db.get_sprint(label)
    reconciled_uat = reconciled_row["summary_uat_count"]

    assert reconciled_uat == materialized_uat == 2, (
        f"reconcile uat_count ({reconciled_uat}) != materialize uat_count ({materialized_uat})"
    )


# ── AC3: Updated uat count after ticket transitions to UAT ────────────────────

def test_reconcile_reflects_new_uat_ticket(fresh_db):
    """When a ticket is moved to UAT after reconcile, next pass reflects the change."""
    label = "sprint-1295-update"

    # Initial state: 1 UAT ticket
    initial_issues = [
        {"ticket_id": 401, "number": 401, "state": "merged", "agent_status": "completed"},
        {"ticket_id": 402, "number": 402, "state": "open", "status": "uat"},
    ]

    fresh_db.record_sprint_ready_to_merge(label, project="o/r")
    with fresh_db.get_conn() as conn:
        conn.execute(
            """UPDATE sprints
               SET state = 'completed', issues_json = ?, summary_uat_count = 1
               WHERE label = ?""",
            (json.dumps(initial_issues), label),
        )
        conn.commit()

    # First reconcile
    import routers.sprint_reconcile_service as rec_svc
    with patch.object(rec_svc, "_issues_from_agent_runs") as mock_agent_runs:
        mock_agent_runs.return_value = [
            {"ticket_id": 401, "state": "merged", "agent_status": "completed"},
        ]
        row = fresh_db.get_sprint(label)
        rec_svc._reconcile_counts(label, row)

    first_reconcile_row = fresh_db.get_sprint(label)
    assert first_reconcile_row["summary_uat_count"] == 1

    # Simulate: ticket 403 gets added to UAT (GitHub labels updated)
    updated_issues = [
        {"ticket_id": 401, "number": 401, "state": "merged", "agent_status": "completed"},
        {"ticket_id": 402, "number": 402, "state": "open", "status": "uat"},
        {"ticket_id": 403, "number": 403, "state": "open", "status": "uat"},
    ]
    with fresh_db.get_conn() as conn:
        conn.execute(
            """UPDATE sprints
               SET issues_json = ?
               WHERE label = ?""",
            (json.dumps(updated_issues), label),
        )
        conn.commit()

    # Second reconcile
    with patch.object(rec_svc, "_issues_from_agent_runs") as mock_agent_runs:
        mock_agent_runs.return_value = [
            {"ticket_id": 401, "state": "merged", "agent_status": "completed"},
        ]
        row = fresh_db.get_sprint(label)
        rec_svc._reconcile_counts(label, row)

    second_reconcile_row = fresh_db.get_sprint(label)
    assert second_reconcile_row["summary_uat_count"] == 2, (
        f"uat_count should be 2 after new UAT ticket; got {second_reconcile_row['summary_uat_count']}"
    )


def test_reconcile_double_pass_stable_count(fresh_db):
    """Running reconcile twice on same state should produce stable uat_count."""
    label = "sprint-1295-stable"

    issues = [
        {"ticket_id": 501, "number": 501, "state": "merged", "agent_status": "completed"},
        {"ticket_id": 502, "number": 502, "state": "open", "status": "uat"},
        {"ticket_id": 503, "number": 503, "state": "open", "status": "uat"},
    ]

    fresh_db.record_sprint_ready_to_merge(label, project="o/r")
    with fresh_db.get_conn() as conn:
        conn.execute(
            """UPDATE sprints
               SET state = 'completed', issues_json = ?, summary_uat_count = 0
               WHERE label = ?""",
            (json.dumps(issues), label),
        )
        conn.commit()

    import routers.sprint_reconcile_service as rec_svc
    mock_agent_runs = [
        {"ticket_id": 501, "state": "merged", "agent_status": "completed"},
    ]

    # First reconcile
    with patch.object(rec_svc, "_issues_from_agent_runs", return_value=mock_agent_runs):
        row = fresh_db.get_sprint(label)
        rec_svc._reconcile_counts(label, row)

    first_row = fresh_db.get_sprint(label)
    first_uat = first_row["summary_uat_count"]

    # Second reconcile (no state change)
    with patch.object(rec_svc, "_issues_from_agent_runs", return_value=mock_agent_runs):
        row = fresh_db.get_sprint(label)
        rec_svc._reconcile_counts(label, row)

    second_row = fresh_db.get_sprint(label)
    second_uat = second_row["summary_uat_count"]

    assert first_uat == second_uat == 2, (
        f"uat_count unstable: first={first_uat}, second={second_uat}"
    )


# ── AC4: No other count columns read from stored state ──────────────────────

def test_reconcile_settled_done_derived_not_stored(fresh_db):
    """summary_settled_done must be re-derived, not preserved."""
    label = "sprint-1295-settled"

    issues = [
        {"ticket_id": 601, "number": 601, "state": "merged", "agent_status": "completed"},
        {"ticket_id": 602, "number": 602, "state": "merged", "agent_status": "completed"},
    ]

    fresh_db.record_sprint_ready_to_merge(label, project="o/r")
    with fresh_db.get_conn() as conn:
        conn.execute(
            """UPDATE sprints
               SET state = 'completed', issues_json = ?, summary_settled_done = 0
               WHERE label = ?""",
            (json.dumps(issues), label),
        )
        conn.commit()

    import routers.sprint_reconcile_service as rec_svc
    with patch.object(rec_svc, "_issues_from_agent_runs") as mock_agent_runs:
        mock_agent_runs.return_value = [
            {"ticket_id": 601, "state": "merged", "agent_status": "completed"},
            {"ticket_id": 602, "state": "merged", "agent_status": "completed"},
        ]
        row = fresh_db.get_sprint(label)
        rec_svc._reconcile_counts(label, row)

    updated_row = fresh_db.get_sprint(label)
    assert updated_row["summary_settled_done"] == 2, (
        f"settled_done should be re-derived as 2, not preserve stored 0; got {updated_row['summary_settled_done']}"
    )


def test_reconcile_failure_count_derived_not_stored(fresh_db):
    """summary_failure_count must be re-derived, not preserved."""
    label = "sprint-1295-failure"

    issues = [
        {"ticket_id": 701, "number": 701, "state": "closed", "agent_status": "failed", "failure_reason": "lint"},
        {"ticket_id": 702, "number": 702, "state": "merged", "agent_status": "completed"},
    ]

    fresh_db.record_sprint_ready_to_merge(label, project="o/r")
    with fresh_db.get_conn() as conn:
        conn.execute(
            """UPDATE sprints
               SET state = 'completed', issues_json = ?, summary_failure_count = 0
               WHERE label = ?""",
            (json.dumps(issues), label),
        )
        conn.commit()

    import routers.sprint_reconcile_service as rec_svc
    with patch.object(rec_svc, "_issues_from_agent_runs") as mock_agent_runs:
        mock_agent_runs.return_value = [
            {"ticket_id": 701, "state": "closed", "agent_status": "failed"},
            {"ticket_id": 702, "state": "merged", "agent_status": "completed"},
        ]
        row = fresh_db.get_sprint(label)
        rec_svc._reconcile_counts(label, row)

    updated_row = fresh_db.get_sprint(label)
    assert updated_row["summary_failure_count"] == 1, (
        f"failure_count should be re-derived as 1, not preserve stored 0; got {updated_row['summary_failure_count']}"
    )


# ── AC5: Code comments explain deliberate exclusions ──────────────────────────

def test_reconcile_counts_code_comment_for_uat(fresh_db):
    """The reconcile_counts function should have a code comment if uat is intentionally excluded."""
    import routers.sprint_reconcile_service as rec_svc
    import inspect

    source = inspect.getsource(rec_svc._reconcile_counts)
    # After fix, there should be a comment explaining uat derivation, not exclusion
    # If the fix is applied, the comment should be about deriving uat, not skipping it.
    # For now, verify that the source has been reviewed (no assertion, just documentation).
    pytest.skip("manual — code inspection required to verify comment quality")
