"""Tests for issue #2247 — AC1 & AC4: run log/reasoning views tolerate NULL sprint_label.

AC1: Run log and reasoning views resolve by issue + role + session when sprint_label is NULL.
AC4: Sprints that do have a label and a state.json still render exactly as before.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

sys.path.insert(0, str(DASHBOARD_DIR))
sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest-2247.db")
os.environ.setdefault("COMMANDER_DISABLE_NEON", "1")

import db as _db  # noqa: E402
import routers.runs_service as svc  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path):
    db_file = tmp_path / "test_2247_ac1.db"
    original = _db.DB_PATH
    _db.DB_PATH = db_file
    _db.init_db()
    yield _db
    _db.DB_PATH = original


def _insert_run(conn, issue_number, agent, sprint_label, log_path, session_id=None):
    conn.execute(
        "INSERT INTO agent_runs (issue_number, sprint_label, agent, started_at, log_path, session_id) "
        "VALUES (?, ?, ?, datetime('now'), ?, ?)",
        (issue_number, sprint_label, agent, log_path, session_id),
    )
    conn.commit()


# ── AC1: get_log_path_from_db("null", ...) resolves NULL sprint rows ──────────

def test_ac1_get_log_path_null_sentinel_returns_path(fresh_db, tmp_path):
    """AC1: get_log_path_from_db('null', issue, agent) returns log_path when DB row has sprint_label=NULL."""
    log_file = tmp_path / "agent.log"
    log_file.write_text("log content")

    with _db.get_conn() as conn:
        _db._create_agent_runs_table(conn)
        _insert_run(conn, 100, "coder", None, str(log_file))

    result = svc.get_log_path_from_db("null", 100, "coder")
    assert result == str(log_file), (
        f"Expected log_path {log_file!r}, got {result!r}"
    )


def test_ac1_get_log_path_null_sentinel_does_not_match_string_null(fresh_db, tmp_path):
    """AC1: 'null' sentinel only matches rows with sprint_label IS NULL, not sprint_label='null'."""
    log_real = tmp_path / "manual.log"
    log_real.write_text("manual")
    log_wrong = tmp_path / "wrong.log"
    log_wrong.write_text("wrong")

    with _db.get_conn() as conn:
        _db._create_agent_runs_table(conn)
        _insert_run(conn, 101, "coder", None, str(log_real))        # sprint_label IS NULL
        _insert_run(conn, 101, "coder", "null", str(log_wrong))     # sprint_label = 'null' (not matched)

    result = svc.get_log_path_from_db("null", 101, "coder")
    # Should return the IS NULL row, not the literal-'null' row
    assert result == str(log_real), (
        f"'null' sentinel must match IS NULL rows only; got {result!r}"
    )


def test_ac1_list_runs_maps_null_sprint_to_sentinel_string(fresh_db, tmp_path):
    """AC1: list_runs() emits 'sprint': 'null' (string) for NULL sprint_label rows so URLs work."""
    log_file = tmp_path / "run.log"
    log_file.write_text("")

    with _db.get_conn() as conn:
        _db._create_agent_runs_table(conn)
        _db._create_sprint_lifecycle_tables(conn)
        _insert_run(conn, 200, "coder", None, str(log_file))

    runs = svc.list_runs()
    # Find the entry for our NULL-sprint run
    null_sprint_entries = [r for r in runs if r.get("sprint") == "null"]
    assert null_sprint_entries, (
        f"Expected a 'null' sentinel sprint in list_runs output; got: {[r.get('sprint') for r in runs]}"
    )
    # Must NOT contain Python None as the sprint key (would serialize to JSON null)
    none_sprint_entries = [r for r in runs if r.get("sprint") is None]
    assert not none_sprint_entries, (
        "list_runs() must not emit sprint=None (JSON null); use 'null' sentinel string"
    )


# ── AC4: existing sprint rows still render unchanged ──────────────────────────

def test_ac4_labeled_sprint_log_path_still_resolves(fresh_db, tmp_path):
    """AC4: get_log_path_from_db('sprint-5', issue, agent) is unchanged for labeled sprints."""
    log_file = tmp_path / "sprint5.log"
    log_file.write_text("sprint log")

    with _db.get_conn() as conn:
        _db._create_agent_runs_table(conn)
        _insert_run(conn, 300, "tester", "sprint-5", str(log_file))

    result = svc.get_log_path_from_db("sprint-5", 300, "tester")
    assert result == str(log_file), (
        f"Labeled sprint lookup broken; expected {log_file!r}, got {result!r}"
    )


def test_ac4_labeled_sprint_appears_in_list_runs(fresh_db, tmp_path):
    """AC4: list_runs() still returns labeled sprint entries with correct sprint key."""
    log_file = tmp_path / "sprint99.log"
    log_file.write_text("")

    with _db.get_conn() as conn:
        _db._create_agent_runs_table(conn)
        _db._create_sprint_lifecycle_tables(conn)
        _insert_run(conn, 400, "coder", "sprint-99", str(log_file))

    runs = svc.list_runs()
    sprint_99_entries = [r for r in runs if r.get("sprint") == "sprint-99"]
    assert sprint_99_entries, "Labeled sprint 'sprint-99' missing from list_runs() output"
