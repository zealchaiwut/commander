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
try:
    import fcntl as _fcntl
    _HAS_FCNTL = True
except ImportError:
    _fcntl = None  # type: ignore[assignment]
    _HAS_FCNTL = False
import json
import logging
import logging.handlers
import os
import re
import secrets
import subprocess
import sys
import threading
from enum import Enum
from pathlib import Path
from typing import Any, TextIO

_LEVEL_ORDER = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}
_REQUIRED_KEYS = (
    "ts", "level", "run_id", "source", "agent_role",
    "issue_num", "sprint_label", "project", "git_sha", "event", "message",
)

# ---------------------------------------------------------------------------
# Dispatch lifecycle events — category mapping (issue #902)
# ---------------------------------------------------------------------------
# Events emitted via structured_log.info/error in sprint_manager.py are teed
# into events-<date>.jsonl with a `category` field so the Activity API can
# surface and filter them. No new call sites needed in sprint_manager.py —
# the tee is done centrally in _emit() below.
_LIFECYCLE_EVENT_CATEGORY: dict[str, str] = {
    "dispatch_start":    "agent",
    "dispatch_finished": "agent",
    "dispatch_failed":   "error",
    "dispatch_killed":   "error",
    "gate_failed":       "error",
    "sprint_crashed":    "error",
    "gate_passed":       "gate",
    "sidecar_written":   "sprint",
}

# Fields to carry from the structured record into the events JSONL entry.
# Only keys that are explicitly listed travel; everything else is dropped to
# keep the events file compact and avoid leaking raw message strings.
_LIFECYCLE_FIELDS = frozenset({
    "run_id", "issue_num", "agent_role", "sprint_label", "project",
    "attempt", "exit_code", "duration_s", "gate", "reason",
    "stderr_tail", "sidecar", "error",
})


class EventType(Enum):
    """Canonical lifecycle event vocabulary for sprint orchestration log records.

    Every structured emit() call must pass one of these values as event_type so
    no string literals appear at call sites in services/sprint_manager.
    """
    dispatched   = "dispatched"
    started      = "started"
    finished     = "finished"
    failed       = "failed"
    killed       = "killed"
    resumed      = "resumed"
    transitioned = "transitioned"


def envelope_subprocess_line(
    raw: str,
    run_id: str,
    sprint: str,
    issue: int,
    agent: str,
) -> dict:
    """Wrap one line of agent subprocess output in the structured envelope schema.

    Returns a dict ready for json.dumps(); callers append '\\n' and write to the
    agent log file.  The 'raw' field preserves the original text verbatim so the
    run browser can render it identically to pre-migration output.
    """
    return {
        "run_id": run_id,
        "sprint": sprint,
        "issue": issue,
        "agent": agent,
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "raw": raw,
    }
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


# ---------------------------------------------------------------------------
# Log rotation config (issue #762)
# ---------------------------------------------------------------------------

# Default rotation bounds for prd.log and the .commander/logs handler.
# Both are overridable via env so a dev/test run can lower maxBytes and exercise
# the rollover path without writing gigabytes.
_DEFAULT_LOG_MAX_BYTES = 10_000_000
_DEFAULT_LOG_BACKUP_COUNT = 5


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _log_max_bytes() -> int:
    return _env_int("COMMANDER_LOG_MAX_BYTES", _DEFAULT_LOG_MAX_BYTES)


def _log_backup_count() -> int:
    return _env_int("COMMANDER_LOG_BACKUP_COUNT", _DEFAULT_LOG_BACKUP_COUNT)


def _prd_log_path() -> Path:
    """Resolve the prd.log path. Defaults to apps/dashboard/prd.log at repo root."""
    override = os.environ.get("COMMANDER_PRD_LOG")
    if override:
        return Path(override)
    repo_root = Path(__file__).resolve().parent.parent
    return repo_root / "apps" / "dashboard" / "prd.log"


def setup_logging(
    log_path: "Path | str | None" = None,
    *,
    max_bytes: "int | None" = None,
    backup_count: "int | None" = None,
) -> logging.handlers.RotatingFileHandler:
    """Install a size-rotating handler for prd.log on the root logger.

    Wired at every server entry point (run_server, server import) so the
    unattended prd.log can never exhaust disk. Idempotent: a second call with an
    already-installed Commander handler returns the existing handler instead of
    stacking a duplicate. maxBytes/backupCount default to 10_000_000 / 5 and are
    overridable via COMMANDER_LOG_MAX_BYTES / COMMANDER_LOG_BACKUP_COUNT.
    """
    path = Path(log_path) if log_path is not None else _prd_log_path()
    max_bytes = max_bytes if max_bytes is not None else _log_max_bytes()
    backup_count = backup_count if backup_count is not None else _log_backup_count()

    root = logging.getLogger()
    for h in root.handlers:
        if getattr(h, "_commander_prd", False):
            return h  # type: ignore[return-value]

    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        str(path), maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    handler._commander_prd = True  # type: ignore[attr-defined]
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    ))
    root.addHandler(handler)
    if root.level in (logging.NOTSET, logging.WARNING):
        root.setLevel(logging.INFO)
    return handler


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
        if _HAS_FCNTL:
            _fcntl.flock(fd, _fcntl.LOCK_EX)
        os.write(fd, line.encode())
    finally:
        if _HAS_FCNTL:
            _fcntl.flock(fd, _fcntl.LOCK_UN)
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

        # Tee dispatch lifecycle events into events-<date>.jsonl (issue #902).
        # Done here centrally so sprint_manager.py call sites need no changes.
        category = _LIFECYCLE_EVENT_CATEGORY.get(event)
        if category is not None:
            try:
                evt: dict[str, Any] = {
                    "timestamp": record["ts"],
                    "name": event,
                    "category": category,
                }
                # Carry only the whitelisted correlation + payload fields.
                for fld in _LIFECYCLE_FIELDS:
                    val = record.get(fld)
                    if val is not None:
                        evt[fld] = val
                _append_line(self._events_log_path(), json.dumps(evt, default=str) + "\n")
            except Exception as _tee_exc:
                print(
                    f"[commander.logging] IO error teeing lifecycle event: {_tee_exc}",
                    file=sys.stderr,
                )

    def info(self, event: str, message: str, **fields: Any) -> None:
        self._emit("INFO", event, message, **fields)

    def warn(self, event: str, message: str, **fields: Any) -> None:
        self._emit("WARN", event, message, **fields)

    def error(self, event: str, message: str, **fields: Any) -> None:
        self._emit("ERROR", event, message, **fields)

    def debug(self, event: str, message: str, **fields: Any) -> None:
        self._emit("DEBUG", event, message, **fields)

    def emit(
        self,
        *,
        event_type: "EventType",
        run_id: str,
        sprint: str,
        issue: int,
        log_path: "Path | None" = None,
        **fields: Any,
    ) -> None:
        """Write one structured lifecycle event using the canonical EventType vocabulary.

        Requires event_type (EventType enum), run_id, sprint, and issue so every
        lifecycle call site has the full correlation context.  When log_path is
        provided the record is written there as a JSON-Lines entry; the internal
        structured log is always written regardless.
        """
        record: dict[str, Any] = {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "event_type": event_type.value,
            "run_id": run_id,
            "sprint": sprint,
            "issue": issue,
        }
        record.update(fields)
        line = json.dumps(record, default=str) + "\n"
        if log_path is not None:
            try:
                _append_line(Path(log_path), line)
            except OSError as exc:
                sys.stderr.write(f"[commander.logging] emit IO error: {exc}\n")
        self._emit("INFO", event_type.value, f"lifecycle:{event_type.value}", **record)


log = _Logger()


def _rotate_if_needed(path: Path, incoming_len: int, max_bytes: int, backup_count: int) -> None:
    """Size-rotate `path` like RotatingFileHandler before an append would exceed
    max_bytes. Renames path -> path.1 -> path.2 ... pruning beyond backup_count.
    No-op when max_bytes <= 0 (rotation disabled) or the file would still fit.
    """
    if max_bytes <= 0 or backup_count <= 0:
        return
    try:
        current = path.stat().st_size
    except OSError:
        return
    if current + incoming_len <= max_bytes:
        return
    # Drop the oldest backup, then shift the rest up by one.
    oldest = path.with_name(f"{path.name}.{backup_count}")
    if oldest.exists():
        oldest.unlink()
    for i in range(backup_count - 1, 0, -1):
        src = path.with_name(f"{path.name}.{i}")
        if src.exists():
            src.rename(path.with_name(f"{path.name}.{i + 1}"))
    if path.exists():
        path.rename(path.with_name(f"{path.name}.1"))


# Module-level lock for intra-process (thread) serialization of rotate+append.
# fcntl.flock on .rotate.lock handles inter-process serialization.
_ROTATE_LOCK = threading.Lock()


class _CommanderFileHandler(logging.Handler):
    """stdlib logging handler that resolves .commander/logs at emit time.

    Size-rotates each daily file (commander-YYYY-MM-DD.log) using the same bounds
    as prd.log so .commander/logs has an equivalent verified upper bound
    (issue #762): maxBytes default 10_000_000, backupCount default 5.

    The rotate+append sequence is guarded by both a threading.Lock (intra-process)
    and an fcntl advisory lock on .rotate.lock (inter-process) so concurrent
    writers never race the rename or drop log lines (issue #818).
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            log_dir = _resolve_log_dir()
            log_dir.mkdir(parents=True, exist_ok=True)
            today = datetime.date.today().isoformat()
            path = log_dir / f"commander-{today}.log"
            msg = self.format(record) + "\n"
            encoded = msg.encode()
            with _ROTATE_LOCK:
                if _HAS_FCNTL:
                    lock_path = log_dir / ".rotate.lock"
                    lock_fd = os.open(str(lock_path), os.O_WRONLY | os.O_CREAT, 0o644)
                    try:
                        _fcntl.flock(lock_fd, _fcntl.LOCK_EX)
                        _rotate_if_needed(path, len(encoded), _log_max_bytes(), _log_backup_count())
                        with open(path, "ab") as f:
                            f.write(encoded)
                    finally:
                        _fcntl.flock(lock_fd, _fcntl.LOCK_UN)
                        os.close(lock_fd)
                else:
                    _rotate_if_needed(path, len(encoded), _log_max_bytes(), _log_backup_count())
                    with open(path, "ab") as f:
                        f.write(encoded)
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


# ---------------------------------------------------------------------------
# Orchestrator stdout timestamps (sprint_manager dispatch log)
# ---------------------------------------------------------------------------

ORCHESTRATOR_LOG_TS_RE = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?::\d{2})?)\]\s*(.*)$",
    re.DOTALL,
)


def _orchestrator_line_ts() -> str:
    """Local wall-clock time for orchestrator log line prefixes."""
    return datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")


class TimestampLineWriter:
    """Prefix each written line with ``[YYYY-MM-DD HH:MM]``."""

    def __init__(self, fh: TextIO) -> None:
        self._fh = fh
        self._buf = ""

    def write(self, data: str) -> int:
        if not data:
            return 0
        self._buf += data
        written = 0
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            written += self._emit_line(line)
        return written or len(data)

    def _emit_line(self, line: str) -> int:
        line = line.rstrip("\r")
        if line:
            payload = f"[{_orchestrator_line_ts()}] {line}\n"
        else:
            payload = "\n"
        self._fh.write(payload)
        return len(payload)

    def flush(self) -> None:
        if self._buf:
            self._emit_line(self._buf.rstrip("\r"))
            self._buf = ""
        self._fh.flush()

    def fileno(self) -> int:
        return self._fh.fileno()


class _TimestampingTextIO:
    """Drop-in ``sys.stdout`` wrapper that stamps each complete line."""

    _orchestrator_ts_wrapped = True

    def __init__(self, underlying: TextIO) -> None:
        self._underlying = underlying
        self._writer = TimestampLineWriter(underlying)

    def write(self, data: str) -> int:
        return self._writer.write(data)

    def flush(self) -> None:
        self._writer.flush()

    def fileno(self) -> int:
        return self._underlying.fileno()

    def isatty(self) -> bool:
        return self._underlying.isatty()

    @property
    def encoding(self) -> str:
        return self._underlying.encoding

    def writable(self) -> bool:
        return True


def install_orchestrator_stdout_timestamps() -> None:
    """Wrap ``sys.stdout`` so every orchestrator line gets a wall-clock prefix."""
    if os.environ.get("COMMANDER_ORCH_LOG_NO_TS") == "1":
        return
    out = sys.stdout
    if getattr(out, "_orchestrator_ts_wrapped", False):
        return
    sys.stdout = _TimestampingTextIO(out)  # type: ignore[assignment]
