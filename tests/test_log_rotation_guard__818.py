"""Tests for issue #818: Guard .commander/logs rotation against multi-process rename race.

AC1: _CommanderFileHandler.emit acquires an fcntl advisory lock covering both _rotate_if_needed and the file write.
AC2: When two processes concurrently write across rotation boundary, no log lines are silently dropped.
AC3: No process holds the log file lock during unrelated I/O — lock released immediately after write.
AC4: If fcntl unavailable (non-POSIX), handler falls back gracefully without raising exception.
AC5: Existing log rotation behavior (daily file, size-based rollover, naming, pruning) preserved.
AC6: Unit tests cover concurrent-write scenario (two threads simulating processes) across rotation boundary.
"""
import datetime
import importlib
import logging
import os
import sys
import threading
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def logging_mod():
    import services.logging as mod
    return importlib.reload(mod)


def _make_handler(logging_mod, tmp_path, monkeypatch, max_bytes: int = 200):
    """Return a _CommanderFileHandler wired to tmp_path."""
    monkeypatch.setattr(logging_mod, "_resolve_log_dir", lambda: tmp_path)
    monkeypatch.setenv("COMMANDER_LOG_MAX_BYTES", str(max_bytes))
    monkeypatch.setenv("COMMANDER_LOG_BACKUP_COUNT", "5")
    handler = logging_mod._CommanderFileHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    return handler


def _log_record(msg: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="test", level=logging.INFO,
        pathname="", lineno=0, msg=msg,
        args=(), exc_info=None,
    )


def _collect_lines(tmp_path: Path, today: str) -> list:
    """Collect all log lines from active + rotated daily log files."""
    lines = []
    for p in sorted(tmp_path.iterdir()):
        if f"commander-{today}" in p.name:
            lines.extend(p.read_text(encoding="utf-8").splitlines())
    return lines


# --- AC1: fcntl advisory lock acquired and covers rotate+append ---

def test_818__ac1_lock_acquired_around_rotate_and_append(logging_mod, tmp_path, monkeypatch):
    """AC1: Lock is acquired before rotate and held through write completion.

    Verified by observing that concurrent emitters do not interfere:
    each hold the lock exclusively during rotate+write sequence.
    """
    if not logging_mod._HAS_FCNTL:
        pytest.skip("fcntl not available on this platform")

    handler = _make_handler(logging_mod, tmp_path, monkeypatch, max_bytes=200)
    today = datetime.date.today().isoformat()
    log_path = tmp_path / f"commander-{today}.log"
    log_path.write_bytes(b"x" * 195)

    barrier = threading.Barrier(2)

    def emit_one(msg):
        barrier.wait()
        handler.emit(_log_record(msg))

    threads = [threading.Thread(target=emit_one, args=(f"msg-{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # If lock is not held during rotate, one process would see the renamed file
    # and fail to append. Both messages should be present.
    lines = _collect_lines(tmp_path, today)
    assert any("msg-0" in l for l in lines), "msg-0 missing — lock may not cover rotate"
    assert any("msg-1" in l for l in lines), "msg-1 missing — lock may not cover rotate"


# --- AC2: No lines dropped during concurrent rotate+append ---

def test_818__ac2_no_data_loss_concurrent_writes(logging_mod, tmp_path, monkeypatch):
    """AC2: When two processes write concurrently across rotation boundary, no lines are dropped."""
    handler = _make_handler(logging_mod, tmp_path, monkeypatch, max_bytes=200)
    today = datetime.date.today().isoformat()
    log_path = tmp_path / f"commander-{today}.log"
    log_path.write_bytes(b"x" * 195)

    barrier = threading.Barrier(2)
    errors = []

    def emit_one(msg):
        try:
            barrier.wait()
            handler.emit(_log_record(msg))
        except Exception as exc:
            errors.append(exc)

    t1 = threading.Thread(target=emit_one, args=("line-thread-1",))
    t2 = threading.Thread(target=emit_one, args=("line-thread-2",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert not errors, f"Exceptions during concurrent emit: {errors}"

    lines = _collect_lines(tmp_path, today)
    assert any("line-thread-1" in l for l in lines), "Thread 1 line was dropped"
    assert any("line-thread-2" in l for l in lines), "Thread 2 line was dropped"


# --- AC3: Lock released immediately after write (not held during unrelated I/O) ---

def test_818__ac3_lock_released_after_write(logging_mod, tmp_path, monkeypatch):
    """AC3: After one emit completes, a second emit acquires the lock promptly.

    If the lock were held indefinitely, the second emit would block.
    """
    handler = _make_handler(logging_mod, tmp_path, monkeypatch)

    handler.emit(_log_record("first line"))
    handler.emit(_log_record("second line"))

    today = datetime.date.today().isoformat()
    lines = _collect_lines(tmp_path, today)
    assert any("first line" in l for l in lines), "first line missing"
    assert any("second line" in l for l in lines), "second line missing"


# --- AC4: Graceful fallback when fcntl unavailable ---

def test_818__ac4_graceful_fallback_no_fcntl(logging_mod, tmp_path, monkeypatch):
    """AC4: When fcntl unavailable, emit succeeds without raising exception."""
    monkeypatch.setattr(logging_mod, "_HAS_FCNTL", False)
    handler = _make_handler(logging_mod, tmp_path, monkeypatch)

    # Must not raise
    handler.emit(_log_record("fallback line"))

    today = datetime.date.today().isoformat()
    lines = _collect_lines(tmp_path, today)
    assert any("fallback line" in l for l in lines), "fallback line missing"


# --- AC5: Existing rotation behavior preserved ---

def test_818__ac5_rotation_naming_preserved(logging_mod, tmp_path, monkeypatch):
    """AC5a: Daily file rotation naming (.1 suffix) is preserved."""
    handler = _make_handler(logging_mod, tmp_path, monkeypatch, max_bytes=100)

    today = datetime.date.today().isoformat()
    log_path = tmp_path / f"commander-{today}.log"
    log_path.write_bytes(b"x" * 90)

    handler.emit(_log_record("post-rotation line"))

    assert (tmp_path / f"commander-{today}.log.1").exists(), ".1 backup not created"
    active = tmp_path / f"commander-{today}.log"
    assert active.exists(), "active log file missing after rotation"
    assert "post-rotation line" in active.read_text(encoding="utf-8")


def test_818__ac5_backup_count_pruning_preserved(logging_mod, tmp_path, monkeypatch):
    """AC5b: Backup pruning beyond backup_count limit is preserved."""
    monkeypatch.setattr(logging_mod, "_resolve_log_dir", lambda: tmp_path)
    monkeypatch.setenv("COMMANDER_LOG_MAX_BYTES", "100")
    monkeypatch.setenv("COMMANDER_LOG_BACKUP_COUNT", "2")

    today = datetime.date.today().isoformat()
    log_path = tmp_path / f"commander-{today}.log"
    log_path.write_bytes(b"x" * 90)
    for i in range(1, 3):
        (tmp_path / f"commander-{today}.log.{i}").write_bytes(b"old")

    logging_mod._rotate_if_needed(log_path, incoming_len=50, max_bytes=100, backup_count=2)

    assert not (tmp_path / f"commander-{today}.log.3").exists(), ".3 should be pruned"
    assert (tmp_path / f"commander-{today}.log.1").exists()
    assert (tmp_path / f"commander-{today}.log.2").exists()


# --- AC6: Unit tests cover concurrent-write scenario ---

def test_818__ac6_rotation_at_most_once(logging_mod, tmp_path, monkeypatch):
    """AC6a: When two threads race the rotation boundary, rotation occurs at most once."""
    handler = _make_handler(logging_mod, tmp_path, monkeypatch, max_bytes=200)

    today = datetime.date.today().isoformat()
    log_path = tmp_path / f"commander-{today}.log"
    log_path.write_bytes(b"x" * 195)

    barrier = threading.Barrier(2)

    def emit_one(msg):
        barrier.wait()
        handler.emit(_log_record(msg))

    threads = [threading.Thread(target=emit_one, args=(f"msg-{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    backup_2 = tmp_path / f"commander-{today}.log.2"
    assert not backup_2.exists(), "Rotation happened more than once — .2 backup created"


def test_818__ac6_lock_file_created_when_fcntl_available(logging_mod, tmp_path, monkeypatch):
    """AC6b: .rotate.lock sentinel is created to coordinate inter-process locking."""
    if not logging_mod._HAS_FCNTL:
        pytest.skip("fcntl not available on this platform")

    handler = _make_handler(logging_mod, tmp_path, monkeypatch)
    handler.emit(_log_record("probe"))

    assert (tmp_path / ".rotate.lock").exists(), ".rotate.lock not created"
