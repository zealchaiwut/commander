"""Tests for issue #755: write-through transition() state to SQLite, drop verify-read.

AC coverage:
1. transition() issues exactly 2 GitHub API calls on the happy path (view + edit);
   the post-edit label verification loop is removed.
2. Retry-on-edit-failure logic is preserved and unchanged.
3. A ticket_status row is written per successful transition containing:
   issue, status, actor, note, ts (UTC timestamp).
4. DB write failure does NOT raise or return False — transition still returns True
   and logs a structured error (event "db_write_failed").
5. _log_transition and activity-event emission remain intact and unmodified.
6. ticket_status table is created/migrated via the existing DB layer (apps/dashboard/db.py),
   not ad-hoc SQL.
7. state_machine unit tests assert 2-call behavior and the DB write (covered here + test_508).
"""
from __future__ import annotations

import json
import os
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

_FAKE_REPO = "zealchaiwut/commander"
_FAKE_ISSUE = 999


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


def _edit_fail(msg: str = "API error") -> MagicMock:
    m = MagicMock()
    m.returncode = 1
    m.stdout = ""
    m.stderr = msg
    return m


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Point the dashboard db layer at a fresh temp SQLite file."""
    db_file = tmp_path / "commander_test.db"
    monkeypatch.setenv("DB_PATH", str(db_file))
    # Reload db module so it picks up the new DB_PATH (read at import time).
    import importlib
    import db as _db
    importlib.reload(_db)
    _db.init_db()
    return _db, db_file


# ── AC1: exactly 2 GitHub API calls on the happy path ─────────────────────────

class TestTwoCallHappyPath:
    def _run_with_disabled_dbwrite(self, from_labels, target):
        """Run a successful transition with the DB write stubbed out,
        capturing every gh subprocess invocation."""
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            if "view" in cmd:
                return _label_response(*from_labels)
            return _edit_ok()

        with patch.object(state_machine, "_write_ticket_status"), \
             patch("services.sprint_manager.state_machine.subprocess.run", side_effect=fake_run), \
             patch("services.sprint_manager.state_machine.time"):
            result = transition(_FAKE_ISSUE, target, actor="test", repo=_FAKE_REPO)
        return result, calls

    def test_happy_path_issues_exactly_two_gh_calls(self):
        result, calls = self._run_with_disabled_dbwrite(["in-progress"], TicketState.SIT)
        assert result is True
        assert len(calls) == 2, f"Expected 2 gh calls (view+edit), got {len(calls)}: {calls}"

    def test_happy_path_one_view_one_edit(self):
        _, calls = self._run_with_disabled_dbwrite(["in-progress"], TicketState.SIT)
        view_calls = [c for c in calls if "view" in c]
        edit_calls = [c for c in calls if "edit" in c]
        assert len(view_calls) == 1
        assert len(edit_calls) == 1

    def test_no_post_edit_verification_view(self):
        """The only view call must precede the edit; there is no verify re-fetch."""
        _, calls = self._run_with_disabled_dbwrite(["in-progress"], TicketState.SIT)
        view_indices = [i for i, c in enumerate(calls) if "view" in c]
        edit_indices = [i for i, c in enumerate(calls) if "edit" in c]
        assert view_indices == [0]
        assert edit_indices == [1]


# ── AC2: retry-on-edit-failure preserved ──────────────────────────────────────

class TestRetryPreserved:
    def test_retry_succeeds_on_second_attempt(self):
        edit_responses = [_edit_fail(), _edit_ok()]
        edit_idx = 0
        sleep_calls = []

        def fake_run(cmd, **kwargs):
            nonlocal edit_idx
            if "view" in cmd:
                return _label_response("in-progress")
            r = edit_responses[edit_idx]
            edit_idx += 1
            return r

        with patch.object(state_machine, "_write_ticket_status"), \
             patch("services.sprint_manager.state_machine.subprocess.run", side_effect=fake_run), \
             patch("services.sprint_manager.state_machine.time") as mock_time:
            mock_time.sleep.side_effect = lambda s: sleep_calls.append(s)
            result = transition(_FAKE_ISSUE, TicketState.SIT, actor="test", repo=_FAKE_REPO)

        assert result is True
        assert edit_idx == 2
        assert sleep_calls == [1]

    def test_all_retries_exhausted_raises(self):
        def fake_run(cmd, **kwargs):
            if "view" in cmd:
                return _label_response("in-progress")
            return _edit_fail("network down")

        with patch.object(state_machine, "_write_ticket_status"), \
             patch("services.sprint_manager.state_machine.subprocess.run", side_effect=fake_run), \
             patch("services.sprint_manager.state_machine.time"):
            with pytest.raises(state_machine.TransitionError):
                transition(_FAKE_ISSUE, TicketState.SIT, actor="test", repo=_FAKE_REPO)

    def test_backoff_sequence_unchanged(self):
        sleep_calls = []

        def fake_run(cmd, **kwargs):
            if "view" in cmd:
                return _label_response("in-progress")
            return _edit_fail()

        with patch.object(state_machine, "_write_ticket_status"), \
             patch("services.sprint_manager.state_machine.subprocess.run", side_effect=fake_run), \
             patch("services.sprint_manager.state_machine.time") as mock_time:
            mock_time.sleep.side_effect = lambda s: sleep_calls.append(s)
            with pytest.raises(state_machine.TransitionError):
                transition(_FAKE_ISSUE, TicketState.SIT, actor="test", repo=_FAKE_REPO)

        assert sleep_calls == [1, 3, 7]


# ── AC3 + AC6: ticket_status row written via db layer ─────────────────────────

class TestTicketStatusWrite:
    def test_row_written_on_successful_transition(self, temp_db):
        _db, db_file = temp_db
        call_idx = 0

        def fake_run(cmd, **kwargs):
            nonlocal call_idx
            call_idx += 1
            if "view" in cmd:
                return _label_response("in-progress")
            return _edit_ok()

        with patch("services.sprint_manager.state_machine.subprocess.run", side_effect=fake_run), \
             patch("services.sprint_manager.state_machine.time"):
            result = transition(
                _FAKE_ISSUE, TicketState.SIT,
                actor="tester-bot", note="post-merge", repo=_FAKE_REPO,
            )

        assert result is True
        conn = sqlite3.connect(db_file)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM ticket_status WHERE issue = ? ORDER BY ts DESC", (str(_FAKE_ISSUE),)
        ).fetchall()
        conn.close()
        assert len(rows) == 1
        row = rows[0]
        assert str(row["issue"]) == str(_FAKE_ISSUE)
        assert row["status"] == TicketState.SIT.name
        assert row["actor"] == "tester-bot"
        assert row["note"] == "post-merge"
        assert row["ts"] is not None and row["ts"] != ""

    def test_table_created_by_init_db(self, temp_db):
        _db, db_file = temp_db
        conn = sqlite3.connect(db_file)
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        assert "ticket_status" in names

    def test_ts_is_utc_iso_format(self, temp_db):
        _db, db_file = temp_db

        def fake_run(cmd, **kwargs):
            if "view" in cmd:
                return _label_response("in-progress")
            return _edit_ok()

        with patch("services.sprint_manager.state_machine.subprocess.run", side_effect=fake_run), \
             patch("services.sprint_manager.state_machine.time"):
            transition(_FAKE_ISSUE, TicketState.SIT, actor="a", repo=_FAKE_REPO)

        conn = sqlite3.connect(db_file)
        ts = conn.execute(
            "SELECT ts FROM ticket_status WHERE issue = ?", (str(_FAKE_ISSUE),)
        ).fetchone()[0]
        conn.close()
        # Parseable as an ISO-8601 timestamp (UTC, no offset suffix like other tables).
        from datetime import datetime
        datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")

    def test_no_row_on_noop(self, temp_db):
        _db, db_file = temp_db
        with patch("services.sprint_manager.state_machine.subprocess.run") as mock_run, \
             patch("services.sprint_manager.state_machine.time"):
            mock_run.return_value = _label_response("SIT")
            result = transition(_FAKE_ISSUE, TicketState.SIT, actor="a", repo=_FAKE_REPO)
        assert result is False
        conn = sqlite3.connect(db_file)
        rows = conn.execute(
            "SELECT * FROM ticket_status WHERE issue = ?", (str(_FAKE_ISSUE),)
        ).fetchall()
        conn.close()
        assert rows == []


# ── AC4: DB write failure is swallowed, transition still True ──────────────────

class TestDbFailureSwallowed:
    def test_db_failure_returns_true(self, temp_db):
        _db, db_file = temp_db

        def fake_run(cmd, **kwargs):
            if "view" in cmd:
                return _label_response("in-progress")
            return _edit_ok()

        def boom(*a, **k):
            raise sqlite3.OperationalError("database is locked")

        with patch.object(_db, "record_ticket_status", side_effect=boom), \
             patch("services.sprint_manager.state_machine.subprocess.run", side_effect=fake_run), \
             patch("services.sprint_manager.state_machine.time"):
            result = transition(_FAKE_ISSUE, TicketState.SIT, actor="a", repo=_FAKE_REPO)

        assert result is True

    def test_db_failure_logs_structured_error(self, temp_db):
        _db, db_file = temp_db

        def fake_run(cmd, **kwargs):
            if "view" in cmd:
                return _label_response("in-progress")
            return _edit_ok()

        def boom(*a, **k):
            raise sqlite3.OperationalError("database is locked")

        with patch.object(_db, "record_ticket_status", side_effect=boom), \
             patch("services.sprint_manager.state_machine.subprocess.run", side_effect=fake_run), \
             patch("services.sprint_manager.state_machine.time"), \
             patch("services.sprint_manager.state_machine._LOG_AVAILABLE", True), \
             patch("services.sprint_manager.state_machine._log") as mock_log:
            transition(_FAKE_ISSUE, TicketState.SIT, actor="a", repo=_FAKE_REPO)

        error_events = [c.args[0] for c in mock_log.error.call_args_list]
        assert "db_write_failed" in error_events

    def test_db_failure_does_not_raise(self, temp_db):
        _db, db_file = temp_db

        def fake_run(cmd, **kwargs):
            if "view" in cmd:
                return _label_response("in-progress")
            return _edit_ok()

        def boom(*a, **k):
            raise RuntimeError("schema error")

        with patch.object(_db, "record_ticket_status", side_effect=boom), \
             patch("services.sprint_manager.state_machine.subprocess.run", side_effect=fake_run), \
             patch("services.sprint_manager.state_machine.time"):
            # Must not propagate.
            transition(_FAKE_ISSUE, TicketState.SIT, actor="a", repo=_FAKE_REPO)


# ── AC5: _log_transition remains intact ───────────────────────────────────────

class TestLoggingIntact:
    def test_transition_still_logs_actor_and_note(self, capsys):
        def fake_run(cmd, **kwargs):
            if "view" in cmd:
                return _label_response("in-progress")
            return _edit_ok()

        with patch.object(state_machine, "_write_ticket_status"), \
             patch("services.sprint_manager.state_machine.subprocess.run", side_effect=fake_run), \
             patch("services.sprint_manager.state_machine.time"), \
             patch("services.sprint_manager.state_machine._LOG_AVAILABLE", False):
            transition(
                _FAKE_ISSUE, TicketState.SIT,
                actor="tester-bot", note="post-merge", repo=_FAKE_REPO,
            )

        out = capsys.readouterr().out
        assert "tester-bot" in out
        assert "post-merge" in out
