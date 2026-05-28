"""Structured JSON-lines logging module for Commander.

Usage:
    from services.logging import log

    log.set_context(run_id="sprint12-20260529-a3f", issue_num=170)
    log.info("dispatch_start", "started coder")
    log.warn("hang_detected", "agent stalled", agent_role="coder")
"""
from __future__ import annotations

import datetime
import fcntl
import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Any

_LEVEL_ORDER = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}
_REQUIRED_KEYS = (
    "ts", "level", "run_id", "source", "agent_role",
    "issue_num", "sprint_label", "project", "git_sha", "event", "message",
)


def _get_git_sha() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return None


def _resolve_log_dir() -> Path:
    """Walk up from cwd to find .commander/logs, falling back to cwd/.commander/logs."""
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        candidate = parent / ".commander"
        if candidate.is_dir():
            return candidate / "logs"
    return cwd / ".commander" / "logs"


class _Logger:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._context: dict[str, Any] = {}
        self._git_sha: str | None = _get_git_sha()

    def set_context(self, **kwargs: Any) -> None:
        """Merge kwargs into the persistent context applied to every record."""
        with self._lock:
            self._context.update(kwargs)

    def _active_level(self) -> int:
        raw = os.environ.get("COMMANDER_LOG_LEVEL", "INFO").upper()
        return _LEVEL_ORDER.get(raw, _LEVEL_ORDER["INFO"])

    def _log_path(self) -> Path:
        log_dir = _resolve_log_dir()
        today = datetime.date.today().isoformat()
        return log_dir / f"structured-{today}.log"

    def event(self, level: str, event: str, message: str, **fields: Any) -> None:
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

        # Ensure all required keys are present (never omitted)
        for key in _REQUIRED_KEYS:
            if key not in record:
                record[key] = None

        line = json.dumps(record, default=str) + "\n"
        log_path = self._log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # O_APPEND + fcntl advisory lock for thread- and process-safety
        fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            os.write(fd, line.encode())
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def info(self, event: str, message: str, **fields: Any) -> None:
        self.event("INFO", event, message, **fields)

    def warn(self, event: str, message: str, **fields: Any) -> None:
        self.event("WARN", event, message, **fields)

    def error(self, event: str, message: str, **fields: Any) -> None:
        self.event("ERROR", event, message, **fields)

    def debug(self, event: str, message: str, **fields: Any) -> None:
        self.event("DEBUG", event, message, **fields)


log = _Logger()
