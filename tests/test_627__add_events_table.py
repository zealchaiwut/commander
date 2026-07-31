"""Tests for issue #627: Add events table and record_event write helper.

AC-1: Migration creates events table with correct columns
AC-2: Three indexes exist: (project, timestamp DESC), (project, target), (action_id)
AC-3: record_event() inserts exactly one row, returns without error
AC-4: Querying by project returns rows ordered newest-first by timestamp
AC-5: Rows sharing action_id retrievable with WHERE action_id = ?
AC-6: detail round-trips valid JSON without data loss
AC-7: Schema uses types compatible with both SQLite and Neon — no SQLite-only syntax
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")

import db as _db_module  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path):
    """Isolated SQLite DB pointed at tmp_path; yields patched db module."""
    db_file = tmp_path / "test_events_627.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


# ── AC-1: events table with correct columns ──────────────────────────────────

class TestEventsTableSchema:
    def test_events_table_exists(self, fresh_db):
        with fresh_db.get_conn() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='events'"
            ).fetchone()
        assert row is not None, "events table not found in fresh DB"

    def test_events_table_has_all_required_columns(self, fresh_db):
        with fresh_db.get_conn() as conn:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(events)").fetchall()}
        expected = {"id", "project", "timestamp", "source", "actor", "type", "target", "action_id", "detail"}
        assert expected == cols, f"Column mismatch. Got: {cols}"

    def test_project_is_not_null(self, fresh_db):
        with fresh_db.get_conn() as conn:
            with pytest.raises(Exception):
                conn.execute(
                    "INSERT INTO events (project, timestamp, source, actor, type, target, detail) "
                    "VALUES (NULL, datetime('now'), 'agent', 'bot', 't', 'x', '{}')"
                )

    def test_timestamp_is_not_null(self, fresh_db):
        with fresh_db.get_conn() as conn:
            with pytest.raises(Exception):
                conn.execute(
                    "INSERT INTO events (project, timestamp, source, actor, type, target, detail) "
                    "VALUES ('p', NULL, 'agent', 'bot', 't', 'x', '{}')"
                )

    def test_source_is_not_null(self, fresh_db):
        with fresh_db.get_conn() as conn:
            with pytest.raises(Exception):
                conn.execute(
                    "INSERT INTO events (project, timestamp, source, actor, type, target, detail) "
                    "VALUES ('p', datetime('now'), NULL, 'bot', 't', 'x', '{}')"
                )

    def test_actor_is_not_null(self, fresh_db):
        with fresh_db.get_conn() as conn:
            with pytest.raises(Exception):
                conn.execute(
                    "INSERT INTO events (project, timestamp, source, actor, type, target, detail) "
                    "VALUES ('p', datetime('now'), 'agent', NULL, 't', 'x', '{}')"
                )

    def test_type_is_not_null(self, fresh_db):
        with fresh_db.get_conn() as conn:
            with pytest.raises(Exception):
                conn.execute(
                    "INSERT INTO events (project, timestamp, source, actor, type, target, detail) "
                    "VALUES ('p', datetime('now'), 'agent', 'bot', NULL, 'x', '{}')"
                )

    def test_target_is_not_null(self, fresh_db):
        with fresh_db.get_conn() as conn:
            with pytest.raises(Exception):
                conn.execute(
                    "INSERT INTO events (project, timestamp, source, actor, type, target, detail) "
                    "VALUES ('p', datetime('now'), 'agent', 'bot', 't', NULL, '{}')"
                )

    def test_detail_is_not_null(self, fresh_db):
        with fresh_db.get_conn() as conn:
            with pytest.raises(Exception):
                conn.execute(
                    "INSERT INTO events (project, timestamp, source, actor, type, target, detail) "
                    "VALUES ('p', datetime('now'), 'agent', 'bot', 't', 'x', NULL)"
                )

    def test_action_id_is_nullable(self, fresh_db):
        with fresh_db.get_conn() as conn:
            conn.execute(
                "INSERT INTO events (project, timestamp, source, actor, type, target, detail) "
                "VALUES ('p', datetime('now'), 'agent', 'bot', 't', 'x', '{}')"
            )
            count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        assert count == 1, "action_id should be nullable — insert without it must succeed"

    def test_source_restricted_to_valid_values(self, fresh_db):
        """source CHECK constraint rejects values outside agent/dashboard/github."""
        with fresh_db.get_conn() as conn:
            with pytest.raises(Exception):
                conn.execute(
                    "INSERT INTO events (project, timestamp, source, actor, type, target, detail) "
                    "VALUES ('p', datetime('now'), 'invalid_source', 'bot', 't', 'x', '{}')"
                )

    def test_source_accepts_agent(self, fresh_db):
        with fresh_db.get_conn() as conn:
            conn.execute(
                "INSERT INTO events (project, timestamp, source, actor, type, target, detail) "
                "VALUES ('p', datetime('now'), 'agent', 'bot', 't', 'x', '{}')"
            )

    def test_source_accepts_dashboard(self, fresh_db):
        with fresh_db.get_conn() as conn:
            conn.execute(
                "INSERT INTO events (project, timestamp, source, actor, type, target, detail) "
                "VALUES ('p', datetime('now'), 'dashboard', 'bot', 't', 'x', '{}')"
            )

    def test_source_accepts_github(self, fresh_db):
        with fresh_db.get_conn() as conn:
            conn.execute(
                "INSERT INTO events (project, timestamp, source, actor, type, target, detail) "
                "VALUES ('p', datetime('now'), 'github', 'bot', 't', 'x', '{}')"
            )


# ── AC-2: three indexes exist ─────────────────────────────────────────────────

class TestEventsTableIndexes:
    def _index_list(self, fresh_db) -> list[str]:
        with fresh_db.get_conn() as conn:
            rows = conn.execute("PRAGMA index_list('events')").fetchall()
        return [r["name"] for r in rows]

    def _index_info(self, fresh_db, idx_name: str) -> list[str]:
        with fresh_db.get_conn() as conn:
            rows = conn.execute(f"PRAGMA index_info('{idx_name}')").fetchall()
        return [r["name"] for r in rows]

    def test_three_indexes_exist(self, fresh_db):
        indexes = self._index_list(fresh_db)
        # 3 explicit indexes (SQLite may add one for PK, filter to ix_ prefix)
        named = [i for i in indexes if not i.startswith("sqlite_")]
        assert len(named) >= 3, f"Expected >= 3 named indexes, got: {named}"

    def test_index_on_project_timestamp(self, fresh_db):
        indexes = self._index_list(fresh_db)
        found = False
        for idx in indexes:
            cols = self._index_info(fresh_db, idx)
            if "project" in cols and "timestamp" in cols:
                found = True
                break
        assert found, f"No index covering (project, timestamp) found. Indexes: {indexes}"

    def test_index_on_project_target(self, fresh_db):
        indexes = self._index_list(fresh_db)
        found = False
        for idx in indexes:
            cols = self._index_info(fresh_db, idx)
            if "project" in cols and "target" in cols:
                found = True
                break
        assert found, f"No index covering (project, target) found. Indexes: {indexes}"

    def test_index_on_action_id(self, fresh_db):
        indexes = self._index_list(fresh_db)
        found = False
        for idx in indexes:
            cols = self._index_info(fresh_db, idx)
            if cols == ["action_id"]:
                found = True
                break
        assert found, f"No index on (action_id) found. Indexes: {indexes}"


# ── AC-3: record_event inserts exactly one row ───────────────────────────────

class TestRecordEvent:
    def test_record_event_inserts_one_row(self, fresh_db):
        fresh_db.record_event("proj-1", "agent", "coder-bot", "sprint_started", "sprint-47", {"sprint": "sprint-47"})
        with fresh_db.get_conn() as conn:
            count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        assert count == 1

    def test_record_event_returns_without_error(self, fresh_db):
        result = fresh_db.record_event("proj-1", "agent", "coder-bot", "sprint_started", "sprint-47", {})
        # Returns None (implicit return) — just verify no exception raised
        assert result is None

    def test_record_event_populates_all_non_nullable_columns(self, fresh_db):
        fresh_db.record_event("proj-1", "agent", "coder-bot", "sprint_started", "sprint-47", {"key": "val"})
        with fresh_db.get_conn() as conn:
            row = conn.execute("SELECT * FROM events").fetchone()
        assert row["project"] == "proj-1"
        assert row["source"] == "agent"
        assert row["actor"] == "coder-bot"
        assert row["type"] == "sprint_started"
        assert row["target"] == "sprint-47"
        assert row["action_id"] is None

    def test_record_event_action_id_defaults_to_none(self, fresh_db):
        fresh_db.record_event("p", "agent", "bot", "t", "x", {})
        with fresh_db.get_conn() as conn:
            row = conn.execute("SELECT action_id FROM events").fetchone()
        assert row["action_id"] is None

    def test_record_event_stores_action_id_when_provided(self, fresh_db):
        fresh_db.record_event("p", "agent", "bot", "t", "x", {}, action_id="abc-123")
        with fresh_db.get_conn() as conn:
            row = conn.execute("SELECT action_id FROM events").fetchone()
        assert row["action_id"] == "abc-123"

    def test_record_event_timestamp_is_populated(self, fresh_db):
        fresh_db.record_event("p", "agent", "bot", "t", "x", {})
        with fresh_db.get_conn() as conn:
            row = conn.execute("SELECT timestamp FROM events").fetchone()
        assert row["timestamp"] is not None and row["timestamp"] != ""


# ── AC-4: query by project returns newest-first ───────────────────────────────

class TestProjectOrdering:
    def test_events_ordered_newest_first(self, fresh_db):
        """Insert three events with distinct timestamps, query by project → newest first."""
        base = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        t1 = (base).strftime("%Y-%m-%dT%H:%M:%S")
        t2 = (base + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
        t3 = (base + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")

        with fresh_db.get_conn() as conn:
            for ts in [t2, t1, t3]:  # insert out of order
                conn.execute(
                    "INSERT INTO events (project, timestamp, source, actor, type, target, detail) "
                    "VALUES (?, ?, 'agent', 'bot', 't', 'x', '{}')",
                    ("proj-1", ts),
                )
            conn.commit()
            rows = conn.execute(
                "SELECT timestamp FROM events WHERE project = 'proj-1' ORDER BY timestamp DESC"
            ).fetchall()

        timestamps = [r["timestamp"] for r in rows]
        assert timestamps == sorted(timestamps, reverse=True), (
            f"Events not in descending timestamp order: {timestamps}"
        )

    def test_project_filter_isolates_rows(self, fresh_db):
        """Inserting events for proj-1 and proj-2; querying proj-1 returns only proj-1 rows."""
        with fresh_db.get_conn() as conn:
            conn.execute(
                "INSERT INTO events (project, timestamp, source, actor, type, target, detail) "
                "VALUES (?, datetime('now'), 'agent', 'bot', 't', 'x', '{}')",
                ("proj-1",),
            )
            conn.execute(
                "INSERT INTO events (project, timestamp, source, actor, type, target, detail) "
                "VALUES (?, datetime('now'), 'agent', 'bot', 't', 'x', '{}')",
                ("proj-2",),
            )
            conn.commit()
            rows = conn.execute(
                "SELECT project FROM events WHERE project = 'proj-1'"
            ).fetchall()

        assert all(r["project"] == "proj-1" for r in rows)
        assert len(rows) == 1


# ── AC-5: action_id grouping ─────────────────────────────────────────────────

class TestActionIdGrouping:
    def test_two_rows_with_same_action_id_retrievable(self, fresh_db):
        fresh_db.record_event("p", "agent", "bot", "t", "x", {}, action_id="abc-123")
        fresh_db.record_event("p", "dashboard", "bot", "t", "y", {}, action_id="abc-123")
        fresh_db.record_event("p", "agent", "bot", "t", "z", {}, action_id="xyz-999")

        with fresh_db.get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE action_id = ?", ("abc-123",)
            ).fetchall()

        assert len(rows) == 2, f"Expected 2 rows for action_id='abc-123', got {len(rows)}"
        assert all(r["action_id"] == "abc-123" for r in rows)


# ── AC-6: detail JSON round-trip ─────────────────────────────────────────────

class TestDetailJsonRoundTrip:
    def test_simple_dict_round_trips(self, fresh_db):
        detail = {"sprint": "sprint-47"}
        fresh_db.record_event("proj-1", "agent", "coder-bot", "sprint_started", "sprint-47", detail)
        with fresh_db.get_conn() as conn:
            row = conn.execute("SELECT detail FROM events").fetchone()
        assert json.loads(row["detail"]) == detail

    def test_nested_json_round_trips(self, fresh_db):
        detail = {"pr": 598, "labels": ["bug", "sprint-49"]}
        fresh_db.record_event("p", "github", "webhook", "pr_opened", "pr-598", detail)
        with fresh_db.get_conn() as conn:
            row = conn.execute("SELECT detail FROM events").fetchone()
        assert json.loads(row["detail"]) == detail

    def test_empty_dict_round_trips(self, fresh_db):
        fresh_db.record_event("p", "agent", "bot", "t", "x", {})
        with fresh_db.get_conn() as conn:
            row = conn.execute("SELECT detail FROM events").fetchone()
        assert json.loads(row["detail"]) == {}


# ── AC-7: schema uses cross-compatible types ──────────────────────────────────

class TestSchemaCompatibility:
    def test_alembic_migration_file_exists(self):
        migration = REPO_ROOT / "alembic" / "versions" / "0006_add_events_table.py"
        assert migration.exists(), "Alembic migration 0006_add_events_table.py not found"

    def test_migration_uses_sqlalchemy_types_not_raw_ddl(self):
        migration = REPO_ROOT / "alembic" / "versions" / "0006_add_events_table.py"
        src = migration.read_text()
        assert "import sqlalchemy as sa" in src or "from sqlalchemy" in src, (
            "Migration must use SQLAlchemy types for cross-DB compatibility"
        )
        assert "op.create_table" in src, "Migration must use op.create_table"

    def test_migration_no_sqlite_autoincrement_keyword(self):
        """AUTOINCREMENT is SQLite-only; migrations must use sa.Integer() autoincrement."""
        migration = REPO_ROOT / "alembic" / "versions" / "0006_add_events_table.py"
        src = migration.read_text()
        assert "AUTOINCREMENT" not in src, (
            "Migration must not use SQLite-only AUTOINCREMENT keyword"
        )

    def test_db_py_events_table_no_sqlite_only_types(self):
        """The events table DDL in db.py must not use AUTOINCREMENT (SQLite-only)."""
        db_src = (REPO_ROOT / "apps" / "dashboard" / "db.py").read_text()
        # Find the CREATE TABLE events block and check it doesn't use AUTOINCREMENT
        # (SQLite uses INTEGER PRIMARY KEY for autoincrement without the keyword;
        # PostgreSQL uses SERIAL or IDENTITY — avoid AUTOINCREMENT in shared DDL)
        # We allow it in session_events (legacy table); check the new events table
        events_block_start = db_src.find("CREATE TABLE IF NOT EXISTS events (")
        if events_block_start != -1:
            events_block = db_src[events_block_start:events_block_start + 500]
            assert "AUTOINCREMENT" not in events_block, (
                "New events table DDL must not use AUTOINCREMENT"
            )
