"""Hourly rolling local backup for commander.db (issue #1901).

Writes a timestamped .bak copy using SQLite's online-backup API (safe under
concurrent writes), keeps the N newest, and starts a periodic timer at server
startup. Intentionally separate from the gist/repo authority-DB backup in
services/sprint_manager/backup.py.

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


def backup_db_local(
    db_path: Path,
    backup_dir: Path,
    n_keep: int = 5,
) -> Path:
    """Write a timestamped .bak copy of db_path to backup_dir.

    Uses SQLite's online-backup API so the copy is consistent even under
    concurrent writes. Prunes old backups so only the n_keep newest remain.
    Returns the path of the new backup file.
    """
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    dest = backup_dir / f"{db_path.name}.{ts}.bak"

    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(dest))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

    _prune_backups(backup_dir, n_keep)
    return dest


def _prune_backups(backup_dir: Path, n_keep: int) -> None:
    """Remove all but the n_keep newest .bak files in backup_dir."""
    baks = sorted(backup_dir.glob("*.bak"), key=lambda p: p.name, reverse=True)
    for old in baks[n_keep:]:
        try:
            old.unlink()
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
            except Exception:
                _logger.exception("local backup failed")
    _schedule_next()


def _schedule_next() -> None:
    global _local_scheduler_timer
    t = threading.Timer(_BACKUP_INTERVAL_SECS, _run_local_backup_in_thread)
    t.daemon = True
    t.start()
    _local_scheduler_timer = t


def start_local_backup_scheduler() -> None:
    """Register the hourly local backup timer. Call once at server startup. Idempotent."""
    global _local_scheduler_started
    if _local_scheduler_started:
        return
    _local_scheduler_started = True
    _schedule_next()
