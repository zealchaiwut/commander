"""Tester regression suite for issue #755: write-through transition() state to SQLite, drop verify-read.

This is a backend-only change to services/sprint_manager/state_machine.transition()
and apps/dashboard/db.py — there is no HTTP surface, so these run in-process
against the module (gh subprocess mocked, DB pointed at a temp SQLite file)
rather than against the UAT server.

One test function per acceptance criterion (skill naming: test_{slug}__{criterion}).
"""
from __future__ import annotations

import importlib
import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

from services.sprint_manager import state_machine
from services.sprint_manager.state_machine import TicketState, transition

_REPO = "zealchaiwut/commander"
_ISSUE = 99055


def _view(*names: str) -> MagicMock:
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


def _edit_fail(msg: str = "API error") -> MagicMock:
    m = MagicMock()
    m.returncode = 1
    m.stdout = ""
    m.stderr = msg
    return m


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Point apps/dashboard/db at a fresh temp SQLite file (DB_PATH read at import)."""
    db_file = tmp_path / "commander_test.db"
    monkeypatch.setenv("DB_PATH", str(db_file))
    import db as _db
    importlib.reload(_db)
    _db.init_db()
    return _db, db_file


# --- Acceptance Criteria ---

def test_write_through_transition__two_gh_calls_no_verify_refetch():
    # AC: transition() issues exactly 2 gh calls (view + edit); verify loop removed.
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return _view("in-progress") if "view" in cmd else _edit_ok()

    with patch.object(state_machine, "_write_ticket_status"), \
         patch("services.sprint_manager.state_machine.subprocess.run", side_effect=fake_run), \
         patch("services.sprint_manager.state_machine.time"):
        result = transition(_ISSUE, TicketState.SIT, actor="test", repo=_REPO)

    assert result is True
    assert len(calls) == 2, f"expected 2 gh calls, got {len(calls)}: {calls}"
    view_idx = [i for i, c in enumerate(calls) if "view" in c]
    edit_idx = [i for i, c in enumerate(calls) if "edit" in c]
    assert view_idx == [0] and edit_idx == [1], "exactly one view before one edit, no post-edit re-fetch"


def test_write_through_transition__retry_on_edit_failure_preserved():
    # AC: retry-on-edit-failure logic preserved and unchanged (backoff 1,3,7).
    sleeps = []

    # Case 1: succeeds on second attempt after one edit failure.
    edits = iter([_edit_fail(), _edit_ok()])

    def fake_run_recover(cmd, **kwargs):
        return _view("in-progress") if "view" in cmd else next(edits)

    with patch.object(state_machine, "_write_ticket_status"), \
         patch("services.sprint_manager.state_machine.subprocess.run", side_effect=fake_run_recover), \
         patch("services.sprint_manager.state_machine.time") as mt:
        mt.sleep.side_effect = lambda s: sleeps.append(s)
        assert transition(_ISSUE, TicketState.SIT, actor="t", repo=_REPO) is True
    assert sleeps == [1]

    # Case 2: all attempts fail -> raises after full backoff sequence.
    sleeps.clear()

    def fake_run_fail(cmd, **kwargs):
        return _view("in-progress") if "view" in cmd else _edit_fail("network down")

    with patch.object(state_machine, "_write_ticket_status"), \
         patch("services.sprint_manager.state_machine.subprocess.run", side_effect=fake_run_fail), \
         patch("services.sprint_manager.state_machine.time") as mt:
        mt.sleep.side_effect = lambda s: sleeps.append(s)
        with pytest.raises(state_machine.TransitionError):
            transition(_ISSUE, TicketState.SIT, actor="t", repo=_REPO)
    assert sleeps == [1, 3, 7]


def test_write_through_transition__ticket_status_row_written(temp_db):
    # AC: a ticket_status row is written per successful transition with all 5 fields.
    _db, db_file = temp_db

    def fake_run(cmd, **kwargs):
        return _view("in-progress") if "view" in cmd else _edit_ok()

    with patch("services.sprint_manager.state_machine.subprocess.run", side_effect=fake_run), \
         patch("services.sprint_manager.state_machine.time"):
        result = transition(_ISSUE, TicketState.SIT, actor="tester-bot", note="post-merge", repo=_REPO)

    assert result is True
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM ticket_status WHERE issue = ? ORDER BY ts DESC", (str(_ISSUE),)
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    r = rows[0]
    assert str(r["issue"]) == str(_ISSUE)
    assert r["status"] == TicketState.SIT.name
    assert r["actor"] == "tester-bot"
    assert r["note"] == "post-merge"
    # ts present and parseable as UTC ISO-8601 (matches other tables' format).
    from datetime import datetime
    datetime.strptime(r["ts"], "%Y-%m-%dT%H:%M:%S")

    # No-op transition writes no row.
    with patch("services.sprint_manager.state_machine.subprocess.run", return_value=_view("SIT")), \
         patch("services.sprint_manager.state_machine.time"):
        assert transition(_ISSUE, TicketState.SIT, actor="t", repo=_REPO) is False
    conn = sqlite3.connect(db_file)
    n = conn.execute("SELECT COUNT(*) FROM ticket_status WHERE issue = ?", (str(_ISSUE),)).fetchone()[0]
    conn.close()
    assert n == 1, "no-op transition must not append a ticket_status row"


def test_write_through_transition__db_failure_swallowed_returns_true(temp_db):
    # AC: DB write failure does NOT raise or return False; logs db_write_failed.
    _db, db_file = temp_db

    def fake_run(cmd, **kwargs):
        return _view("in-progress") if "view" in cmd else _edit_ok()

    def boom(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    with patch.object(_db, "record_ticket_status", side_effect=boom), \
         patch("services.sprint_manager.state_machine.subprocess.run", side_effect=fake_run), \
         patch("services.sprint_manager.state_machine.time"), \
         patch("services.sprint_manager.state_machine._LOG_AVAILABLE", True), \
         patch("services.sprint_manager.state_machine._log") as mock_log:
        result = transition(_ISSUE, TicketState.SIT, actor="t", repo=_REPO)

    assert result is True, "transition must still succeed when the DB write fails"
    events = [c.args[0] for c in mock_log.error.call_args_list]
    assert "db_write_failed" in events, f"expected structured db_write_failed log, got {events}"


def test_write_through_transition__log_transition_intact(capsys):
    # AC: _log_transition and activity-event emission remain intact and unmodified.
    def fake_run(cmd, **kwargs):
        return _view("in-progress") if "view" in cmd else _edit_ok()

    with patch.object(state_machine, "_write_ticket_status"), \
         patch("services.sprint_manager.state_machine.subprocess.run", side_effect=fake_run), \
         patch("services.sprint_manager.state_machine.time"), \
         patch("services.sprint_manager.state_machine._LOG_AVAILABLE", False):
        transition(_ISSUE, TicketState.SIT, actor="tester-bot", note="post-merge", repo=_REPO)

    out = capsys.readouterr().out
    assert "tester-bot" in out and "post-merge" in out


def test_write_through_transition__table_via_db_layer_not_adhoc(temp_db):
    # AC: ticket_status created/migrated via the existing DB layer (db.py), not ad-hoc SQL.
    _db, db_file = temp_db
    # init_db() (run by the fixture) must have created the table with the AC's schema.
    conn = sqlite3.connect(db_file)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    cols = {r[1] for r in conn.execute("PRAGMA table_info(ticket_status)")}
    conn.close()
    assert "ticket_status" in tables
    assert {"issue", "status", "actor", "note", "ts"} <= cols
    # db layer exposes the documented helper rather than callers issuing raw SQL.
    assert hasattr(_db, "record_ticket_status")


def test_write_through_transition__no_caller_visible_api_change(temp_db):
    # AC: state_machine 2-call + DB write behavior; no caller-visible API change.
    # transition() signature and return contract unchanged: True on change, False on no-op.
    _db, db_file = temp_db

    def fake_run(cmd, **kwargs):
        return _view("in-progress") if "view" in cmd else _edit_ok()

    with patch("services.sprint_manager.state_machine.subprocess.run", side_effect=fake_run), \
         patch("services.sprint_manager.state_machine.time"):
        changed = transition(_ISSUE, TicketState.SIT, actor="t", repo=_REPO)
    assert changed is True

    # Pseudo-state guard still raises ValueError (unchanged contract).
    with pytest.raises(ValueError):
        transition(_ISSUE, TicketState.DONE, actor="t", repo=_REPO)
