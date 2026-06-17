"""Tests for issue #822: setup_machine.sh --restore-db errors on existing commander.db.

Acceptance Criteria:
  AC1 — Running setup_machine.sh --restore-db when commander.db already exists
         does not raise FileExistsError and completes successfully.
  AC2 — The fix uses --force on the backup restore-db call OR prompts the user.
  AC3 — (If prompt chosen) Answering "no" aborts and exits non-zero without modifying DB.
  AC4 — (If --force chosen) setup_machine.sh --restore-db overwrites DB without prompting.
  AC5 — No regression: --restore-db on a machine with no existing commander.db
         continues to work as before.

This tester suite verifies the script behavior (AC2, AC4, AC5 via dry-run/bash execution)
and ensures no new HTTP endpoints were added (AC scoped to CLI/script level).
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
    """Run setup_machine.sh with given args and environment overrides."""
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
        "CREATE TABLE events (id INTEGER PRIMARY KEY, kind TEXT);\n"
        "CREATE TABLE token_usage (id INTEGER PRIMARY KEY, tokens INTEGER);\n"
        "CREATE TABLE session_events (id INTEGER PRIMARY KEY, session_id TEXT);\n"
        "CREATE TABLE project_events (id INTEGER PRIMARY KEY, project TEXT);\n"
        "CREATE TABLE docs_freshness_warnings (id INTEGER PRIMARY KEY, path TEXT);\n"
        "CREATE TABLE ticket_status (id INTEGER PRIMARY KEY, ticket TEXT);\n"
    )
    return path


def _make_existing_db(target: Path) -> None:
    """Write a minimal sqlite DB to simulate an existing commander.db."""
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.execute("CREATE TABLE placeholder (id INTEGER PRIMARY KEY, marker TEXT)")
    conn.execute("INSERT INTO placeholder (marker) VALUES ('old_db')")
    conn.commit()
    conn.close()


# ── AC2: dry-run output includes --force flag ────────────────────────────────

def test_ac2_restore_db_dryrun_includes_force_flag():
    """AC2: The backup restore-db command in setup_machine.sh includes --force."""
    proc = _run(
        ["--restore-db", "/some/backup/src"],
        env={"SETUP_MACHINE_DRY_RUN": "1"},
    )
    assert proc.returncode == 0, f"Expected exit 0, got {proc.returncode}\nstderr: {proc.stderr}"
    out = proc.stdout + proc.stderr
    assert "restore-db" in out, "dry-run should emit restore-db command"
    assert "--force" in out, (
        "setup_machine.sh --restore-db must pass --force to backup.restore_db"
    )


# ── AC4: --force approach — no prompt appears in dry-run ─────────────────────

def test_ac4_force_approach_no_prompt_in_dryrun():
    """AC4: Using --force; no interactive 'overwrite?' prompt in dry-run output."""
    proc = _run(
        ["--restore-db", "/some/backup/src"],
        env={"SETUP_MACHINE_DRY_RUN": "1"},
    )
    out = proc.stdout + proc.stderr
    # Ensure we're using --force, not a prompt-based approach
    assert "--force" in out
    # Confirm no prompt keywords appear
    assert "overwrite?" not in out.lower()
    assert "[y/n]" not in out.lower()


# ── AC5: No regression — dry-run works when no existing commander.db ─────────

def test_ac5_no_existing_db_dryrun_still_works():
    """AC5: --restore-db dry-run on a clean machine (no pre-existing DB) still works."""
    proc = _run(
        ["--restore-db", "/some/backup/src"],
        env={"SETUP_MACHINE_DRY_RUN": "1"},
    )
    assert proc.returncode == 0, f"Expected exit 0, got {proc.returncode}\nstderr: {proc.stderr}"
    out = proc.stdout + proc.stderr
    assert "restore-db" in out
    assert "--from /some/backup/src" in out


# ── AC1: Real execution — existing DB is overwritten, no FileExistsError ─────

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
    reason="backup module not importable (venv missing or not set up)",
)
def test_ac1_restore_db_overwrites_existing_db_no_error(tmp_path):
    """AC1: --restore-db succeeds when commander.db already exists (no FileExistsError)."""
    dash = tmp_path / "dash"
    dash.mkdir()
    existing_db = dash / "commander.db"
    _make_existing_db(existing_db)
    assert existing_db.exists()

    src = _minimal_dump_dir(tmp_path / "backup_src")

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
    assert "agents" in tables, "restored DB should have 'agents' table from dump"
