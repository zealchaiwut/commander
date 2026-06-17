"""Tests for issue #822 — setup_machine.sh --restore-db should pass --force.

AC coverage:

  AC1 — Running --restore-db when commander.db already exists does not raise
         FileExistsError and completes successfully.
  AC2 — The fix uses --force on the backup restore-db call.
  AC4 — --force approach: existing DB is overwritten without prompting.
  AC5 — No regression: --restore-db on a machine with no existing commander.db
         continues to work as before.
"""

import os
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / "scripts" / "setup_machine.sh"


def _run(args, env=None):
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.run(
        ["bash", str(SCRIPT)] + args,
        capture_output=True,
        text=True,
        env=full_env,
    )


def _minimal_dump_dir(path: Path) -> Path:
    """Write a minimal db_dump.sql that backup.restore_db can consume."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "db_dump.sql").write_text(
        "CREATE TABLE agents (id INTEGER PRIMARY KEY, name TEXT);\n"
        "CREATE TABLE events (id INTEGER PRIMARY KEY, kind TEXT, payload TEXT);\n"
        "CREATE TABLE token_usage (id INTEGER PRIMARY KEY, tokens INTEGER);\n"
    )
    return path


def _make_existing_db(target: Path) -> None:
    """Write a minimal sqlite DB to simulate an existing commander.db."""
    conn = sqlite3.connect(target)
    conn.execute("CREATE TABLE placeholder (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()


# ── AC2: dry-run output includes --force ────────────────────────────────────


def test_restore_db_dryrun_includes_force_flag(tmp_path):
    """AC2: the backup restore-db command emitted by setup_machine.sh includes --force."""
    proc = _run(
        ["--restore-db", "/some/backup/src"],
        env={"SETUP_MACHINE_DRY_RUN": "1"},
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout + proc.stderr
    assert "restore-db" in out
    assert "--force" in out, (
        "setup_machine.sh --restore-db dry-run must include --force in backup call"
    )


# ── AC4: --force approach — no prompt in dry-run ────────────────────────────


def test_restore_db_force_approach_no_prompt_in_dryrun(tmp_path):
    """AC4: --force approach; no interactive prompt appears in the dry-run output."""
    proc = _run(
        ["--restore-db", "/some/backup/src"],
        env={"SETUP_MACHINE_DRY_RUN": "1"},
    )
    out = proc.stdout + proc.stderr
    # The word "prompt" or "overwrite? [y/n]" must not appear
    assert "overwrite?" not in out.lower()
    assert "[y/n]" not in out.lower()
    assert "(y/n)" not in out.lower()


# ── AC1: real execution — existing DB is overwritten, no FileExistsError ─────


def _python_executable() -> str | None:
    """Return the Python that can import services.sprint_manager.backup, or None."""
    venv_py = REPO_ROOT / "venv" / "bin" / "python"
    if venv_py.exists():
        return str(venv_py)
    # Fall back to the interpreter running pytest — it may have the module on sys.path.
    return sys.executable


@pytest.mark.skipif(
    not (REPO_ROOT / "venv" / "bin" / "python").exists()
    and not (REPO_ROOT / "services" / "sprint_manager" / "backup.py").exists(),
    reason="backup module not importable from this environment",
)
def test_restore_db_overwrites_existing_db_no_error(tmp_path):
    """AC1: --restore-db succeeds when commander.db already exists (no FileExistsError)."""
    dash = tmp_path / "dash"
    dash.mkdir()
    existing_db = dash / "commander.db"
    _make_existing_db(existing_db)
    assert existing_db.exists()

    src = _minimal_dump_dir(tmp_path / "backup_src")

    # Point venv at a non-existent path so the script falls back to python3
    # (which has the backup module on PYTHONPATH via REPO_ROOT).
    python_exe = _python_executable()
    env = {
        "COMMANDER_DASHBOARD_DIR": str(dash),
        "COMMANDER_VENV_DIR": str(tmp_path / "nonexistent_venv"),
    }
    if python_exe != "python3":
        # Inject a shim so the script's PATH finds the right python as "python3".
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        shim = bin_dir / "python3"
        shim.write_text(f"#!/bin/sh\nexec {python_exe} \"$@\"\n")
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"

    proc = _run(["--restore-db", str(src)], env=env)

    assert "FileExistsError" not in (proc.stdout + proc.stderr), (
        f"FileExistsError must not appear when --force is passed\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert proc.returncode == 0, (
        f"Expected exit 0 but got {proc.returncode}\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    # DB should have been replaced with restored schema
    assert existing_db.exists()
    conn = sqlite3.connect(existing_db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    assert "agents" in tables, "restored DB should have 'agents' table"


# ── AC5: no regression — works when no existing commander.db ─────────────────


def test_restore_db_no_existing_db_dryrun_still_works(tmp_path):
    """AC5: --restore-db dry-run on a clean machine (no pre-existing DB) still works."""
    proc = _run(
        ["--restore-db", "/some/backup/src"],
        env={"SETUP_MACHINE_DRY_RUN": "1"},
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout + proc.stderr
    assert "restore-db" in out
    assert "--from /some/backup/src" in out
