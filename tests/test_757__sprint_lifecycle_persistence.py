"""Tests for issue #757: persist sprint lifecycle and ticket order to DB.

Each test is anchored to a specific acceptance criterion:

AC1  `sprints` table exists with columns: label, project, state, created_at,
     started_at, ended_at, end_reason, parent_label.
AC2  `sprint_ticket_order` table exists with columns: label, issue, position.
AC3  Lifecycle writes: start -> state='running', finish -> state='completed',
     cancel -> state='cancelled' (the DB API sprint_manager.py calls).
AC4  Ticket order is persisted and read back in the persisted order.
AC5  Dashboard lifecycle helpers write the same transitions to the DB.
AC6  "Is a sprint running?" uses DB state='running' AND PID liveness;
     PID-dead + DB-running does NOT report running.
AC7  `failed` is a valid state value (no CHECK violation).
AC8  plan.json dual-write helpers still function (no regression).
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))

import db as _db_module  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path):
    """Isolated SQLite DB; yields the patched db module."""
    db_file = tmp_path / "test_757.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


def _columns(db, table: str) -> set[str]:
    with db.get_conn() as conn:
        return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


# ── AC1: sprints table schema ────────────────────────────────────────────────

def test_ac1_sprints_table_has_required_columns(fresh_db):
    cols = _columns(fresh_db, "sprints")
    required = {
        "label", "project", "state",
        "created_at", "started_at", "ended_at",
        "end_reason", "parent_label",
    }
    assert required <= cols, f"missing columns: {required - cols}"


# ── AC2: sprint_ticket_order table schema ────────────────────────────────────

def test_ac2_sprint_ticket_order_table_has_required_columns(fresh_db):
    cols = _columns(fresh_db, "sprint_ticket_order")
    assert {"label", "issue", "position"} <= cols


# ── AC3: lifecycle transitions ───────────────────────────────────────────────

def test_ac3_start_writes_running_row(fresh_db):
    fresh_db.record_sprint_start("sprint-99", project="zealchaiwut/commander")
    row = fresh_db.get_sprint("sprint-99")
    assert row is not None
    assert row["state"] == "running"
    assert row["project"] == "zealchaiwut/commander"
    assert row["started_at"]  # populated


def test_ac3_finish_updates_to_completed(fresh_db):
    fresh_db.record_sprint_start("sprint-99", project="p")
    fresh_db.record_sprint_finish("sprint-99")
    row = fresh_db.get_sprint("sprint-99")
    assert row["state"] == "completed"
    assert row["ended_at"]


def test_ac3_cancel_updates_to_cancelled_with_reason(fresh_db):
    fresh_db.record_sprint_start("sprint-99", project="p")
    fresh_db.record_sprint_cancel("sprint-99", end_reason="user-cancel")
    row = fresh_db.get_sprint("sprint-99")
    assert row["state"] == "cancelled"
    assert row["end_reason"] == "user-cancel"
    assert row["ended_at"]


def test_ac3_start_is_idempotent_single_row(fresh_db):
    fresh_db.record_sprint_start("sprint-99", project="p")
    fresh_db.record_sprint_start("sprint-99", project="p")
    with fresh_db.get_conn() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM sprints WHERE label='sprint-99'"
        ).fetchone()["c"]
    assert n == 1


# ── AC4: ticket order persistence + ordering ─────────────────────────────────

def test_ac4_ticket_order_round_trips_in_persisted_order(fresh_db):
    fresh_db.set_sprint_ticket_order("sprint-99", [42, 17, 88])
    assert fresh_db.get_sprint_ticket_order("sprint-99") == [42, 17, 88]


def test_ac4_reorder_updates_positions(fresh_db):
    fresh_db.set_sprint_ticket_order("sprint-99", [42, 17, 88])
    fresh_db.set_sprint_ticket_order("sprint-99", [88, 42, 17])
    assert fresh_db.get_sprint_ticket_order("sprint-99") == [88, 42, 17]
    with fresh_db.get_conn() as conn:
        rows = conn.execute(
            "SELECT issue, position FROM sprint_ticket_order "
            "WHERE label='sprint-99' ORDER BY position"
        ).fetchall()
    assert [(r["issue"], r["position"]) for r in rows] == [(88, 0), (42, 1), (17, 2)]


def test_ac4_unknown_label_returns_empty(fresh_db):
    assert fresh_db.get_sprint_ticket_order("sprint-nope") == []


# ── AC6: is-running uses DB state AND PID liveness ───────────────────────────

def test_ac6_running_and_pid_alive_reports_running(fresh_db):
    fresh_db.record_sprint_start("sprint-99", project="p")
    assert fresh_db.is_sprint_running("sprint-99", pid_alive=True) is True


def test_ac6_running_but_pid_dead_does_not_report_running(fresh_db):
    fresh_db.record_sprint_start("sprint-99", project="p")
    assert fresh_db.is_sprint_running("sprint-99", pid_alive=False) is False


def test_ac6_completed_never_reports_running(fresh_db):
    fresh_db.record_sprint_start("sprint-99", project="p")
    fresh_db.record_sprint_finish("sprint-99")
    assert fresh_db.is_sprint_running("sprint-99", pid_alive=True) is False


def test_ac6_no_row_not_running(fresh_db):
    assert fresh_db.is_sprint_running("sprint-nope", pid_alive=True) is False


# ── AC7: failed is a valid state value ───────────────────────────────────────

def test_ac7_failed_state_accepted_no_constraint_violation(fresh_db):
    fresh_db.record_sprint_start("sprint-99", project="p")
    with fresh_db.get_conn() as conn:
        conn.execute("UPDATE sprints SET state='failed' WHERE label='sprint-99'")
        conn.commit()
    assert fresh_db.get_sprint("sprint-99")["state"] == "failed"


def test_ac7_invalid_state_rejected(fresh_db):
    with fresh_db.get_conn() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO sprints (label, state) VALUES ('sprint-x', 'bogus')"
            )
            conn.commit()


# ── AC5/AC8: dashboard helpers dual-write to DB + plan.json ──────────────────

def test_ac5_server_start_writes_db_row(fresh_db, tmp_path):
    import importlib
    import server as _server  # noqa
    importlib.reload  # keep linters quiet; we use _server directly
    project_root = tmp_path / "proj"
    (project_root / ".commander" / "sprints").mkdir(parents=True)
    # The dashboard lifecycle helper must mirror state into the DB.
    _server._sprint_db_set_state(
        "sprint-77", project="zealchaiwut/commander", state="running"
    )
    row = fresh_db.get_sprint("sprint-77")
    assert row is not None and row["state"] == "running"


def test_ac8_plan_json_helper_still_writes(fresh_db, tmp_path):
    import server as _server  # noqa
    project_root = tmp_path / "proj"
    (project_root / ".commander" / "sprints").mkdir(parents=True)
    _server._plan_json_set_state(project_root, "sprint-77", "running")
    plan = _server._read_plan_json(project_root, "sprint-77")
    assert plan is not None and plan["state"] == "running"
