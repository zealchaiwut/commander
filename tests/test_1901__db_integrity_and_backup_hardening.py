"""Tests for issue #1901: DB integrity check + WAL/local-backup hardening.

Acceptance criteria covered:

  AC2   On startup and on caught OperationalError "disk I/O error", run
        PRAGMA integrity_check; if corrupt, fail loudly with a clear operator
        message that includes the restore path if a backup exists.
  AC3   Periodic integrity-safe local backup (hourly, N-deep) so corruption is
        never more than ~1h of telemetry from recovery.
  AC4   WAL mode + busy_timeout set on every connection; connections are closed
        on context manager exit (fd-leak fix, coordinates with #1749).
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))

import db  # noqa: E402
import backup  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_valid_db(path: Path) -> None:
    """Create a minimal valid SQLite DB at path."""
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
    conn.execute("INSERT INTO t (val) VALUES ('hello')")
    conn.commit()
    conn.close()


def _make_corrupt_db(path: Path) -> None:
    """Write a file that looks like a DB but is truncated/corrupt."""
    path.write_bytes(b"SQLite format 3\x00" + b"\x00" * 80)


# ---------------------------------------------------------------------------
# AC2 — integrity_check / startup detection
# ---------------------------------------------------------------------------

class TestIntegrityCheck:
    def test_valid_db_returns_ok(self, tmp_path):
        """check_db_integrity returns 'ok' for a healthy DB."""
        db_path = tmp_path / "good.db"
        _make_valid_db(db_path)
        result = db.check_db_integrity(db_path)
        assert result == "ok"

    def test_corrupt_db_returns_error(self, tmp_path):
        """check_db_integrity returns a non-'ok' string for a corrupt DB."""
        db_path = tmp_path / "bad.db"
        _make_corrupt_db(db_path)
        result = db.check_db_integrity(db_path)
        assert result != "ok"
        assert isinstance(result, str)
        assert len(result) > 0

    def test_nonexistent_db_returns_error(self, tmp_path):
        """check_db_integrity returns an error string for a missing file."""
        result = db.check_db_integrity(tmp_path / "missing.db")
        assert result != "ok"

    def test_init_db_raises_clearly_on_corrupt_db(self, tmp_path, monkeypatch):
        """init_db raises RuntimeError with 'corrupt' in the message when DB is corrupt."""
        db_path = tmp_path / "corrupt.db"
        _make_corrupt_db(db_path)
        monkeypatch.setattr(db, "DB_PATH", db_path)
        with pytest.raises(RuntimeError, match="corrupt"):
            db.init_db()

    def test_init_db_surfaces_restore_path_when_backup_exists(self, tmp_path, monkeypatch):
        """init_db error message includes the backup dir path when a local backup exists."""
        db_path = tmp_path / "corrupt.db"
        _make_corrupt_db(db_path)
        monkeypatch.setattr(db, "DB_PATH", db_path)

        backup_dir = tmp_path / ".db-backups"
        backup_dir.mkdir()
        fake_backup = backup_dir / "commander.db.20260101_120000_000000.bak"
        _make_valid_db(fake_backup)
        monkeypatch.setattr(db, "_LOCAL_BACKUP_DIR", backup_dir)

        with pytest.raises(RuntimeError) as exc_info:
            db.init_db()
        msg = str(exc_info.value)
        assert str(backup_dir) in msg or "db-backups" in msg


# ---------------------------------------------------------------------------
# AC3 — local rolling backup
# ---------------------------------------------------------------------------

class TestLocalBackup:
    def test_backup_db_local_creates_file(self, tmp_path):
        """backup_db_local writes a timestamped .bak file to backup_dir."""
        db_path = tmp_path / "commander.db"
        _make_valid_db(db_path)
        backup_dir = tmp_path / "backups"

        result_path = backup.backup_db_local(db_path, backup_dir)

        assert result_path.exists(), "backup file was not created"
        assert result_path.suffix == ".bak"
        assert result_path.parent == backup_dir

    def test_backup_db_local_is_readable_sqlite(self, tmp_path):
        """The local backup is a valid SQLite DB (online-backup copy)."""
        db_path = tmp_path / "commander.db"
        _make_valid_db(db_path)
        backup_dir = tmp_path / "backups"

        result_path = backup.backup_db_local(db_path, backup_dir)

        conn = sqlite3.connect(str(result_path))
        try:
            rows = conn.execute("SELECT val FROM t").fetchall()
            assert rows == [("hello",)]
        finally:
            conn.close()

    def test_backup_db_local_prunes_old_copies(self, tmp_path):
        """After N+1 backups with n_keep=3, only the 3 newest are retained."""
        db_path = tmp_path / "commander.db"
        _make_valid_db(db_path)
        backup_dir = tmp_path / "backups"
        n_keep = 3

        for _ in range(n_keep + 2):
            backup.backup_db_local(db_path, backup_dir, n_keep=n_keep)
            time.sleep(0.01)

        remaining = list(backup_dir.glob("*.bak"))
        assert len(remaining) == n_keep, (
            f"expected {n_keep} backups, found {len(remaining)}: {remaining}"
        )

    def test_list_local_backups_returns_newest_first(self, tmp_path):
        """list_local_backups returns paths sorted newest-first."""
        db_path = tmp_path / "commander.db"
        _make_valid_db(db_path)
        backup_dir = tmp_path / "backups"

        for _ in range(3):
            backup.backup_db_local(db_path, backup_dir)
            time.sleep(0.01)

        listed = backup.list_local_backups(backup_dir)
        assert len(listed) == 3
        names = [p.name for p in listed]
        assert names == sorted(names, reverse=True), f"not sorted newest-first: {names}"

    def test_list_local_backups_empty_dir(self, tmp_path):
        """list_local_backups returns [] for a directory with no backups."""
        backup_dir = tmp_path / "empty-backups"
        backup_dir.mkdir()
        assert backup.list_local_backups(backup_dir) == []

    def test_list_local_backups_nonexistent_dir(self, tmp_path):
        """list_local_backups returns [] if the backup dir doesn't exist yet."""
        assert backup.list_local_backups(tmp_path / "no-such-dir") == []

    def test_local_backup_scheduler_starts(self, monkeypatch):
        """start_local_backup_scheduler registers a timer without blocking."""
        monkeypatch.setattr(backup, "_local_scheduler_started", False)
        monkeypatch.setattr(backup, "_local_scheduler_timer", None)

        backup.start_local_backup_scheduler()

        assert backup._local_scheduler_started is True


# ---------------------------------------------------------------------------
# AC4 — WAL mode + connection management
# ---------------------------------------------------------------------------

class TestWalHardening:
    def test_wal_mode_is_active(self, tmp_path, monkeypatch):
        """get_conn sets journal_mode=WAL; PRAGMA journal_mode returns 'wal'."""
        db_path = tmp_path / "wal_test.db"
        monkeypatch.setattr(db, "DB_PATH", db_path)
        with db.get_conn() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"

    def test_get_conn_closes_connection_on_exit(self, tmp_path, monkeypatch):
        """get_conn closes the connection when the context manager exits (AC4)."""
        db_path = tmp_path / "close_test.db"
        monkeypatch.setattr(db, "DB_PATH", db_path)

        conn_ref = None
        with db.get_conn() as conn:
            conn_ref = conn
            conn.execute("CREATE TABLE IF NOT EXISTS t (x INTEGER)")
            conn.commit()

        # A closed sqlite3.Connection raises ProgrammingError on any operation.
        with pytest.raises(Exception):
            conn_ref.execute("SELECT 1")

    def test_wal_checkpoint_runs_without_error(self, tmp_path, monkeypatch):
        """run_wal_checkpoint succeeds on a valid DB and returns a 3-tuple."""
        db_path = tmp_path / "ckpt_test.db"
        monkeypatch.setattr(db, "DB_PATH", db_path)
        with db.get_conn() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS _test (x INTEGER)")
            conn.commit()

        result = db.run_wal_checkpoint(db_path)
        assert isinstance(result, tuple)
        assert len(result) == 3
