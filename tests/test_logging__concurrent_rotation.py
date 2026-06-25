"""Tests for issue #818: Guard .commander/logs rotation against multi-process rename race.

AC1: _CommanderFileHandler.emit acquires an fcntl advisory lock covering both
     _rotate_if_needed and the file write.
AC2: No log lines dropped when two processes concurrently write across rotation boundary.
AC3: Lock released immediately after write.
AC4: Graceful fallback when fcntl unavailable (non-POSIX).
AC5: Existing rotation behavior (daily file, size-based rollover, naming, pruning) preserved.
AC6: Unit tests cover concurrent-write scenario with two threads.
"""
import datetime
import importlib
import logging
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


# --- AC1 + AC3: lock acquired and released around rotate+write ---

def test_818__lock_acquired_and_released(logging_mod, tmp_path, monkeypatch):
    """After one emit completes, a second emit succeeds — lock must have been released."""
    handler = _make_handler(logging_mod, tmp_path, monkeypatch)

    handler.emit(_log_record("first line"))
    handler.emit(_log_record("second line"))

    today = datetime.date.today().isoformat()
    lines = _collect_lines(tmp_path, today)
    assert any("first line" in l for l in lines), "first line missing"
    assert any("second line" in l for l in lines), "second line missing"


def test_818__lock_file_created_when_fcntl_available(logging_mod, tmp_path, monkeypatch):
    """When fcntl is available, a .rotate.lock sentinel file is created in log_dir."""
    if not logging_mod._HAS_FCNTL:
        pytest.skip("fcntl not available on this platform")
    handler = _make_handler(logging_mod, tmp_path, monkeypatch)

    handler.emit(_log_record("probe"))

    assert (tmp_path / ".rotate.lock").exists(), ".rotate.lock not created"


# --- AC2 + AC6: concurrent emits across rotation boundary produce no data loss ---

def test_818__no_data_loss_concurrent_emit(logging_mod, tmp_path, monkeypatch):
    """Two concurrent emitters racing a rotation boundary lose no log lines (AC2, AC6)."""
    handler = _make_handler(logging_mod, tmp_path, monkeypatch, max_bytes=200)

    today = datetime.date.today().isoformat()
    log_path = tmp_path / f"commander-{today}.log"
    # Fill to 195 bytes — any write (even a short message) will trigger rotation.
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


def test_818__rotation_at_most_once(logging_mod, tmp_path, monkeypatch):
    """When two threads race the rotation boundary, rotation occurs at most once (no .2)."""
    handler = _make_handler(logging_mod, tmp_path, monkeypatch, max_bytes=200)

    today = datetime.date.today().isoformat()
    log_path = tmp_path / f"commander-{today}.log"
    # Fill to 195 bytes so any write triggers rotation.
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
    assert not backup_2.exists(), "Rotation happened more than once — .2 backup created unexpectedly"


# --- AC4: graceful fallback when fcntl unavailable ---

def test_818__fcntl_unavailable_fallback(logging_mod, tmp_path, monkeypatch):
    """When _HAS_FCNTL is False, emit falls back without raising any exception."""
    monkeypatch.setattr(logging_mod, "_HAS_FCNTL", False)
    handler = _make_handler(logging_mod, tmp_path, monkeypatch)

    # Must not raise
    handler.emit(_log_record("fallback line"))

    today = datetime.date.today().isoformat()
    lines = _collect_lines(tmp_path, today)
    assert any("fallback line" in l for l in lines), "fallback line missing"


# --- AC5: existing rotation naming and archive retention preserved ---

def test_818__existing_rotation_naming_preserved(logging_mod, tmp_path, monkeypatch):
    """Single-threaded rotation still produces .1 suffix and writes new line to active file."""
    handler = _make_handler(logging_mod, tmp_path, monkeypatch, max_bytes=100)

    today = datetime.date.today().isoformat()
    log_path = tmp_path / f"commander-{today}.log"
    log_path.write_bytes(b"x" * 90)  # next write triggers rotation

    handler.emit(_log_record("post-rotation line"))

    assert (tmp_path / f"commander-{today}.log.1").exists(), ".1 backup not created"
    active = tmp_path / f"commander-{today}.log"
    assert active.exists(), "active log file missing after rotation"
    assert "post-rotation line" in active.read_text(encoding="utf-8")


def test_818__backup_count_pruning_preserved(logging_mod, tmp_path, monkeypatch):
    """Backups beyond backup_count are pruned — rotation helper behaviour unchanged."""
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
