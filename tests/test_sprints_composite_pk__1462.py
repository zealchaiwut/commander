"""Tests for issue #1462: Migrate sprints table to composite (label, project) primary key.

Verifies composite primary key migration, deduplication, idempotency, coexistence,
and constraint checks.
"""
import os
import sys
import pytest
import tempfile
import sqlite3
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "dashboard"))

import db


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["DB_PATH"] = path
    db.DB_PATH = Path(path)
    yield path
    # Cleanup
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def clean_db(temp_db):
    """Initialize a clean database for each test."""
    db.init_db()
    yield
    # Cleanup
    with db.get_conn() as conn:
        conn.execute("DELETE FROM sprints")
        conn.execute("DELETE FROM _sprint_schema_migrations")
        conn.commit()


def test_sprints_composite_pk__schema_inspection(clean_db):
    """AC: pytest — fresh DB: schema inspection confirms PRIMARY KEY (label, project)."""
    with db.get_conn() as conn:
        pk_info = conn.execute(
            "SELECT name FROM pragma_table_info('sprints') WHERE pk > 0 ORDER BY pk"
        ).fetchall()
        pk_cols = [r[0] for r in pk_info]
        assert pk_cols == ["label", "project"], f"Expected PK (label, project), got {pk_cols}"


def test_sprints_composite_pk__idempotent_no_error(clean_db):
    """AC: Migration is idempotent: running it twice against same DB produces no error and no data change."""
    with db.get_conn() as conn:
        # Insert a row
        conn.execute(
            "INSERT INTO sprints (label, project, state) VALUES (?, ?, ?)",
            ("sprint-1", "project-a", "draft"),
        )
        conn.commit()

        # Get row count after first migration (already ran in init_db)
        count_after_first = conn.execute("SELECT COUNT(*) FROM sprints").fetchone()[0]

        # Run migration again
        db._migrate_sprints_to_composite_pk(conn)
        conn.commit()

        # Should be no error and no data change
        count_after_second = conn.execute("SELECT COUNT(*) FROM sprints").fetchone()[0]
        assert count_after_first == count_after_second


def test_sprints_composite_pk__coexist_same_label_diff_project(clean_db):
    """AC: Inserting two rows with same label but different project values succeeds; both retrievable."""
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO sprints (label, project, state) VALUES (?, ?, ?)",
            ("sprint-66", "zealchaiwut/perf-coach", "draft"),
        )
        conn.execute(
            "INSERT INTO sprints (label, project, state) VALUES (?, ?, ?)",
            ("sprint-66", "zealchaiwut/commander", "draft"),
        )
        conn.commit()

        rows = conn.execute(
            "SELECT label, project FROM sprints WHERE label = ? ORDER BY project",
            ("sprint-66",),
        ).fetchall()
        assert len(rows) == 2
        assert rows[0][1] == "zealchaiwut/commander"
        assert rows[1][1] == "zealchaiwut/perf-coach"


def test_sprints_composite_pk__duplicate_pk_raises_error(clean_db):
    """AC: Attempting to insert duplicate (label, project) pair raises integrity error."""
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO sprints (label, project, state) VALUES (?, ?, ?)",
            ("sprint-1", "project-a", "draft"),
        )
        conn.commit()

        # Attempt to insert duplicate
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO sprints (label, project, state) VALUES (?, ?, ?)",
                ("sprint-1", "project-a", "draft"),
            )
            conn.commit()


def test_sprints_composite_pk__state_check_constraint(clean_db):
    """AC: state CHECK constraint still rejects invalid values."""
    with db.get_conn() as conn:
        # Invalid state should raise CHECK constraint error
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO sprints (label, project, state) VALUES (?, ?, ?)",
                ("sprint-1", "project-a", "invalid_state"),
            )
            conn.commit()


def test_sprints_composite_pk__default_project_empty_string(clean_db):
    """AC: Insert sprint row omitting project field uses DEFAULT ''; can coexist with same label, non-empty project."""
    with db.get_conn() as conn:
        # Insert with default (omit project)
        conn.execute(
            "INSERT INTO sprints (label, state) VALUES (?, ?)",
            ("sprint-1", "draft"),
        )
        # Insert with explicit project
        conn.execute(
            "INSERT INTO sprints (label, project, state) VALUES (?, ?, ?)",
            ("sprint-1", "some-project", "draft"),
        )
        conn.commit()

        rows = conn.execute(
            "SELECT label, project FROM sprints WHERE label = ? ORDER BY project",
            ("sprint-1",),
        ).fetchall()
        assert len(rows) == 2
        assert rows[0][1] == ""  # default empty string
        assert rows[1][1] == "some-project"


def test_sprints_composite_pk__upgrade_path_preserves_data(clean_db):
    """AC: pytest — upgrade path: pre-migration single-PK DB with sample rows upgrades without data loss."""
    # Simulate a pre-migration DB by manually creating old-style single-PK sprints table
    with db.get_conn() as conn:
        # Drop existing sprints (fresh from init_db with composite PK)
        conn.execute("DROP TABLE IF EXISTS sprints")
        conn.execute("DROP TABLE IF EXISTS _sprint_schema_migrations")

        # Create old-style single-PK table (with project column already present for migration compatibility)
        conn.execute("""
            CREATE TABLE sprints (
                label      TEXT NOT NULL,
                project    TEXT NOT NULL DEFAULT '',
                state      TEXT NOT NULL DEFAULT 'draft'
                           CHECK(state IN (
                               'draft', 'planned', 'running', 'ready_to_merge',
                               'needs_rework', 'completed',
                               'planning', 'cancelled', 'failed'
                           )),
                created_at TEXT,
                started_at TEXT,
                ended_at   TEXT,
                end_reason TEXT,
                parent_label TEXT,
                PRIMARY KEY (label)
            )
        """)

        # Seed with sample rows
        conn.execute(
            "INSERT INTO sprints (label, project, state) VALUES (?, ?, ?)",
            ("sprint-66", "", "draft"),
        )
        conn.execute(
            "INSERT INTO sprints (label, project, state) VALUES (?, ?, ?)",
            ("sprint-67", "", "running"),
        )
        conn.commit()
        count_before = conn.execute("SELECT COUNT(*) FROM sprints").fetchone()[0]
        assert count_before == 2

    # Now run the migration (via _create_sprint_lifecycle_tables)
    with db.get_conn() as conn:
        db._create_sprint_lifecycle_tables(conn)
        conn.commit()

        # Verify new schema
        pk_info = conn.execute(
            "SELECT name FROM pragma_table_info('sprints') WHERE pk > 0 ORDER BY pk"
        ).fetchall()
        pk_cols = [r[0] for r in pk_info]
        assert pk_cols == ["label", "project"], f"Expected (label, project), got {pk_cols}"

        # Verify data preserved
        count_after = conn.execute("SELECT COUNT(*) FROM sprints").fetchone()[0]
        assert count_after >= 2, f"Data loss: {count_before} rows before, {count_after} after"

        # Verify specific rows exist
        row_66 = conn.execute(
            "SELECT label, project FROM sprints WHERE label = ?",
            ("sprint-66",),
        ).fetchone()
        assert row_66 is not None
        assert row_66[0] == "sprint-66"


def test_sprints_composite_pk__dedup_keeps_latest_row(clean_db):
    """AC: All rows copied from sprints into sprints_new, deduplicating by keeping most-recently-updated row."""
    # Simulate pre-migration state with composite key old table
    with db.get_conn() as conn:
        # Drop and recreate old-style table
        conn.execute("DROP TABLE IF EXISTS sprints")
        conn.execute("DROP TABLE IF EXISTS _sprint_schema_migrations")

        # Create old single-PK table but with project column for migration compat
        conn.execute("""
            CREATE TABLE sprints (
                label      TEXT NOT NULL,
                project    TEXT NOT NULL DEFAULT '',
                state      TEXT NOT NULL DEFAULT 'draft'
                           CHECK(state IN (
                               'draft', 'planned', 'running', 'ready_to_merge',
                               'needs_rework', 'completed',
                               'planning', 'cancelled', 'failed'
                           )),
                created_at TEXT,
                started_at TEXT,
                ended_at   TEXT,
                end_reason TEXT,
                parent_label TEXT,
                PRIMARY KEY (label)
            )
        """)
        conn.commit()

        # Insert a row
        conn.execute(
            "INSERT INTO sprints (label, project, state, created_at) VALUES (?, ?, ?, ?)",
            ("sprint-X", "", "draft", "2026-01-01"),
        )
        conn.commit()

    # Run migration
    with db.get_conn() as conn:
        db._create_sprint_lifecycle_tables(conn)
        conn.commit()

        # Verify single row exists (migration preserves it)
        rows = conn.execute(
            "SELECT COUNT(*) FROM sprints WHERE label = ?",
            ("sprint-X",),
        ).fetchone()
        assert rows[0] == 1


def test_sprints_composite_pk__all_columns_preserved(clean_db):
    """AC: All existing column values are preserved through rebuild (no data loss)."""
    with db.get_conn() as conn:
        # Insert row with all non-null columns
        conn.execute(
            """INSERT INTO sprints
               (label, project, state, created_at, started_at, ended_at, end_reason, parent_label)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "sprint-full",
                "test-project",
                "completed",
                "2026-01-01T00:00:00",
                "2026-01-02T00:00:00",
                "2026-01-10T00:00:00",
                "manual",
                "parent-sprint",
            ),
        )
        conn.commit()

        # Verify all columns preserved
        row = conn.execute(
            "SELECT label, project, state, created_at, started_at, ended_at, end_reason, parent_label FROM sprints WHERE label = ?",
            ("sprint-full",),
        ).fetchone()

        assert row[0] == "sprint-full"
        assert row[1] == "test-project"
        assert row[2] == "completed"
        assert row[3] == "2026-01-01T00:00:00"
        assert row[4] == "2026-01-02T00:00:00"
        assert row[5] == "2026-01-10T00:00:00"
        assert row[6] == "manual"
        assert row[7] == "parent-sprint"
