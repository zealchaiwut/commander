"""Timekeeping helpers for sprint_manager.

Contains: _token_window_sums, _utcnow, _bangkok_now, _wait_if_paused,
_setup_pid_file, _acquire_pid_lock, _release_pid_lock, and supporting
helpers/constants extracted from sprint_manager.py (issue #1277).
"""
from __future__ import annotations

import atexit
import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from services.sprint_manager.events import _post_sprint_status
from services.sprint_manager.paths import _pid_file_path

if TYPE_CHECKING:
    from services.sprint_manager.config import SprintConfig
    from services.sprint_manager.state import SprintState

# Repo root is three levels up: timekeeping.py → sprint_manager/ → services/ → root
_REPO_ROOT     = Path(__file__).parent.parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"
SPRINTS_DIR    = _DASHBOARD_DIR / "sprints"

_BANGKOK_TZ = timezone(timedelta(hours=7))

_active_pid_path: Optional[Path] = None


def _token_window_utc_now() -> str:
    """UTC timestamp in the exact format token_usage.recorded_at uses."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _token_window_sums(role: str, since_utc: str) -> "tuple[int, int]":
    """Best-effort (input, output) token sums for *role* since *since_utc*.

    Attributes a dispatch's token spend by role + time window over the
    token_usage rows the PostToolUse hook records. Correct while at most one
    agent per role runs at a time (serial mode, and coder∥tester pipeline —
    the roles differ). Returns (0, 0) when the dashboard DB is unavailable.
    """
    try:
        import db  # apps/dashboard on sys.path
        return db.sum_token_usage_window(role, since_utc, _token_window_utc_now())
    except (Exception, SystemExit):
        return (0, 0)


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _bangkok_now() -> str:
    """Return current Bangkok time (UTC+7) as YYYY-MM-DDTHH:MM:SS+07:00."""
    return datetime.now(_BANGKOK_TZ).strftime("%Y-%m-%dT%H:%M:%S+07:00")


def _to_bangkok(utc_str: str) -> str:
    """Convert a UTC timestamp string ending in Z to Bangkok local time."""
    try:
        dt = datetime.strptime(utc_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return dt.astimezone(_BANGKOK_TZ).strftime("%Y-%m-%dT%H:%M:%S+07:00")
    except (ValueError, TypeError):
        return _bangkok_now()


def _remove_pid_file() -> None:
    """Remove the sprint PID file if it exists (called by atexit + signal handlers).

    Also clears the run lock (COMMANDER_SPRINT_RUNNING) so non-status label
    mutations are allowed again once the run ends (issue #754).
    """
    global _active_pid_path
    os.environ.pop("COMMANDER_SPRINT_RUNNING", None)
    if _active_pid_path and _active_pid_path.exists():
        try:
            _active_pid_path.unlink()
        except OSError:
            pass


def _setup_pid_file(sprint_num: Optional[int]) -> None:
    """Write PID to dashboard/sprints/sprint-N.pid and register cleanup handlers."""
    global _active_pid_path
    # Hold the label lock for the duration of the run (issue #754). Set before the
    # sprint_num guard so the lock — and its cleanup — covers every real run. It
    # propagates to dispatched agents because each subprocess inherits
    # os.environ.copy() (see sub_env below).
    os.environ["COMMANDER_SPRINT_RUNNING"] = "1"

    # Register lock + PID cleanup on every exit path before the sprint_num guard,
    # so the run lock is always cleared on exit even when no PID file is written.
    atexit.register(_remove_pid_file)

    def _sig_handler(signum: int, frame: object) -> None:
        _remove_pid_file()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _sig_handler)
    signal.signal(signal.SIGINT, _sig_handler)

    if sprint_num is None:
        return
    sprints_dir = SPRINTS_DIR
    sprints_dir.mkdir(parents=True, exist_ok=True)
    pid_path = sprints_dir / f"sprint-{sprint_num}.pid"
    pid_path.write_text(str(os.getpid()), encoding="utf-8")
    _active_pid_path = pid_path


def _wait_if_paused(
    sprint_num: Optional[int],
    state: "SprintState",
    api_url: Optional[str] = None,
) -> None:
    """Block until the sprint-N.pause file is removed, then broadcast resume."""
    if sprint_num is None:
        return
    pause_file = SPRINTS_DIR / f"sprint-{sprint_num}.pause"
    if not pause_file.exists():
        return
    sys.stdout.write(str("Paused — waiting for resume…") + "\n")
    sys.stdout.flush()
    while pause_file.exists():
        time.sleep(5)
    sys.stdout.write(str("Resuming sprint…") + "\n")
    sys.stdout.flush()
    _post_sprint_status(state, api_url=api_url)


def _acquire_pid_lock(sprint_label: str, project: str,
                      cfg: Optional["SprintConfig"] = None) -> Path:
    """Write a PID file scoped to (project, sprint_label).

    Handles three cases:
    1. No file exists (CLI dispatch): creates the file fresh.
    2. File already exists with *our own* PID (server-dispatch two-phase claim,
       issue #155): file is already correct — nothing to do.
    3. File exists with a different PID:
       a. Process alive → another instance is running; exit with an error.
       b. Process dead → stale lock; log, clean up, then write fresh.

    Returns the path so the caller can release it on exit.
    """
    pid_path = _pid_file_path(sprint_label, cfg)
    my_pid   = os.getpid()

    if pid_path.exists():
        stale_pid: Optional[int] = None
        try:
            stale_pid = int(pid_path.read_text().strip())
        except (ValueError, OSError):
            pass

        # Case 2: server already wrote our PID via two-phase claim — nothing to do.
        if stale_pid == my_pid:
            return pid_path

        alive = False
        if stale_pid is not None:
            try:
                os.kill(stale_pid, 0)
                alive = True
            except ProcessLookupError:
                alive = False
            except PermissionError:
                alive = True  # process exists but is owned by another user

        if alive:
            sys.exit(
                f"Sprint {sprint_label} is already running on {project} (PID {stale_pid})"
            )
        else:
            sys.stderr.write(str(f"  [pid-lock] Stale lock from PID {stale_pid} cleaned up") + "\n")
            try:
                pid_path.unlink()
            except OSError:
                pass

    pid_path.write_text(str(my_pid))
    return pid_path


def _release_pid_lock(pid_path: Path) -> None:
    """Remove the PID file; idempotent."""
    try:
        pid_path.unlink()
    except OSError:
        pass
