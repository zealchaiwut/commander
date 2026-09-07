"""Process-group-safe pytest launcher (issue #2345).

``subprocess.run(..., timeout=T)`` only SIGKILLs the direct child. Commander's
suite contains meta-tests that spawn nested ``pytest`` processes; when an outer
timeout (finish_feature / record_test_baseline / dispatch gate / suite health)
fires, those grandchildren survive as PPID-1 orphans and keep running — and can
themselves spawn further nested pytests. That is the runaway process tree.

This helper starts pytest in a new session (``start_new_session=True``) so the
whole tree shares a process group, then ``killpg`` on timeout.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Mapping, Optional, Sequence, Union


PathLike = Union[str, Path]


def run_pytest(
    args: Sequence[str],
    *,
    cwd: PathLike,
    timeout: Optional[float] = None,
    env: Optional[Mapping[str, str]] = None,
    isolate_db: bool = True,
) -> subprocess.CompletedProcess:
    """Run ``python -m pytest <args>`` and kill the whole process group on timeout.

    When ``isolate_db`` is True (default), sets a unique ``COMMANDER_TEST_DB`` /
    ``DB_PATH`` so this run cannot collide with another concurrent suite on the
    shared ``/tmp/commander-pytest.db`` path (a second source of non-determinism
    under overlapping orphans — issue #2345).
    """
    run_env = dict(os.environ)
    if env:
        run_env.update(env)

    tmp_db_dir: Optional[tempfile.TemporaryDirectory] = None
    if isolate_db and "COMMANDER_TEST_DB" not in (env or {}):
        tmp_db_dir = tempfile.TemporaryDirectory(prefix="commander-pytest-db-")
        db_path = str(Path(tmp_db_dir.name) / f"{uuid.uuid4().hex}.db")
        run_env["COMMANDER_TEST_DB"] = db_path
        run_env["DB_PATH"] = db_path

    cmd = [sys.executable, "-m", "pytest", *list(args)]
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=run_env,
        start_new_session=True,
    )
    try:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            _kill_process_group(proc.pid)
            # Drain pipes after the kill so we do not leak FDs / zombie the child.
            try:
                stdout, stderr = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
                stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                proc.wait(timeout=5)
            raise subprocess.TimeoutExpired(
                cmd, timeout, output=stdout, stderr=stderr,
            ) from None
        return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
    finally:
        if tmp_db_dir is not None:
            tmp_db_dir.cleanup()


def _kill_process_group(pid: int) -> None:
    """SIGKILL the process group led by ``pid``. No-op if it is already gone."""
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        # Process already exited, or we are not the group leader somehow —
        # fall back to killing the direct child only.
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
