"""Tests for issue #1462: Migrate sprints table to composite (label, project) primary key.

AC1  — sprints_new created with PRIMARY KEY (label, project), all columns, state CHECK.
AC2  — Rows deduped on (label, project) keeping most-recently-updated (highest rowid).
AC3  — sprints dropped, sprints_new renamed to sprints; indexes recreated.
AC4  — Rebuild gated on schema-version check; runs exactly once, no-op after.
AC5  — Runs inside _create_sprint_lifecycle_tables after project backfill.
AC6  — _migrate_sprints_state_check and _migrate_sprints_run_artifacts cooperate.
AC7  — ('sprint-66','zealchaiwut/perf-coach') and ('sprint-66','zealchaiwut/commander') coexist.
AC8  — state CHECK constraint still rejects values outside allowed set.
AC9  — Migration is idempotent: twice against same DB → no error, no data change.
AC10 — All existing column values preserved through the rebuild.
AC11 — pytest/fresh DB: schema inspection confirms PRIMARY KEY (label, project).
AC12 — pytest/upgrade path: pre-migration single-PK DB upgrades without data loss.
AC13 — pytest/coexistence: same label, different projects succeed; dup (label,project) raises.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"
for _p in (str(_REPO_ROOT), str(_DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import db  # noqa: E402

_COMMANDER = "zealchaiwut/commander"
_PERF_COACH = "zealchaiwut/perf-coach"
_LABEL = "sprint-66"


@pytest.fixture
def fresh_db(tmp_path):
    """Fresh isolated DB per test via init_db()."""
    db_file = tmp_path / "test_1462.db"
    original = db.DB_PATH
    db.DB_PATH = str(db_file)
    db.init_db()
    yield str(db_file)
    db.DB_PATH = original


def _build_old_single_pk_db(db_path: str, rows: list[tuple]) -> None:
    """Seed a DB with the legacy single-column label PRIMARY KEY schema."""
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE sprints (
            label        TEXT PRIMARY KEY,
            project      TEXT NOT NULL DEFAULT '',
            state        TEXT NOT NULL DEFAULT 'draft',
            created_at   TEXT,
            started_at   TEXT,
            ended_at     TEXT,
            end_reason   TEXT,
            parent_label TEXT
        )
    """)
    for row in rows:
        conn.execute(
            "INSERT INTO sprints (label, project, state, created_at) VALUES (?, ?, ?, ?)",
            row,
        )
    conn.commit()
    conn.close()


# ── AC11: Fresh DB has composite PK ───────────────────────────────────────────

class TestFreshDBCompositeKey:
    """AC11: Schema inspection confirms PRIMARY KEY (label, project) on fresh DB."""

    def test_pk_columns_are_label_and_project(self, fresh_db):
        with db.get_conn() as conn:
            info = conn.execute("PRAGMA table_info(sprints)").fetchall()
        pk_cols = sorted(r[1] for r in info if r[5] > 0)
        assert pk_cols == ["label", "project"], (
            f"Expected composite PK (label, project), got PK cols: {pk_cols}"
        )

    def test_schema_version_recorded(self, fresh_db):
        """Migration version is written to _sprint_schema_migrations after init."""
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT version FROM _sprint_schema_migrations WHERE version = ?",
                (db._SPRINTS_COMPOSITE_PK_VERSION,),
            ).fetchone()
        assert row is not None, (
            "_sprint_schema_migrations must record version after migration"
        )


# ── AC1: sprints_new carries all columns + state CHECK ────────────────────────

class TestSprintsNewShape:
    """AC1: sprints_new created with composite PK, all existing columns, state CHECK."""

    def test_sprints_new_not_left_behind(self, fresh_db):
        """After successful migration sprints_new must not remain as a leftover table."""
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='sprints_new'"
            ).fetchone()
        assert row is None, "sprints_new must be renamed/dropped — no leftover allowed"

    def test_final_table_has_run_artifact_columns(self, fresh_db):
        """All run-artifact columns from _RUN_ARTIFACT_COLUMNS exist on sprints."""
        with db.get_conn() as conn:
            existing = {r[1] for r in conn.execute("PRAGMA table_info(sprints)").fetchall()}
        for col, _ in db._RUN_ARTIFACT_COLUMNS:
            assert col in existing, f"Run-artifact column '{col}' missing from sprints"


# ── AC2: Deduplication keeps most-recently-updated row ────────────────────────

class TestDeduplication:
    """AC2: When (label, project) duplicates exist, keep the row with highest rowid.

    Tests call _migrate_sprints_to_composite_pk directly on a raw connection
    so that _migrate_sprints_state_check ordering does not interfere.
    """

    def _raw_conn_with_dups(self, db_path: str) -> sqlite3.Connection:
        """Return an open connection seeded with two duplicate (label, project) rows."""
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        # No PK constraint so we can insert duplicates
        conn.execute("""
            CREATE TABLE sprints (
                label        TEXT NOT NULL,
                project      TEXT NOT NULL DEFAULT '',
                state        TEXT NOT NULL DEFAULT 'draft',
                created_at   TEXT,
                started_at   TEXT,
                ended_at     TEXT,
                end_reason   TEXT,
                parent_label TEXT
            )
        """)
        conn.execute(
            "INSERT INTO sprints (label, project, state) VALUES (?, ?, 'running')",
            (_LABEL, _COMMANDER),
        )
        conn.execute(
            "INSERT INTO sprints (label, project, state) VALUES (?, ?, 'completed')",
            (_LABEL, _COMMANDER),
        )
        conn.commit()
        return conn

    def test_dedup_keeps_exactly_one_row(self, tmp_path):
        """Two rows with same (label, project) → migration keeps exactly one."""
        conn = self._raw_conn_with_dups(str(tmp_path / "dup.db"))
        db._migrate_sprints_to_composite_pk(conn)
        conn.commit()
        count = conn.execute(
            "SELECT COUNT(*) FROM sprints WHERE label = ? AND project = ?",
            (_LABEL, _COMMANDER),
        ).fetchone()[0]
        conn.close()
        assert count == 1, f"Dedup must yield exactly 1 row per (label, project), got {count}"

    def test_dedup_keeps_highest_rowid_row(self, tmp_path):
        """The retained row is the last-written one (state='completed', not 'running')."""
        conn = self._raw_conn_with_dups(str(tmp_path / "dup2.db"))
        db._migrate_sprints_to_composite_pk(conn)
        conn.commit()
        row = conn.execute(
            "SELECT state FROM sprints WHERE label = ? AND project = ?",
            (_LABEL, _COMMANDER),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["state"] == "completed", (
            "Dedup must keep the highest-rowid row (last written = 'completed')"
        )


# ── AC4: Schema-version gating (runs exactly once) ────────────────────────────

class TestSchemaVersionGating:
    """AC4: Migration runs exactly once; subsequent call is a no-op."""

    def test_calling_lifecycle_twice_raises_no_error(self, fresh_db):
        """Running _create_sprint_lifecycle_tables twice must not raise."""
        with db.get_conn() as conn:
            db._create_sprint_lifecycle_tables(conn)
            db._create_sprint_lifecycle_tables(conn)

    def test_version_appears_exactly_once_after_double_call(self, fresh_db):
        """Version record must appear exactly once even after two lifecycle calls."""
        with db.get_conn() as conn:
            db._create_sprint_lifecycle_tables(conn)
            db._create_sprint_lifecycle_tables(conn)
            count = conn.execute(
                "SELECT COUNT(*) FROM _sprint_schema_migrations WHERE version = ?",
                (db._SPRINTS_COMPOSITE_PK_VERSION,),
            ).fetchone()[0]
        assert count == 1, f"Version must be recorded exactly once, got {count}"

    def test_table_structure_unchanged_after_second_call(self, fresh_db):
        """Second lifecycle call must not alter the sprints table structure."""
        with db.get_conn() as conn:
            info_before = conn.execute("PRAGMA table_info(sprints)").fetchall()
            db._create_sprint_lifecycle_tables(conn)
            info_after = conn.execute("PRAGMA table_info(sprints)").fetchall()
        assert info_before == info_after, "Table structure must be identical after second call"


# ── AC6: Cooperative migrations ───────────────────────────────────────────────

class TestCooperativeMigrations:
    """AC6: _migrate_sprints_state_check and _migrate_sprints_run_artifacts cooperate."""

    def test_state_check_is_noop_after_composite_pk(self, fresh_db):
        """_migrate_sprints_state_check must not alter the table after composite PK."""
        with db.get_conn() as conn:
            sql_before = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='sprints'"
            ).fetchone()[0]
            db._migrate_sprints_state_check(conn)
            sql_after = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='sprints'"
            ).fetchone()[0]
        assert sql_before == sql_after, (
            "_migrate_sprints_state_check must be a no-op when composite PK is present"
        )

    def test_run_artifacts_noop_after_migration(self, fresh_db):
        """_migrate_sprints_run_artifacts must not raise when columns already exist."""
        with db.get_conn() as conn:
            db._migrate_sprints_run_artifacts(conn)  # must not raise

    def test_all_run_artifact_columns_present(self, fresh_db):
        """All run-artifact columns exist after migration — no duplicate-column errors."""
        with db.get_conn() as conn:
            existing = {r[1] for r in conn.execute("PRAGMA table_info(sprints)").fetchall()}
        missing = {col for col, _ in db._RUN_ARTIFACT_COLUMNS} - existing
        assert not missing, f"Run-artifact columns missing: {missing}"


# ── AC7 / AC13: Coexistence and uniqueness ────────────────────────────────────

class TestCoexistence:
    """AC7/AC13: Same label, different projects coexist; dup (label,project) raises."""

    def test_perf_coach_and_commander_coexist(self, fresh_db):
        """('sprint-66','perf-coach') and ('sprint-66','commander') coexist."""
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO sprints (label, project, state) VALUES (?, ?, 'draft')",
                (_LABEL, _COMMANDER),
            )
            conn.execute(
                "INSERT INTO sprints (label, project, state) VALUES (?, ?, 'draft')",
                (_LABEL, _PERF_COACH),
            )
        with db.get_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM sprints WHERE label = ?", (_LABEL,)
            ).fetchone()[0]
        assert count == 2, f"Both projects must coexist for label {_LABEL!r}, got {count}"

    def test_duplicate_label_project_raises_integrity_error(self, fresh_db):
        """Inserting a duplicate (label, project) pair raises IntegrityError."""
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO sprints (label, project, state) VALUES (?, ?, 'draft')",
                (_LABEL, _COMMANDER),
            )
        with pytest.raises(sqlite3.IntegrityError):
            with db.get_conn() as conn:
                conn.execute(
                    "INSERT INTO sprints (label, project, state) VALUES (?, ?, 'draft')",
                    (_LABEL, _COMMANDER),
                )


# ── AC8: CHECK constraint ─────────────────────────────────────────────────────

class TestStateCheckConstraint:
    """AC8: state CHECK constraint still rejects values outside the allowed set."""

    def test_invalid_state_raises_integrity_error(self, fresh_db):
        with pytest.raises(sqlite3.IntegrityError):
            with db.get_conn() as conn:
                conn.execute(
                    "INSERT INTO sprints (label, project, state) "
                    "VALUES ('sprint-x', '', 'invalid_state')"
                )

    def test_valid_states_are_accepted(self, fresh_db):
        """All allowed state values can be inserted without error."""
        allowed = (
            "draft", "planned", "running", "ready_to_merge",
            "needs_rework", "completed", "planning", "cancelled", "failed",
        )
        for i, state in enumerate(allowed):
            with db.get_conn() as conn:
                conn.execute(
                    "INSERT INTO sprints (label, project, state) VALUES (?, ?, ?)",
                    (f"sprint-chk-{i}", _COMMANDER, state),
                )


# ── AC9: Idempotent ──────────────────────────────────────────────────────────

class TestIdempotent:
    """AC9: Running migration twice produces no error and no data change."""

    def test_double_init_db_no_error(self, fresh_db):
        """Calling init_db() a second time against an already-migrated DB raises no error."""
        original = db.DB_PATH
        db.DB_PATH = fresh_db
        try:
            db.init_db()
        finally:
            db.DB_PATH = original

    def test_data_unchanged_after_second_init(self, fresh_db):
        """Rows seeded after first init survive a second init_db() call unchanged."""
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO sprints (label, project, state) VALUES ('sprint-42', ?, 'draft')",
                (_COMMANDER,),
            )
        original = db.DB_PATH
        db.DB_PATH = fresh_db
        try:
            db.init_db()
        finally:
            db.DB_PATH = original

        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT label FROM sprints WHERE label = 'sprint-42' AND project = ?",
                (_COMMANDER,),
            ).fetchone()
        assert row is not None, "sprint-42 must survive a second init_db() call"


# ── AC10: Data preservation ───────────────────────────────────────────────────

class TestDataPreservation:
    """AC10: All existing column values are preserved through the rebuild."""

    def test_all_column_values_preserved(self, tmp_path):
        """Seeded row values survive the composite PK migration intact."""
        db_path = str(tmp_path / "preserve.db")
        _build_old_single_pk_db(
            db_path,
            [("sprint-99", _COMMANDER, "completed", "2026-01-01")],
        )
        # Also set extra fields via a direct connection update
        conn0 = sqlite3.connect(db_path)
        conn0.execute(
            "UPDATE sprints SET started_at=?, ended_at=?, end_reason=?, parent_label=? "
            "WHERE label='sprint-99'",
            ("2026-01-02", "2026-01-10", "done", "sprint-99"),
        )
        conn0.commit()
        conn0.close()

        original = db.DB_PATH
        db.DB_PATH = db_path
        try:
            db.init_db()
        finally:
            db.DB_PATH = original

        conn2 = sqlite3.connect(db_path)
        conn2.row_factory = sqlite3.Row
        row = conn2.execute(
            "SELECT * FROM sprints WHERE label='sprint-99'"
        ).fetchone()
        conn2.close()

        assert row is not None, "Row must survive migration"
        assert row["state"] == "completed"
        assert row["created_at"] == "2026-01-01"
        assert row["started_at"] == "2026-01-02"
        assert row["ended_at"] == "2026-01-10"
        assert row["end_reason"] == "done"
        assert row["parent_label"] == "sprint-99"
        assert row["project"] == _COMMANDER


# ── AC12: Upgrade path ────────────────────────────────────────────────────────

class TestUpgradePath:
    """AC12: Pre-migration single-PK DB seeded with rows upgrades without data loss."""

    def test_all_rows_present_after_upgrade(self, tmp_path):
        db_path = str(tmp_path / "upgrade.db")
        _build_old_single_pk_db(db_path, [
            ("sprint-10", _COMMANDER, "completed", "2026-01-01"),
            ("sprint-11", _PERF_COACH, "running", "2026-02-01"),
        ])

        original = db.DB_PATH
        db.DB_PATH = db_path
        try:
            db.init_db()
        finally:
            db.DB_PATH = original

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT label FROM sprints ORDER BY label").fetchall()
        conn.close()

        labels = [r["label"] for r in rows]
        assert "sprint-10" in labels, "sprint-10 must survive upgrade"
        assert "sprint-11" in labels, "sprint-11 must survive upgrade"
        assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"

    def test_composite_pk_present_after_upgrade(self, tmp_path):
        db_path = str(tmp_path / "upgrade_pk.db")
        _build_old_single_pk_db(db_path, [
            ("sprint-20", _COMMANDER, "draft", "2026-03-01"),
        ])

        original = db.DB_PATH
        db.DB_PATH = db_path
        try:
            db.init_db()
        finally:
            db.DB_PATH = original

        conn = sqlite3.connect(db_path)
        info = conn.execute("PRAGMA table_info(sprints)").fetchall()
        conn.close()
        pk_cols = sorted(r[1] for r in info if r[5] > 0)
        assert pk_cols == ["label", "project"], (
            f"Upgraded DB must have composite PK, got: {pk_cols}"
        )

    def test_same_label_different_projects_coexist_after_upgrade(self, tmp_path):
        """After upgrading, two rows sharing a label but different projects can coexist."""
        db_path = str(tmp_path / "upgrade_coexist.db")
        # Old schema can only have one row per label — seed one
        _build_old_single_pk_db(db_path, [
            ("sprint-66", _COMMANDER, "completed", "2026-01-01"),
        ])

        original = db.DB_PATH
        db.DB_PATH = db_path
        try:
            db.init_db()
        finally:
            db.DB_PATH = original

        # After upgrade, insert perf-coach row for same label
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO sprints (label, project, state) VALUES (?, ?, 'running')",
            ("sprint-66", _PERF_COACH),
        )
        conn.commit()
        count = conn.execute(
            "SELECT COUNT(*) FROM sprints WHERE label='sprint-66'"
        ).fetchone()[0]
        conn.close()
        assert count == 2, "Both projects must coexist after upgrade"
