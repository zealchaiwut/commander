"""Behavioral tests for issue #2037 — post-mortem: DB corruption undetected for ~3 hours.

Post-mortem investigation findings (AC1–AC4)
--------------------------------------------

AC1 — Corruption mechanism
    The corrupt DB file (apps/dashboard/commander.db.corrupt-20260731) lives on the
    UAT machine and is not present in this worktree, so a live PRAGMA integrity_check
    analysis is not reproducible here.  From the issue evidence:
    - The file's mtime shows the last successful write at ~10:03.
    - sqlite3 .recover produced 231k lines but recovered fewer rows than the 00:33 backup,
      indicating page-level loss in the 10:03–13:07 window.
    - WAL + busy_timeout were active; the corruption bypassed them.  Most likely cause:
      OS-level I/O error (disk, machine sleep, or power) rather than a SQLite locking bug.

AC2 — Concurrent writers that bypass get_conn()
    Identified by grepping `sqlite3.connect` across the codebase:
    - apps/dashboard/backup.py  — read-only (integrity_check + online-backup API); not a write risk.
    - apps/dashboard/calibration_cache_service.py — SELECT only; not a write risk.
    - services/sprint_manager/settings_repo.py:76 — writes to commander.db via DB_PATH env var
      using `sqlite3.connect(path, timeout=5)` WITHOUT PRAGMA busy_timeout=5000.
      WAL is persistent once set by get_conn(), so journal mode is not a risk here, but
      the missing busy_timeout means a sprint_manager subprocess write could fail immediately
      on a short lock burst instead of retrying for 5 s.  Not a corruption mechanism, but
      a write-failure risk worth noting.
    - scripts/backfill_*.py — operator-run one-shot tools; not concurrent writers.

AC3 — Non-SQLite causes
    The following should be verified on the UAT machine before the next incident:
    a. Disk full: `df -h $(dirname $DB_PATH)` — was there available space at 10:03?
    b. Disk I/O errors: `dmesg | grep -i 'error|disk|io'` for the 10:03 window.
    c. Machine sleep mid-write: `pmset -g log | grep -i sleep` for the same window.
    d. Multiple dashboard instances on the same file: `lsof $DB_PATH` while running.
    Conclusion: cause not fully determined from available evidence.

AC4 — Guard justified by findings
    Adding a periodic PRAGMA quick_check (AC5) is the guard that is always justified
    regardless of root cause.  It ensures corruption is detected within one check interval
    (~30 min) rather than only on the next startup.  Fixing settings_repo.py to add
    PRAGMA busy_timeout is a follow-up (low urgency: write-failure, not corruption).

Acceptance criteria covered by this test file
---------------------------------------------
  AC5  check_db_quick() detects corruption for the periodic integrity loop.
  AC5  alert_if_corrupt() emits a CRITICAL log when quick_check returns non-'ok'.
  AC5  alert_if_corrupt() does not emit CRITICAL for a healthy DB.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))
sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest-2037.db")

import db  # noqa: E402 — apps/dashboard/db.py


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_valid_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
    conn.execute("INSERT INTO t (val) VALUES ('hello')")
    conn.commit()
    conn.close()


def _make_corrupt_db(path: Path) -> None:
    """Write a file that looks like a DB header but has invalid content."""
    path.write_bytes(b"SQLite format 3\x00" + b"\x00" * 80)


# ── AC5 — check_db_quick ──────────────────────────────────────────────────────

class TestCheckDbQuick:
    def test_valid_db_returns_ok(self, tmp_path):
        """check_db_quick returns 'ok' for a healthy DB."""
        db_path = tmp_path / "good.db"
        _make_valid_db(db_path)
        assert db.check_db_quick(db_path) == "ok"

    def test_corrupt_db_returns_error(self, tmp_path):
        """check_db_quick returns a non-'ok' string for a corrupt DB."""
        db_path = tmp_path / "bad.db"
        _make_corrupt_db(db_path)
        result = db.check_db_quick(db_path)
        assert result != "ok"
        assert isinstance(result, str)
        assert len(result) > 0

    def test_missing_file_returns_error(self, tmp_path):
        """check_db_quick returns an error string for a non-existent file."""
        result = db.check_db_quick(tmp_path / "missing.db")
        assert result != "ok"
        assert "not found" in result.lower() or "error" in result.lower()

    def test_empty_file_returns_error(self, tmp_path):
        """check_db_quick returns an error string for a 0-byte file."""
        p = tmp_path / "empty.db"
        p.write_bytes(b"")
        result = db.check_db_quick(p)
        assert result != "ok"
        assert "empty" in result.lower() or "error" in result.lower()


# ── AC5 — alert_if_corrupt (check-and-log helper used by the periodic loop) ──

class TestAlertIfCorrupt:
    def test_corrupt_db_logs_critical(self, tmp_path, caplog):
        """alert_if_corrupt emits a CRITICAL log when the DB is corrupt."""
        db_path = tmp_path / "bad.db"
        _make_corrupt_db(db_path)

        with caplog.at_level(logging.CRITICAL, logger="db"):
            status = db.alert_if_corrupt(db_path)

        assert status != "ok", "Expected non-ok status for corrupt DB"
        critical_msgs = [r.message for r in caplog.records if r.levelno >= logging.CRITICAL]
        assert critical_msgs, "Expected at least one CRITICAL log entry"
        assert any(
            "CORRUPTION" in m or "corrupt" in m.lower()
            for m in critical_msgs
        ), f"CRITICAL log does not mention corruption: {critical_msgs}"

    def test_healthy_db_no_critical_log(self, tmp_path, caplog):
        """alert_if_corrupt does not emit CRITICAL log when the DB is healthy."""
        db_path = tmp_path / "good.db"
        _make_valid_db(db_path)

        with caplog.at_level(logging.CRITICAL, logger="db"):
            status = db.alert_if_corrupt(db_path)

        assert status == "ok"
        critical_msgs = [r.message for r in caplog.records if r.levelno >= logging.CRITICAL]
        assert not critical_msgs, f"Unexpected CRITICAL log on healthy DB: {critical_msgs}"

    def test_returns_ok_for_healthy_db(self, tmp_path):
        """alert_if_corrupt returns 'ok' for a healthy DB."""
        db_path = tmp_path / "good.db"
        _make_valid_db(db_path)
        assert db.alert_if_corrupt(db_path) == "ok"

    def test_returns_error_for_corrupt_db(self, tmp_path):
        """alert_if_corrupt returns a non-'ok' string for a corrupt DB."""
        db_path = tmp_path / "bad.db"
        _make_corrupt_db(db_path)
        result = db.alert_if_corrupt(db_path)
        assert result != "ok"

    def test_defaults_to_db_path(self, tmp_path, monkeypatch):
        """alert_if_corrupt without explicit path uses db.DB_PATH."""
        db_path = tmp_path / "good.db"
        _make_valid_db(db_path)
        monkeypatch.setattr(db, "DB_PATH", db_path)
        assert db.alert_if_corrupt() == "ok"
