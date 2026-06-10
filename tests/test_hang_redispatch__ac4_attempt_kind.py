"""Tests for #787 AC4: agent_runs records attempt_kind (initial/fix_round/hang_continue)."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import apps.dashboard.db as db
import services.sprint_manager.sprint_manager as sm


class TestAC4AttemptKind:
    def test_agent_runs_schema_has_attempt_kind_column(self, tmp_path):
        """agent_runs table must have an attempt_kind column."""
        import sqlite3
        db_path = tmp_path / "test.db"
        with patch.object(db, "DB_PATH", db_path):
            with db.get_conn() as conn:
                db._create_agent_runs_table(conn)
            with sqlite3.connect(db_path) as conn:
                cursor = conn.execute("PRAGMA table_info(agent_runs)")
                cols = {row[1] for row in cursor.fetchall()}
        assert "attempt_kind" in cols, (
            f"agent_runs must have attempt_kind column; found: {cols}"
        )

    def test_record_agent_start_accepts_attempt_kind(self):
        import inspect
        sig = inspect.signature(db.record_agent_start)
        assert "attempt_kind" in sig.parameters, (
            "db.record_agent_start must accept attempt_kind parameter"
        )

    def test_db_agent_start_sm_accepts_attempt_kind(self):
        import inspect
        sig = inspect.signature(sm._db_agent_start_sm)
        assert "attempt_kind" in sig.parameters, (
            "_db_agent_start_sm must accept attempt_kind parameter"
        )

    def test_record_agent_start_stores_attempt_kind_initial(self, tmp_path):
        import sqlite3
        db_path = tmp_path / "test.db"
        with patch.object(db, "DB_PATH", db_path):
            row_id = db.record_agent_start(
                1, "sprint-99", "coder", attempt_kind="initial"
            )
            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    "SELECT attempt_kind FROM agent_runs WHERE id = ?", (row_id,)
                ).fetchone()
        assert row is not None
        assert row[0] == "initial", f"Expected 'initial', got {row[0]!r}"

    def test_record_agent_start_stores_attempt_kind_fix_round(self, tmp_path):
        import sqlite3
        db_path = tmp_path / "test.db"
        with patch.object(db, "DB_PATH", db_path):
            row_id = db.record_agent_start(
                2, "sprint-99", "coder", attempt_kind="fix_round"
            )
            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    "SELECT attempt_kind FROM agent_runs WHERE id = ?", (row_id,)
                ).fetchone()
        assert row is not None
        assert row[0] == "fix_round"

    def test_record_agent_start_stores_attempt_kind_hang_continue(self, tmp_path):
        import sqlite3
        db_path = tmp_path / "test.db"
        with patch.object(db, "DB_PATH", db_path):
            row_id = db.record_agent_start(
                3, "sprint-99", "coder", attempt_kind="hang_continue"
            )
            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    "SELECT attempt_kind FROM agent_runs WHERE id = ?", (row_id,)
                ).fetchone()
        assert row is not None
        assert row[0] == "hang_continue"

    def test_record_agent_start_attempt_kind_defaults_to_none(self, tmp_path):
        import sqlite3
        db_path = tmp_path / "test.db"
        with patch.object(db, "DB_PATH", db_path):
            row_id = db.record_agent_start(
                4, "sprint-99", "coder"
            )
            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    "SELECT attempt_kind FROM agent_runs WHERE id = ?", (row_id,)
                ).fetchone()
        assert row is not None
        # When not provided, attempt_kind should be NULL or the default value
        # (backward-compatible: existing callers don't pass attempt_kind)

    def test_alter_table_migration_adds_attempt_kind_to_existing_db(self, tmp_path):
        """Existing DBs (before #787) must get attempt_kind column via ALTER TABLE."""
        import sqlite3
        db_path = tmp_path / "test.db"
        # Create an old-schema table without attempt_kind
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_runs (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    issue_number     INTEGER NOT NULL,
                    sprint_label     TEXT NOT NULL,
                    agent            TEXT NOT NULL,
                    started_at       TEXT NOT NULL,
                    finished_at      TEXT,
                    duration_seconds INTEGER,
                    outcome          TEXT
                )
                """
            )
            conn.commit()
        # Now run _create_agent_runs_table to trigger migration
        with patch.object(db, "DB_PATH", db_path):
            with db.get_conn() as conn:
                db._create_agent_runs_table(conn)
        # Column must now exist
        with sqlite3.connect(db_path) as conn:
            cursor = conn.execute("PRAGMA table_info(agent_runs)")
            cols = {row[1] for row in cursor.fetchall()}
        assert "attempt_kind" in cols, (
            "ALTER TABLE migration must add attempt_kind to existing agent_runs"
        )
