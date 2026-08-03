"""Tests for issue #2012: runtime disk I/O error triggers integrity_check.

Acceptance criteria:
  AC1  When sqlite3.OperationalError("disk I/O error") is caught in the
       mirror-sync loop, check_db_integrity(DB_PATH) is called.
  AC2  The event is logged at CRITICAL level with the restore hint so the
       operator knows to stop-and-restore rather than let the server 500-loop.
  AC3  The loop aborts (raises RuntimeError) instead of continuing to retry,
       preventing an infinite 500-loop on a corrupt database.
  AC4  Non-disk-IO OperationalErrors (e.g. "no such table") still fall through
       to the existing warning-and-continue path — only disk I/O aborts.
  AC5  db.handle_runtime_disk_io_error is a no-op for any exception that is
       not a sqlite3.OperationalError containing "disk I/O error".
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")

import db  # noqa: E402
import github_events_sync  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_valid_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
    conn.execute("INSERT INTO t (val) VALUES ('hello')")
    conn.commit()
    conn.close()


_MS_STUB = types.SimpleNamespace(sync_milestones_mirror=lambda *a, **kw: None)


# ── AC5 / AC1: db.handle_runtime_disk_io_error unit tests ────────────────────

class TestHandleRuntimeDiskIoError:
    """Unit tests for db.handle_runtime_disk_io_error."""

    def test_no_op_for_plain_value_error(self, tmp_path, monkeypatch):
        """Non-sqlite exceptions are silently ignored (no-op, no raise)."""
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
        db.handle_runtime_disk_io_error(ValueError("something went wrong"))

    def test_no_op_for_other_operational_error(self, tmp_path, monkeypatch):
        """OperationalErrors that are NOT disk I/O are also ignored (AC4/AC5)."""
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
        db.handle_runtime_disk_io_error(
            sqlite3.OperationalError("no such table: foo")
        )

    def test_no_op_for_database_error_non_io(self, tmp_path, monkeypatch):
        """Generic DatabaseError without 'disk I/O error' is ignored."""
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
        db.handle_runtime_disk_io_error(sqlite3.DatabaseError("disk full"))

    def test_raises_for_disk_io_error(self, tmp_path, monkeypatch):
        """sqlite3.OperationalError('disk I/O error') → raises RuntimeError (AC3)."""
        db_path = tmp_path / "t.db"
        _make_valid_db(db_path)
        monkeypatch.setattr(db, "DB_PATH", db_path)
        with pytest.raises(RuntimeError, match="disk I/O error"):
            db.handle_runtime_disk_io_error(
                sqlite3.OperationalError("disk I/O error")
            )

    def test_calls_check_db_integrity_on_disk_io(self, tmp_path, monkeypatch):
        """check_db_integrity(DB_PATH) is called when disk I/O error is caught (AC1)."""
        db_path = tmp_path / "t.db"
        _make_valid_db(db_path)
        monkeypatch.setattr(db, "DB_PATH", db_path)
        called_with = []
        orig = db.check_db_integrity
        monkeypatch.setattr(
            db,
            "check_db_integrity",
            lambda p: (called_with.append(p), orig(p))[1],
        )
        with pytest.raises(RuntimeError):
            db.handle_runtime_disk_io_error(
                sqlite3.OperationalError("disk I/O error")
            )
        assert len(called_with) == 1, "check_db_integrity must be called exactly once"
        assert called_with[0] == db_path

    def test_logs_critical_on_disk_io(self, tmp_path, monkeypatch, caplog):
        """A CRITICAL log record is emitted so operators see the alert (AC2)."""
        db_path = tmp_path / "t.db"
        _make_valid_db(db_path)
        monkeypatch.setattr(db, "DB_PATH", db_path)
        with caplog.at_level(logging.CRITICAL, logger="db"):
            with pytest.raises(RuntimeError):
                db.handle_runtime_disk_io_error(
                    sqlite3.OperationalError("disk I/O error")
                )
        critical_records = [r for r in caplog.records if r.levelno >= logging.CRITICAL]
        assert critical_records, "Expected at least one CRITICAL log record"

    def test_error_message_includes_restore_hint_when_backup_exists(
        self, tmp_path, monkeypatch
    ):
        """RuntimeError message includes the restore hint when a valid backup exists (AC2)."""
        db_path = tmp_path / "t.db"
        _make_valid_db(db_path)
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        fake_backup = backup_dir / "commander.db.20260101_120000_000000.bak"
        _make_valid_db(fake_backup)
        monkeypatch.setattr(db, "DB_PATH", db_path)
        monkeypatch.setattr(db, "_LOCAL_BACKUP_DIR", backup_dir)
        with pytest.raises(RuntimeError) as exc_info:
            db.handle_runtime_disk_io_error(
                sqlite3.OperationalError("disk I/O error")
            )
        msg = str(exc_info.value)
        assert str(fake_backup) in msg, (
            f"Expected backup path {fake_backup} in error message:\n{msg}"
        )

    def test_case_insensitive_disk_io_detection(self, tmp_path, monkeypatch):
        """Detection is case-insensitive — 'Disk I/O Error' also triggers (AC1)."""
        db_path = tmp_path / "t.db"
        _make_valid_db(db_path)
        monkeypatch.setattr(db, "DB_PATH", db_path)
        with pytest.raises(RuntimeError):
            db.handle_runtime_disk_io_error(
                sqlite3.OperationalError("Disk I/O Error")
            )


# ── AC3 / AC4: run_issues_sync_loop behaviour ─────────────────────────────────

class TestSyncLoopDiskIoHandling:
    """Integration tests: mirror-sync loop aborts on disk I/O, continues otherwise."""

    def _no_sleep(self, monkeypatch) -> None:
        async def _instant_sleep(_: float) -> None:
            return None
        monkeypatch.setattr(github_events_sync.asyncio, "sleep", _instant_sleep)

    def test_disk_io_error_aborts_loop(self, tmp_path, monkeypatch):
        """Loop raises (aborts) when sync raises sqlite3.OperationalError("disk I/O error") (AC3)."""
        db_path = tmp_path / "t.db"
        _make_valid_db(db_path)
        monkeypatch.setattr(db, "DB_PATH", db_path)

        def _disk_io_sync(repo, db_module=None):
            raise sqlite3.OperationalError("disk I/O error")

        monkeypatch.setattr(github_events_sync, "sync_issues_mirror", _disk_io_sync)
        monkeypatch.setattr(
            github_events_sync, "reconcile_closed_issues", lambda *a, **kw: {}
        )
        self._no_sleep(monkeypatch)

        with patch.dict(sys.modules, {"github_milestones": _MS_STUB}):
            with pytest.raises((RuntimeError, sqlite3.OperationalError)):
                asyncio.run(
                    github_events_sync.run_issues_sync_loop(
                        ["o/r"], iterations=1
                    )
                )

    def test_disk_io_error_calls_integrity_check_in_loop(self, tmp_path, monkeypatch):
        """check_db_integrity is called when disk I/O error surfaces in the loop (AC1)."""
        db_path = tmp_path / "t.db"
        _make_valid_db(db_path)
        monkeypatch.setattr(db, "DB_PATH", db_path)

        def _disk_io_sync(repo, db_module=None):
            raise sqlite3.OperationalError("disk I/O error")

        integrity_calls: list = []
        orig = db.check_db_integrity
        monkeypatch.setattr(
            db,
            "check_db_integrity",
            lambda p: (integrity_calls.append(p), orig(p))[1],
        )
        monkeypatch.setattr(github_events_sync, "sync_issues_mirror", _disk_io_sync)
        monkeypatch.setattr(
            github_events_sync, "reconcile_closed_issues", lambda *a, **kw: {}
        )
        self._no_sleep(monkeypatch)

        with patch.dict(sys.modules, {"github_milestones": _MS_STUB}):
            with pytest.raises((RuntimeError, sqlite3.OperationalError)):
                asyncio.run(
                    github_events_sync.run_issues_sync_loop(
                        ["o/r"], iterations=1
                    )
                )
        assert integrity_calls, "check_db_integrity must be called at least once"

    def test_non_io_operational_error_continues(self, monkeypatch):
        """Non-disk-IO OperationalError logs a warning and the loop continues (AC4)."""
        calls = []

        def _bad_sync(repo, db_module=None):
            calls.append(repo)
            raise sqlite3.OperationalError("no such table: issues")

        monkeypatch.setattr(github_events_sync, "sync_issues_mirror", _bad_sync)
        monkeypatch.setattr(
            github_events_sync, "reconcile_closed_issues", lambda *a, **kw: {}
        )
        self._no_sleep(monkeypatch)

        with patch.dict(sys.modules, {"github_milestones": _MS_STUB}):
            asyncio.run(
                github_events_sync.run_issues_sync_loop(
                    ["o/r"], iterations=3
                )
            )
        assert calls == ["o/r", "o/r", "o/r"], (
            "Non-disk-IO error must NOT abort the loop; expected 3 iterations"
        )

    def test_disk_io_from_milestones_sync_aborts_loop(self, tmp_path, monkeypatch):
        """Disk I/O from milestones sync also aborts the loop (AC3)."""
        db_path = tmp_path / "t.db"
        _make_valid_db(db_path)
        monkeypatch.setattr(db, "DB_PATH", db_path)

        def _good_sync(repo, db_module=None):
            return {"status": 304, "synced": 0}

        def _disk_io_milestones(repo, db_module=None):
            raise sqlite3.OperationalError("disk I/O error")

        bad_ms_stub = types.SimpleNamespace(
            sync_milestones_mirror=_disk_io_milestones
        )
        monkeypatch.setattr(github_events_sync, "sync_issues_mirror", _good_sync)
        monkeypatch.setattr(
            github_events_sync, "reconcile_closed_issues", lambda *a, **kw: {}
        )
        self._no_sleep(monkeypatch)

        with patch.dict(sys.modules, {"github_milestones": bad_ms_stub}):
            with pytest.raises((RuntimeError, sqlite3.OperationalError)):
                asyncio.run(
                    github_events_sync.run_issues_sync_loop(
                        ["o/r"], iterations=1
                    )
                )
