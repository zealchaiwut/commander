"""Hourly rolling local backup for commander.db (issue #1901).

Writes a timestamped .bak copy using SQLite's online-backup API (safe under
concurrent writes), keeps the N newest VERIFIED copies, and starts a periodic
timer at server startup. Intentionally separate from the gist/repo
authority-DB backup in services/sprint_manager/backup.py.

Hardening (issue #2036)
-----------------------
* Source DB is checked with PRAGMA integrity_check BEFORE writing.  A corrupt
  source is refused; the existing rotation is left intact.
* The backup copy is checked with PRAGMA integrity_check AFTER writing.  A
  copy that fails is deleted immediately — it is never rotated in.
* The result is also checked for 0 bytes and for a drastic size drop relative
  to the previous good backup (threshold: 10 % of previous size).
* _prune_backups counts only non-zero files toward n_keep, so an invalid
  legacy file never crowds out a good one.
* start_local_backup_scheduler() registers the backup dir with db.py so the
  startup fatal message names a VERIFIED backup, not merely the newest file.

Public API
----------
backup_db_local(db_path, backup_dir, n_keep=5)  -> Path
list_local_backups(backup_dir)                   -> list[Path]
start_local_backup_scheduler()                   -> None
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_logger = logging.getLogger("backup.local")

_local_scheduler_started: bool = False
_local_scheduler_timer: Optional[threading.Timer] = None
_BACKUP_INTERVAL_SECS: int = 3600  # 1 hour

# Minimum fraction of the previous good backup's size the new backup must be.
# A result smaller than this is considered "drastically shrunk" and is
# discarded.  0.10 = reject if new backup < 10 % of previous good backup size.
_MIN_SIZE_RATIO: float = 0.10


# ── Internal helpers ──────────────────────────────────────────────────────────

def _check_integrity(db_path: Path) -> str:
    """Run PRAGMA integrity_check on db_path.  Returns 'ok' or an error string.

    Duplicates db.check_db_integrity to avoid a circular import at module
    level (backup.py is loaded by startup.py which has already imported db).
    """
    if not db_path.exists():
        return f"error: file not found: {db_path}"
    if db_path.stat().st_size == 0:
        return "error: database file is empty"
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            return row[0] if row else "error: no result from integrity_check"
        except sqlite3.DatabaseError as exc:
            return f"error: {exc}"
        finally:
            conn.close()
    except Exception as exc:
        return f"error: could not open db: {exc}"


def _prev_valid_backup_size(backup_dir: Path) -> Optional[int]:
    """Return the byte size of the most recent non-zero .bak file, or None."""
    if not backup_dir.exists():
        return None
    baks = sorted(backup_dir.glob("*.bak"), key=lambda p: p.name, reverse=True)
    for bak in baks:
        try:
            sz = bak.stat().st_size
            if sz > 0:
                return sz
        except OSError:
            pass
    return None


# ── Public API ────────────────────────────────────────────────────────────────

def backup_db_local(
    db_path: Path,
    backup_dir: Path,
    n_keep: int = 5,
) -> Path:
    """Write a verified timestamped .bak copy of db_path to backup_dir.

    Hardening guards (issue #2036):
    1. Pre-write: source DB must pass PRAGMA integrity_check.
    2. Post-write: result must be non-zero bytes AND not drastically smaller
       than the previous good backup.
    3. Post-write: copy must pass PRAGMA integrity_check.
    4. Prune only after a verified backup is written so the rotation never
       loses a known-good backup in favour of an invalid one.

    Raises RuntimeError (with a CRITICAL log) if the backup cannot safely be
    written.  The existing rotation is left intact in every failure path.
    Returns the path of the new backup file on success.
    """
    # ── AC1a: source integrity pre-flight ────────────────────────────────────
    src_status = _check_integrity(db_path)
    if src_status != "ok":
        _logger.critical(
            "BACKUP SKIPPED — source DB failed PRAGMA integrity_check: %s "
            "— existing rotation is untouched",
            src_status,
        )
        raise RuntimeError(f"backup refused: source DB is corrupt ({src_status})")

    # ── Size reference for ratio guard ───────────────────────────────────────
    prev_size = _prev_valid_backup_size(backup_dir)

    # ── Write backup via SQLite online-backup API ─────────────────────────────
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    dest = backup_dir / f"{db_path.name}.{ts}.bak"

    src_conn: Optional[sqlite3.Connection] = None
    dst_conn: Optional[sqlite3.Connection] = None
    backup_ok = False
    try:
        src_conn = sqlite3.connect(str(db_path))
        dst_conn = sqlite3.connect(str(dest))
        src_conn.backup(dst_conn)
        backup_ok = True
    finally:
        if dst_conn is not None:
            dst_conn.close()
        if src_conn is not None:
            src_conn.close()
        # Clean up a partial/empty destination so rotation stays uncontaminated
        if not backup_ok and dest.exists():
            try:
                dest.unlink()
            except OSError:
                pass

    if not backup_ok:
        # Should be unreachable (exception already propagating), but guard it.
        raise RuntimeError("backup_db_local: src.backup() failed unexpectedly")

    # ── AC1b: zero-byte result guard ─────────────────────────────────────────
    dest_size = dest.stat().st_size
    if dest_size == 0:
        try:
            dest.unlink()
        except OSError:
            pass
        _logger.critical(
            "BACKUP DISCARDED — result was 0 bytes (source may be corrupt or "
            "unreadable under concurrent I/O) — existing rotation is untouched"
        )
        raise RuntimeError("backup refused: backup result was 0 bytes")

    # ── AC1c: drastic-shrink guard ───────────────────────────────────────────
    if prev_size is not None and _MIN_SIZE_RATIO > 0:
        min_expected = int(prev_size * _MIN_SIZE_RATIO)
        if dest_size < min_expected:
            try:
                dest.unlink()
            except OSError:
                pass
            _logger.critical(
                "BACKUP DISCARDED — result (%d B) is < %.0f %% of the previous "
                "good backup (%d B) — existing rotation is untouched",
                dest_size,
                _MIN_SIZE_RATIO * 100,
                prev_size,
            )
            raise RuntimeError(
                f"backup refused: result ({dest_size} B) is drastically smaller "
                f"than the previous good backup ({prev_size} B)"
            )

    # ── AC2: post-write verification ─────────────────────────────────────────
    copy_status = _check_integrity(dest)
    if copy_status != "ok":
        try:
            dest.unlink()
        except OSError:
            pass
        _logger.critical(
            "BACKUP DISCARDED — copy failed PRAGMA integrity_check: %s "
            "— existing rotation is untouched",
            copy_status,
        )
        raise RuntimeError(
            f"backup refused: backup copy failed integrity_check ({copy_status})"
        )

    # ── AC4: prune only after a verified backup is written ───────────────────
    _prune_backups(backup_dir, n_keep)
    _logger.info("local backup written and verified: %s (%d B)", dest, dest_size)
    return dest


def _prune_backups(backup_dir: Path, n_keep: int) -> None:
    """Keep the n_keep newest non-zero .bak files; delete others.

    Counts only non-zero-byte files toward n_keep (AC4: an invalid/empty
    legacy file is never allowed to crowd out a known-good backup).
    Zero-byte files are deleted regardless of position.
    """
    baks = sorted(backup_dir.glob("*.bak"), key=lambda p: p.name, reverse=True)
    valid_kept = 0
    for bak in baks:
        try:
            sz = bak.stat().st_size
        except OSError:
            continue
        if sz > 0 and valid_kept < n_keep:
            valid_kept += 1
            # keep this backup
        else:
            try:
                bak.unlink()
            except OSError:
                pass


def list_local_backups(backup_dir: Path) -> list[Path]:
    """Return .bak files in backup_dir sorted newest-first. Returns [] if dir missing."""
    if not backup_dir.exists():
        return []
    return sorted(backup_dir.glob("*.bak"), key=lambda p: p.name, reverse=True)


def _run_local_backup_in_thread() -> None:
    """Execute one backup cycle and reschedule for the next interval."""
    db_path_str = os.environ.get("DB_PATH", "").strip()
    if db_path_str:
        db_path = Path(db_path_str)
        if db_path.exists():
            backup_dir = db_path.parent / ".commander" / "db-backups"
            try:
                dest = backup_db_local(db_path, backup_dir)
                _logger.info("local backup written: %s", dest)
            except RuntimeError as exc:
                # backup_db_local raises RuntimeError for validated refusals
                # (corrupt source, 0-byte result, failed copy verify).  The
                # root cause is already logged at CRITICAL inside backup_db_local.
                _logger.warning(
                    "local backup refused — existing rotation intact: %s", exc
                )
            except Exception:
                _logger.exception("local backup failed unexpectedly")
    _schedule_next()


def _schedule_next() -> None:
    global _local_scheduler_timer
    t = threading.Timer(_BACKUP_INTERVAL_SECS, _run_local_backup_in_thread)
    t.daemon = True
    t.start()
    _local_scheduler_timer = t


def start_local_backup_scheduler() -> None:
    """Register the hourly local backup timer.  Call once at server startup.  Idempotent.

    Also registers the backup directory with db.py so that _startup_integrity_check
    can name a VERIFIED backup in its restore hint rather than merely the newest
    file by mtime (issue #2036).  Mirrors what the dead duplicate in
    services/sprint_manager/backup.py already does via db.set_local_backup_dir(),
    but for the LIVE scheduler path.
    """
    global _local_scheduler_started
    if _local_scheduler_started:
        return
    _local_scheduler_started = True

    # Wire the backup dir into db.py so the startup integrity error message
    # points at the correct directory (and _find_best_backup can scan it).
    db_path_str = os.environ.get("DB_PATH", "").strip()
    if db_path_str:
        _backup_dir = Path(db_path_str).parent / ".commander" / "db-backups"
        try:
            import db as _db_mod  # noqa: PLC0415 — lazy to avoid circular import at module load
            _db_mod.set_local_backup_dir(_backup_dir)
        except Exception:
            _logger.warning(
                "could not register backup dir with db.py; restore hint may "
                "fall back to hardcoded path",
                exc_info=True,
            )

    _schedule_next()
