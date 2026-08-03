"""Tests for issue #2013: run_wal_checkpoint() wired into the hourly backup tick.

Behavioral assertions:
  AC1  After a successful backup, the hourly tick calls run_wal_checkpoint()
       so WAL checkpointing is actively driven rather than relying on SQLite's
       auto-checkpoint alone.
  AC2  A checkpoint failure (any exception) is non-fatal — the backup tick
       completes without propagating the error.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest-2013.db")

import backup as backup_mod  # noqa: E402
import db as db_mod  # noqa: E402


@pytest.fixture(autouse=True)
def _no_scheduler_threads(monkeypatch):
    """Block _schedule_next() so no real threads fire during tests."""
    monkeypatch.setattr(backup_mod, "_local_scheduler_started", False)
    monkeypatch.setattr(backup_mod, "_local_scheduler_timer", None)
    monkeypatch.setattr(backup_mod, "_schedule_next", lambda: None)


def _make_wal_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS t (x INTEGER)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()


class TestWalCheckpointWiredIntoBackupTick:
    """AC1: run_wal_checkpoint() is called on each successful backup tick."""

    def test_backup_tick_calls_run_wal_checkpoint(self, tmp_path, monkeypatch):
        """Successful backup tick invokes run_wal_checkpoint() with the DB path."""
        db_path = tmp_path / "dashboard.db"
        _make_wal_db(db_path)
        monkeypatch.setenv("DB_PATH", str(db_path))

        checkpoint_calls: list = []

        def capture_checkpoint(path=None):
            checkpoint_calls.append(path)
            return (0, 0, 0)

        with patch.object(db_mod, "run_wal_checkpoint", side_effect=capture_checkpoint):
            backup_mod._run_local_backup_in_thread()

        assert checkpoint_calls, "run_wal_checkpoint() was not called after backup"
        assert checkpoint_calls[0] == db_path, (
            f"expected checkpoint on {db_path}, got {checkpoint_calls[0]}"
        )

    def test_backup_tick_does_not_checkpoint_when_backup_fails(
        self, tmp_path, monkeypatch
    ):
        """If the backup itself is refused (corrupt source), checkpoint is not called."""
        db_path = tmp_path / "corrupt.db"
        # Write a corrupt file so backup_db_local raises RuntimeError
        db_path.write_bytes(b"SQLite format 3\x00" + b"\x00" * 80)
        monkeypatch.setenv("DB_PATH", str(db_path))

        checkpoint_calls: list = []

        def capture_checkpoint(path=None):
            checkpoint_calls.append(path)
            return (0, 0, 0)

        with patch.object(db_mod, "run_wal_checkpoint", side_effect=capture_checkpoint):
            backup_mod._run_local_backup_in_thread()  # must not raise

        assert not checkpoint_calls, (
            "run_wal_checkpoint() should not be called when the backup was refused"
        )


class TestWalCheckpointFailureIsNonFatal:
    """AC2: A checkpoint exception does not crash the backup tick."""

    def test_checkpoint_exception_does_not_propagate(self, tmp_path, monkeypatch):
        """If run_wal_checkpoint() raises, _run_local_backup_in_thread() still completes."""
        db_path = tmp_path / "dashboard.db"
        _make_wal_db(db_path)
        monkeypatch.setenv("DB_PATH", str(db_path))

        def exploding_checkpoint(path=None):
            raise RuntimeError("simulated checkpoint I/O error")

        with patch.object(db_mod, "run_wal_checkpoint", side_effect=exploding_checkpoint):
            # Must complete without raising
            backup_mod._run_local_backup_in_thread()
