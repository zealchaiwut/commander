"""Behavioral tests for issue #2036 — backup hardening.

Acceptance criteria covered:
  AC1  backup_db_local refuses to write when the source DB fails
       PRAGMA integrity_check, or when the result would be 0 bytes /
       drastically smaller than the previous good backup.
  AC2  A backup is verified after writing (PRAGMA integrity_check on the
       copy); a copy that fails is deleted and never rotated in.
  AC3  _startup_integrity_check names the most recent VERIFIED non-empty
       backup in its restore hint; if none exists, says so explicitly.
  AC4  _prune_backups never deletes the last known-good backup to make
       room for an invalid one.
  AC5  Behavioral sub-tests:
       (a) corrupt source → no new file created, rotation untouched, loud log
       (b) 0-byte / unverifiable backup never selected as restore target
       (c) with only invalid backups, fatal message says "no valid backup"

How these tests FAIL against pre-fix code
------------------------------------------
(a) test_corrupt_source_no_backup_written:
    Pre-fix: backup_db_local does NOT check source integrity.  It calls
    sqlite3.connect() + src.backup(dst); on a corrupt source this produces a
    0-byte file in backup_dir and returns it.  The test asserts no new file
    was created, so it FAILS (a 0-byte dest file is present).

(b) test_zero_byte_backup_not_selected_as_restore_target:
    Pre-fix: _startup_integrity_check picks sorted(baks, reverse=True)[0],
    which is the newest .bak by filename — the 0-byte one.  The restore hint
    contains "Restore: cp <zero-byte-file>", so the assertion that the
    0-byte file is NOT named FAILS.

(c) test_no_valid_backup_says_so_explicitly:
    Pre-fix: _startup_integrity_check has a non-empty baks list (the 0-byte
    file) and prints a cp command pointing at it.  The assertion that the
    message does NOT contain "Restore: cp" FAILS.

Git-isolation guarantee
-----------------------
Every test is guarded by the git_no_mutation autouse fixture (pattern copied
from test_2031__false_orphan_sweep.py).  Any code path that runs git commit
or git add causes the fixture to fail loudly.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

# Ensure dashboard modules are importable; apps/dashboard FIRST so that plain
# ``import backup`` and ``import db`` pick up the live copies, not the dead
# services/sprint_manager duplicate.
for _p in (str(DASHBOARD_DIR), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest-2036.db")

# Load the LIVE apps/dashboard/backup.py by file path to guarantee we are not
# accidentally importing services/sprint_manager/backup.py (which is also on
# sys.path after the insert above).
import importlib.util as _ilu

_bak_spec = _ilu.spec_from_file_location(
    "_dashboard_backup_2036",
    str(DASHBOARD_DIR / "backup.py"),
)
backup = _ilu.module_from_spec(_bak_spec)
_bak_spec.loader.exec_module(backup)  # type: ignore[union-attr]

import db  # noqa: E402 — apps/dashboard/db.py (first on sys.path)


# ── Git-isolation guard (copied from test_2031__false_orphan_sweep.py) ────────

def _git_head_sha() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=str(REPO_ROOT),
        text=True,
    ).strip()


@pytest.fixture(autouse=True)
def git_no_mutation():
    """Assert that no test in this module commits to the repository."""
    sha_before = _git_head_sha()
    yield
    sha_after = _git_head_sha()
    assert sha_before == sha_after, (
        f"Test mutated the git repository!\n"
        f"  HEAD before: {sha_before}\n"
        f"  HEAD after:  {sha_after}\n"
        "An unmocked code path ran 'git commit' or 'git add'."
    )


# ── Shared fixtures and helpers ───────────────────────────────────────────────

def _make_valid_db(path: Path) -> None:
    """Create a minimal valid SQLite DB at path."""
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
    conn.execute("INSERT INTO t (val) VALUES ('hello')")
    conn.commit()
    conn.close()


def _make_corrupt_db(path: Path) -> None:
    """Write a file that looks like a DB but has an invalid header."""
    # SQLite magic is correct so the file extension check passes; the rest of
    # the header is zeros → invalid page size → integrity_check returns error.
    path.write_bytes(b"SQLite format 3\x00" + b"\x00" * 80)


# ── AC1 / AC5(a) — corrupt source: no new file, rotation untouched ────────────

class TestCorruptSourceGuard:
    def test_corrupt_source_no_backup_written(self, tmp_path, caplog):
        """AC5(a): corrupt source → no new file in backup_dir, rotation untouched."""
        db_path = tmp_path / "corrupt.db"
        _make_corrupt_db(db_path)
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        # Plant a known-good backup already in the rotation
        existing = backup_dir / "commander.db.20260101_000000_000000.bak"
        _make_valid_db(existing)
        files_before = set(backup_dir.glob("*.bak"))

        with caplog.at_level(logging.CRITICAL, logger="backup.local"):
            with pytest.raises(RuntimeError, match="corrupt"):
                backup.backup_db_local(db_path, backup_dir)

        files_after = set(backup_dir.glob("*.bak"))
        # No new file must have been added
        assert files_after == files_before, (
            f"Expected rotation unchanged; found new files: {files_after - files_before}"
        )

    def test_corrupt_source_logs_critical(self, tmp_path, caplog):
        """AC1: corrupt source refusal is logged at CRITICAL level."""
        db_path = tmp_path / "corrupt.db"
        _make_corrupt_db(db_path)
        backup_dir = tmp_path / "backups"

        with caplog.at_level(logging.CRITICAL, logger="backup.local"):
            with pytest.raises(RuntimeError):
                backup.backup_db_local(db_path, backup_dir)

        critical_msgs = [
            r.message for r in caplog.records if r.levelno >= logging.CRITICAL
        ]
        assert critical_msgs, "Expected at least one CRITICAL log entry on corrupt source"
        assert any("BACKUP SKIPPED" in m or "corrupt" in m.lower() for m in critical_msgs)

    def test_corrupt_source_rotation_count_unchanged(self, tmp_path, caplog):
        """AC4: the n_keep count of valid backups is unchanged after a refused backup."""
        db_path = tmp_path / "corrupt.db"
        _make_corrupt_db(db_path)
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        # Put 3 valid backups in rotation
        for i in range(3):
            bak = backup_dir / f"commander.db.2026010{i+1}_000000_000000.bak"
            _make_valid_db(bak)

        n_before = len(list(backup_dir.glob("*.bak")))

        with caplog.at_level(logging.CRITICAL, logger="backup.local"):
            with pytest.raises(RuntimeError):
                backup.backup_db_local(db_path, backup_dir, n_keep=3)

        n_after = len(list(backup_dir.glob("*.bak")))
        assert n_after == n_before, (
            f"Rotation changed from {n_before} to {n_after} files on a refused backup"
        )


# ── AC1b — zero-byte result ────────────────────────────────────────────────────

class TestZeroByteResultGuard:
    def test_zero_byte_result_not_kept(self, tmp_path, monkeypatch, caplog):
        """AC1b: a 0-byte backup result is deleted, not rotated in.

        Simulated by replacing backup.py's sqlite3 module reference with a
        namespace whose connect() returns a proxy src connection whose backup()
        is a no-op — so the dest file is created (by the real connect for dst)
        but no pages are written, leaving it empty.
        """
        import types

        db_path = tmp_path / "good.db"
        _make_valid_db(db_path)
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        real_connect = sqlite3.connect
        call_count = [0]

        class _SrcProxy:
            """Source connection whose backup() is a no-op."""
            def backup(self, target): pass
            def close(self): pass

        def _mock_connect(path, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call = source connection → noop backup
                return _SrcProxy()
            # Second call = dest connection → creates the file (stays 0 bytes)
            return real_connect(path, **kwargs)

        # Replace backup.py's sqlite3 module reference (not the global module)
        mock_sqlite3 = types.SimpleNamespace(
            connect=_mock_connect,
            DatabaseError=sqlite3.DatabaseError,
        )
        monkeypatch.setattr(backup, "sqlite3", mock_sqlite3)
        monkeypatch.setattr(backup, "_check_integrity", lambda p: "ok")

        with caplog.at_level(logging.CRITICAL, logger="backup.local"):
            with pytest.raises(RuntimeError, match="0 bytes"):
                backup.backup_db_local(db_path, backup_dir)

        # No .bak files should remain
        remaining = list(backup_dir.glob("*.bak")) if backup_dir.exists() else []
        assert remaining == [], f"Expected no backup files; found: {remaining}"

    def test_zero_byte_backup_not_selected_as_restore_target(self, tmp_path, monkeypatch):
        """AC5(b): a 0-byte backup is never named in the restore hint."""
        # Corrupt main DB
        db_path = tmp_path / "corrupt.db"
        _make_corrupt_db(db_path)

        # Backup dir: one GOOD old backup, then a newer 0-byte "backup"
        backup_dir = tmp_path / ".commander" / "db-backups"
        backup_dir.mkdir(parents=True)

        good_bak = backup_dir / "commander.db.20260101_000000_000000.bak"
        _make_valid_db(good_bak)

        zero_bak = backup_dir / "commander.db.20260201_000000_000000.bak"
        zero_bak.write_bytes(b"")  # 0 bytes, newer timestamp in filename

        monkeypatch.setattr(db, "DB_PATH", db_path)
        monkeypatch.setattr(db, "_LOCAL_BACKUP_DIR", backup_dir)

        with pytest.raises(RuntimeError) as exc_info:
            db._startup_integrity_check()

        msg = str(exc_info.value)
        # The 0-byte file must NOT be in the restore hint
        assert str(zero_bak) not in msg, (
            f"0-byte backup {zero_bak.name} was named in the restore hint: {msg}"
        )
        # The good backup SHOULD be named
        assert str(good_bak) in msg or good_bak.name in msg, (
            f"Expected good backup {good_bak.name} in restore hint but got: {msg}"
        )

    def test_restore_hint_names_verified_backup(self, tmp_path, monkeypatch):
        """AC3: restore hint names the most recent verified backup, not merely newest."""
        db_path = tmp_path / "corrupt.db"
        _make_corrupt_db(db_path)

        backup_dir = tmp_path / ".commander" / "db-backups"
        backup_dir.mkdir(parents=True)

        # Newest by name: 0-byte (invalid)
        bad_bak = backup_dir / "commander.db.20260301_000000_000000.bak"
        bad_bak.write_bytes(b"")

        # Older by name: valid
        good_bak = backup_dir / "commander.db.20260201_000000_000000.bak"
        _make_valid_db(good_bak)

        monkeypatch.setattr(db, "DB_PATH", db_path)
        monkeypatch.setattr(db, "_LOCAL_BACKUP_DIR", backup_dir)

        with pytest.raises(RuntimeError) as exc_info:
            db._startup_integrity_check()

        msg = str(exc_info.value)
        assert str(bad_bak) not in msg, (
            "Restore hint must not reference the newer 0-byte backup"
        )
        assert str(good_bak) in msg or good_bak.name in msg, (
            "Restore hint must reference the older verified backup"
        )


# ── AC3 / AC5(c) — no valid backup: says so explicitly ───────────────────────

class TestNoValidBackupMessage:
    def test_no_valid_backup_says_so_explicitly(self, tmp_path, monkeypatch):
        """AC5(c): with only invalid backups, fatal message says no valid backup."""
        db_path = tmp_path / "corrupt.db"
        _make_corrupt_db(db_path)

        backup_dir = tmp_path / ".commander" / "db-backups"
        backup_dir.mkdir(parents=True)

        # Only 0-byte backups in rotation
        for ts in ["20260201_000000_000000", "20260202_000000_000000"]:
            (backup_dir / f"commander.db.{ts}.bak").write_bytes(b"")

        monkeypatch.setattr(db, "DB_PATH", db_path)
        monkeypatch.setattr(db, "_LOCAL_BACKUP_DIR", backup_dir)

        with pytest.raises(RuntimeError) as exc_info:
            db._startup_integrity_check()

        msg = str(exc_info.value)
        # Must NOT contain a cp command (that would be a lie)
        assert "Restore: cp" not in msg, (
            f"Expected no 'Restore: cp' command when all backups are invalid; got: {msg}"
        )
        # Must say something about no valid backup
        assert any(
            phrase in msg
            for phrase in [
                "No verified",
                "no valid",
                "do NOT restore",
                "failed integrity",
            ]
        ), f"Expected explicit 'no valid backup' message; got: {msg}"

    def test_empty_backup_dir_says_so(self, tmp_path, monkeypatch):
        """AC3: empty backup dir is reported, not a false restore hint."""
        db_path = tmp_path / "corrupt.db"
        _make_corrupt_db(db_path)

        backup_dir = tmp_path / ".commander" / "db-backups"
        backup_dir.mkdir(parents=True)
        # Leave backup_dir empty

        monkeypatch.setattr(db, "DB_PATH", db_path)
        monkeypatch.setattr(db, "_LOCAL_BACKUP_DIR", backup_dir)

        with pytest.raises(RuntimeError) as exc_info:
            db._startup_integrity_check()

        msg = str(exc_info.value)
        assert "Restore: cp" not in msg, (
            "Must not print a cp command when backup dir is empty"
        )
        assert "empty" in msg.lower() or "no backup" in msg.lower() or "db-backups" in msg


# ── AC2 — post-write verification ────────────────────────────────────────────

class TestPostWriteVerification:
    def test_invalid_copy_is_discarded(self, tmp_path, monkeypatch, caplog):
        """AC2: backup copy that fails integrity_check is deleted, not rotated in."""
        db_path = tmp_path / "good.db"
        _make_valid_db(db_path)
        backup_dir = tmp_path / "backups"

        # Source passes integrity_check; copy "fails" it.
        call_count = [0]

        def _fake_check(path: Path) -> str:
            call_count[0] += 1
            if str(path) == str(db_path):
                return "ok"   # source passes
            return "error: simulated copy corruption"  # copy fails

        monkeypatch.setattr(backup, "_check_integrity", _fake_check)

        with caplog.at_level(logging.CRITICAL, logger="backup.local"):
            with pytest.raises(RuntimeError, match="integrity_check"):
                backup.backup_db_local(db_path, backup_dir)

        # No .bak files should remain
        remaining = list(backup_dir.glob("*.bak")) if backup_dir.exists() else []
        assert remaining == [], f"Invalid copy was not cleaned up: {remaining}"

    def test_valid_backup_passes_through(self, tmp_path):
        """AC2 positive case: a healthy DB produces a verified backup file."""
        db_path = tmp_path / "good.db"
        _make_valid_db(db_path)
        backup_dir = tmp_path / "backups"

        result = backup.backup_db_local(db_path, backup_dir)

        assert result.exists()
        assert result.stat().st_size > 0
        assert backup._check_integrity(result) == "ok"


# ── AC4 — pruning preserves valid backups ─────────────────────────────────────

class TestPruningPreservesValid:
    def test_prune_keeps_n_valid_not_n_total(self, tmp_path):
        """AC4: n_keep counts only non-zero files; valid backups are not crowded out."""
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        # Plant 2 valid and 3 zero-byte "legacy" backups
        for i in range(2):
            bak = backup_dir / f"commander.db.20260101_00000{i}_000000.bak"
            _make_valid_db(bak)
        for i in range(3):
            bak = backup_dir / f"commander.db.20260102_00000{i}_000000.bak"
            bak.write_bytes(b"")

        # Prune with n_keep=2 — should keep 2 valid, delete invalid ones
        backup._prune_backups(backup_dir, n_keep=2)

        remaining = list(backup_dir.glob("*.bak"))
        zero_remaining = [b for b in remaining if b.stat().st_size == 0]
        nonzero_remaining = [b for b in remaining if b.stat().st_size > 0]

        assert zero_remaining == [], f"Zero-byte backups were not pruned: {zero_remaining}"
        assert len(nonzero_remaining) == 2, (
            f"Expected 2 valid backups to be kept; found {len(nonzero_remaining)}"
        )

    def test_prune_does_not_delete_last_valid_backup(self, tmp_path):
        """AC4: the single last valid backup is not deleted regardless of n_keep."""
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        # 1 valid backup
        valid = backup_dir / "commander.db.20260101_000000_000000.bak"
        _make_valid_db(valid)

        backup._prune_backups(backup_dir, n_keep=5)

        assert valid.exists(), "Last valid backup was incorrectly deleted"


# ── AC1c — drastic shrink guard ──────────────────────────────────────────────

class TestDrasticShrinkGuard:
    def test_drastically_smaller_result_is_rejected(self, tmp_path, monkeypatch, caplog):
        """AC1c: result < MIN_SIZE_RATIO of previous good backup is discarded."""
        db_path = tmp_path / "good.db"
        _make_valid_db(db_path)
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        # Plant a "previous good backup" that is artificially large
        prev = backup_dir / "commander.db.20260101_000000_000000.bak"
        # Write 100 KB of valid SQLite content so prev_size is large
        conn = sqlite3.connect(str(prev))
        conn.execute("CREATE TABLE big (data BLOB)")
        conn.execute("INSERT INTO big VALUES (?)", (b"x" * 102400,))
        conn.commit()
        conn.close()
        prev_size = prev.stat().st_size

        # The source DB is tiny (< 10 % of prev_size)
        # Don't monkeypatch integrity — source must be genuinely valid
        assert backup._check_integrity(db_path) == "ok"
        assert db_path.stat().st_size < prev_size * backup._MIN_SIZE_RATIO

        with caplog.at_level(logging.CRITICAL, logger="backup.local"):
            with pytest.raises(RuntimeError, match="drastically smaller"):
                backup.backup_db_local(db_path, backup_dir)

        # Destination file must not linger
        new_baks = [b for b in backup_dir.glob("*.bak") if b != prev]
        assert new_baks == [], f"Discarded backup was not cleaned up: {new_baks}"


# ── _find_best_backup unit tests ─────────────────────────────────────────────

class TestFindBestBackup:
    def test_returns_newest_valid(self, tmp_path):
        """_find_best_backup returns the newest verified non-empty backup."""
        bdir = tmp_path / "baks"
        bdir.mkdir()

        old = bdir / "commander.db.20260101_000000_000000.bak"
        _make_valid_db(old)
        newer = bdir / "commander.db.20260201_000000_000000.bak"
        _make_valid_db(newer)

        assert db._find_best_backup(bdir) == newer

    def test_skips_zero_byte(self, tmp_path):
        """_find_best_backup skips 0-byte files even if they are newest."""
        bdir = tmp_path / "baks"
        bdir.mkdir()

        good = bdir / "commander.db.20260101_000000_000000.bak"
        _make_valid_db(good)
        zero = bdir / "commander.db.20260201_000000_000000.bak"
        zero.write_bytes(b"")  # newer but empty

        assert db._find_best_backup(bdir) == good

    def test_returns_none_when_all_invalid(self, tmp_path):
        """_find_best_backup returns None when all backups are 0-byte or corrupt."""
        bdir = tmp_path / "baks"
        bdir.mkdir()

        for ts in ["20260101_000000_000000", "20260201_000000_000000"]:
            (bdir / f"commander.db.{ts}.bak").write_bytes(b"")

        assert db._find_best_backup(bdir) is None

    def test_returns_none_for_empty_dir(self, tmp_path):
        """_find_best_backup returns None for a dir with no .bak files."""
        bdir = tmp_path / "baks"
        bdir.mkdir()
        assert db._find_best_backup(bdir) is None
