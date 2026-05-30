"""Structured event logging for Commander.

Usage (JSON-lines sink):
    from services.logging import log, generate_run_id
    log.event("test.start", run_id=generate_run_id("manual"), issue_num=42)

Usage (human-readable via stdlib logging):
    import logging
    logging.getLogger("commander").info("hello")

Usage (level-based structured logging, backward compat):
    from services.logging import log
    log.set_context(run_id="sprint-20260531T120000-a3b4c5d6", issue_num=170)
    log.info("dispatch_start", "started coder")
"""
from __future__ import annotations

import datetime
import fcntl
import json
import logging
import os
import secrets
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

_LEVEL_ORDER = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}
_REQUIRED_KEYS = (
    "ts", "level", "run_id", "source", "agent_role",
    "issue_num", "sprint_label", "project", "git_sha", "event", "message",
)
_VALID_SOURCES = frozenset({"sprint", "manual", "adhoc"})


def _get_git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return "unknown"


def _resolve_log_dir() -> Path:
    """Walk up from cwd to find .commander/logs, falling back to cwd/.commander/logs."""
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        candidate = parent / ".commander"
        if candidate.is_dir():
            return candidate / "logs"
    return cwd / ".commander" / "logs"


def generate_run_id(source: str) -> str:
    """Return a run_id string in format <source>-<YYYYMMDDTHHmmss>-<8hex>.

    source must be one of: sprint, manual, adhoc.
    Raises ValueError for any other value.
    """
    if source not in _VALID_SOURCES:
        raise ValueError(
            f"Invalid source {source!r}; must be one of {sorted(_VALID_SOURCES)}"
        )
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S")
    rand = secrets.token_hex(4)
    return f"{source}-{ts}-{rand}"


def _append_line(path: Path, line: str) -> None:
    """Append line to path via O_APPEND + advisory lock. Raises OSError on failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, line.encode())
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


class _Logger:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._context: dict[str, Any] = {}
        self._git_sha: str = _get_git_sha()

    def set_context(self, **kwargs: Any) -> None:
        """Merge kwargs into the persistent context applied to every structured record."""
        with self._lock:
            self._context.update(kwargs)

    def _active_level(self) -> int:
        raw = os.environ.get("COMMANDER_LOG_LEVEL", "INFO").upper()
        return _LEVEL_ORDER.get(raw, _LEVEL_ORDER["INFO"])

    def _structured_log_path(self) -> Path:
        log_dir = _resolve_log_dir()
        today = datetime.date.today().isoformat()
        return log_dir / f"structured-{today}.log"

    def _events_log_path(self) -> Path:
        log_dir = _resolve_log_dir()
        today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        return log_dir / f"events-{today}.jsonl"

    def event(self, name: str, **kwargs: Any) -> None:
        """Write one JSON-lines event to events-YYYY-MM-DD.jsonl.

        Includes automatic ISO-8601 UTC timestamp and name fields.
        Accepted correlation keys: run_id, issue_num, sprint_label, agent_role,
        project, git_sha. Extra kwargs are included as-is.
        Never raises; IO errors are printed to stderr and suppressed.
        """
        try:
            record: dict[str, Any] = {
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "name": name,
            }
            record.update(kwargs)
            _append_line(self._events_log_path(), json.dumps(record, default=str) + "\n")
        except Exception as exc:
            print(f"[commander.logging] IO error writing event: {exc}", file=sys.stderr)

    def _emit(self, level: str, event: str, message: str, **fields: Any) -> None:
        level = level.upper()
        if _LEVEL_ORDER.get(level, 0) < self._active_level():
            return

        with self._lock:
            ctx = dict(self._context)

        record: dict[str, Any] = {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "level": level,
            "run_id": None,
            "source": None,
            "agent_role": None,
            "issue_num": None,
            "sprint_label": None,
            "project": None,
            "git_sha": self._git_sha,
            "event": event,
            "message": message,
        }
        record.update(ctx)
        record.update(fields)

        for key in _REQUIRED_KEYS:
            if key not in record:
                record[key] = None

        _append_line(self._structured_log_path(), json.dumps(record, default=str) + "\n")

    def info(self, event: str, message: str, **fields: Any) -> None:
        self._emit("INFO", event, message, **fields)

    def warn(self, event: str, message: str, **fields: Any) -> None:
        self._emit("WARN", event, message, **fields)

    def error(self, event: str, message: str, **fields: Any) -> None:
        self._emit("ERROR", event, message, **fields)

    def debug(self, event: str, message: str, **fields: Any) -> None:
        self._emit("DEBUG", event, message, **fields)


log = _Logger()


class _CommanderFileHandler(logging.Handler):
    """stdlib logging handler that resolves .commander/logs at emit time."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            log_dir = _resolve_log_dir()
            log_dir.mkdir(parents=True, exist_ok=True)
            today = datetime.date.today().isoformat()
            path = log_dir / f"commander-{today}.log"
            msg = self.format(record) + "\n"
            with open(path, "a", encoding="utf-8") as f:
                f.write(msg)
        except OSError as exc:
            print(f"[commander.logging] IO error: {exc}", file=sys.stderr)


def _build_commander_logger() -> logging.Logger:
    logger = logging.getLogger("commander")
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        handler = _CommanderFileHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%SZ",
        ))
        logger.addHandler(handler)
        logger.propagate = False
    return logger


commander_logger: logging.Logger = _build_commander_logger()
