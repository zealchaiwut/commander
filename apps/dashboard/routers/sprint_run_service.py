"""Subprocess glue and request body models for sprint run/kill (issue #1263).

This module owns all process spawn, signal, and status-tracking logic so that
the route handlers in sprint_run.py stay thin. Pydantic request body models
are also defined here to avoid circular imports with server.py.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Optional

from fastapi import HTTPException
from pydantic import BaseModel


# ── Request body models (mirrors of the server.py originals) ─────────────────


class SprintMgmtRunBody(BaseModel):
    project: str
    sprint_label: str
    migrate_from: list[int] = []
    use_cline_followups: bool = False
    # Per-run LLM provider (issue #1667 follow-up): "anthropic" (direct,
    # subscription OAuth) or "ica" (route agents through claude-proxy to IBM
    # ICA). Explicit default means every modal-started run states its choice —
    # the next run auto-reverts to anthropic unless re-selected.
    llm_provider: str = "anthropic"


class SprintRerunV2Body(BaseModel):
    ticket_numbers: list[int] = []
    auto_run: bool = True
    confirm: Optional[bool] = None


def spawn_sprint_process(
    argv: list[str],
    *,
    cwd: str,
    env: dict,
    log_path: Path,
    pid_path: Path,
    pending_path: Path,
    sprint_label: str,
    project: str,
) -> subprocess.Popen:
    """Atomically claim a sprint slot and spawn the sprint_manager subprocess.

    Phase 1: create the pending file with O_CREAT|O_EXCL so concurrent
    requests race to the same syscall; only one wins (raises 409 on loss).
    Phase 2: write the real PID and atomically rename pending → pid (rename(2)).

    After spawn, waits up to 2 s for early-crash detection. If the process
    exits within that window, cleans up the PID file and raises HTTP 502 with
    the log tail so callers get a meaningful error.

    Returns the live Popen object on success.
    """
    # Phase 1: atomic claim
    try:
        fd = os.open(str(pending_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        os.write(fd, b"0")
        os.close(fd)
    except FileExistsError:
        raise HTTPException(
            409,
            detail=f"Sprint {sprint_label} is already running on {project}",
        )

    log_fh = open(log_path, "w")
    try:
        proc = subprocess.Popen(
            argv,
            env=env,
            cwd=cwd,
            stdout=log_fh,
            stderr=log_fh,
            start_new_session=True,
        )
    except Exception:
        try:
            pending_path.unlink()
        except OSError:
            pass
        raise

    # Phase 2: atomic rename pending → pid
    pending_path.write_text(str(proc.pid), encoding="utf-8")
    os.replace(str(pending_path), str(pid_path))

    # Early-crash detection: subprocess exits within 2 s → startup failure
    try:
        proc.wait(timeout=2.0)
        log_fh.flush()
        try:
            log_text = log_path.read_text(encoding="utf-8", errors="replace")
            tail = "\n".join(log_text.splitlines()[-30:]) if log_text else "(no output)"
        except OSError:
            tail = "(could not read log)"
        try:
            pid_path.unlink()
        except OSError:
            pass
        raise HTTPException(
            502,
            detail=(
                f"Sprint subprocess exited immediately (rc={proc.returncode}). "
                f"Log tail:\n{tail}"
            ),
        )
    except subprocess.TimeoutExpired:
        pass  # still running — normal startup

    return proc


def kill_sprint_process(pid: int) -> None:
    """Send SIGTERM to pid, wait up to 5 s for graceful exit, then SIGKILL.

    Silently ignores ProcessLookupError and PermissionError at each step so
    callers don't need try/except around the common case of a fast exit.
    """
    if pid <= 0:
        return

    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return

    for _ in range(10):
        time.sleep(0.5)
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            return

    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
