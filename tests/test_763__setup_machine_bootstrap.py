"""Tests for issue #763 — setup_machine.sh bootstrap script with doctor checks.

AC coverage (code-testable subset; the clean-macOS-account / real-dashboard
boot steps are covered by the manual UAT steps in the issue):

  AC1  — scripts/setup_machine.sh is idempotent (re-run = no errors, no
         unintended side effects)
  AC2  — creates venv + runs pip install -r requirements.txt when venv absent
  AC3  — creates .env from .env.example, prompting for required keys; values
         are never echoed to stdout
  AC4  — constructs ~/dev/commander/{prd,uat} layout if missing; documents or
         creates coder/tester worktrees
  AC5  — --restore-gist <id> hooks into backup.py config-restore path
  AC6  — --restore-db <source> hooks into backup.py db-restore path
  AC7  — doctor section prints PASS/FAIL table for gh / claude / tailscale /
         port / sqlite3
  AC8  — exits nonzero if any doctor check FAILs
  AC9  — doctor output references install_launchd.sh for launchd-related failures
  AC10 — README "New machine" section reduced to clone -> bash scripts/setup_machine.sh
  AC11 — interactive gh/claude logins are detected and instructed, not silently
         skipped

The script routes every side-effecting path through overridable env vars
(COMMANDER_DASHBOARD_DIR, COMMANDER_VENV_DIR, COMMANDER_PROJECT_DIR) and a
SETUP_MACHINE_DRY_RUN echo mode, so these tests never touch the real clone,
the real venv, or ~/dev/commander.
"""

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / "scripts" / "setup_machine.sh"
ENV_EXAMPLE = REPO_ROOT / "apps" / "dashboard" / ".env.example"


# ── helpers ───────────────────────────────────────────────────────────────────


def _run(args, env=None, input_text=None):
    """Run setup_machine.sh with extra env; return CompletedProcess."""
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.run(
        ["bash", str(SCRIPT)] + args,
        capture_output=True,
        text=True,
        env=full_env,
        input=input_text,
    )


def _make_stub(path: Path, exit_code: int = 0, stdout: str = ""):
    """Write an executable stub command at path."""
    body = "#!/usr/bin/env bash\n"
    if stdout:
        body += f"printf '%s\\n' {stdout!r}\n"
    body += f"exit {exit_code}\n"
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _doctor_env(tmp_path, *, gh=0, claude=True, tailscale=0, sqlite3=True,
                lsof_listener=False):
    """Build a PATH of doctor stubs + a HOME with claude creds, return env dict.

    Each knob controls whether a given check passes:
      gh/tailscale       : exit code of the stub command (0 = PASS)
      claude/sqlite3     : whether the binary stub exists on PATH at all
      lsof_listener      : if True, lsof reports a listener (port NOT free)
    """
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)

    _make_stub(bindir / "gh", exit_code=gh)
    _make_stub(bindir / "tailscale", exit_code=tailscale)
    if claude:
        _make_stub(bindir / "claude")
        creds = home / ".claude"
        creds.mkdir(parents=True, exist_ok=True)
        (creds / ".credentials.json").write_text("{}")
    if sqlite3:
        _make_stub(bindir / "sqlite3")
    # lsof: exit 0 + print pid => listener present (port busy); exit 1 => free
    _make_stub(bindir / "lsof", exit_code=0 if lsof_listener else 1,
               stdout="12345" if lsof_listener else "")

    return {
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "HOME": str(home),
    }


# ── script presence / syntax ────────────────────────────────────────────────


def test_script_exists():
    assert SCRIPT.exists(), f"{SCRIPT} missing"


def test_script_is_bash_with_strict_mode():
    text = SCRIPT.read_text()
    assert text.startswith("#!"), "missing shebang"
    assert "set -euo pipefail" in text, "strict mode not enabled"


def test_script_syntax_valid():
    proc = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_help_lists_flags():
    proc = _run(["--help"])
    assert proc.returncode == 0
    out = proc.stdout + proc.stderr
    assert "--restore-gist" in out
    assert "--restore-db" in out
    assert "--doctor" in out


# ── AC1: idempotent ───────────────────────────────────────────────────────────


def test_setup_only_idempotent(tmp_path):
    """AC1: re-running setup produces no errors and no unintended side effects."""
    dash = tmp_path / "dash"
    dash.mkdir()
    (dash / ".env.example").write_text(ENV_EXAMPLE.read_text())
    (dash / ".env").write_text("DB_PATH=./commander.db\n")  # already present

    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    _make_stub(venv / "bin" / "pip")  # venv already present

    proj = tmp_path / "commander"
    (proj / "prd").mkdir(parents=True)
    (proj / "uat").mkdir(parents=True)

    env = {
        "COMMANDER_DASHBOARD_DIR": str(dash),
        "COMMANDER_VENV_DIR": str(venv),
        "COMMANDER_PROJECT_DIR": str(proj),
        "SETUP_MACHINE_SKIP_PIP": "1",
    }
    first = _run(["--setup-only"], env=env)
    second = _run(["--setup-only"], env=env)
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    # everything already present -> skip messages, no creation
    combined = (second.stdout + second.stderr).lower()
    assert "skip" in combined


# ── AC2: venv creation + pip install ──────────────────────────────────────────


def test_venv_created_and_pip_installed_when_absent(tmp_path):
    """AC2: when venv absent, script creates it and pip installs requirements."""
    dash = tmp_path / "dash"
    dash.mkdir()
    (dash / ".env.example").write_text(ENV_EXAMPLE.read_text())
    (dash / ".env").write_text("DB_PATH=./x.db\n")
    env = {
        "COMMANDER_DASHBOARD_DIR": str(dash),
        "COMMANDER_VENV_DIR": str(tmp_path / "venv"),  # absent
        "COMMANDER_PROJECT_DIR": str(tmp_path / "commander"),
        "SETUP_MACHINE_DRY_RUN": "1",
    }
    proc = _run(["--setup-only"], env=env)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout + proc.stderr
    assert "venv" in str(tmp_path / "venv") and "venv" in out
    assert "pip install" in out and "requirements.txt" in out


def test_venv_skipped_when_present(tmp_path):
    """AC2: existing venv is not recreated."""
    dash = tmp_path / "dash"
    dash.mkdir()
    (dash / ".env.example").write_text(ENV_EXAMPLE.read_text())
    (dash / ".env").write_text("DB_PATH=./x.db\n")
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    _make_stub(venv / "bin" / "pip")
    env = {
        "COMMANDER_DASHBOARD_DIR": str(dash),
        "COMMANDER_VENV_DIR": str(venv),
        "COMMANDER_PROJECT_DIR": str(tmp_path / "commander"),
        "SETUP_MACHINE_SKIP_PIP": "1",
    }
    proc = _run(["--setup-only"], env=env)
    assert proc.returncode == 0, proc.stderr
    assert "skip" in (proc.stdout + proc.stderr).lower()


# ── AC3: .env from .env.example, secrets never echoed ─────────────────────────


def test_env_created_from_example_secret_not_echoed(tmp_path):
    """AC3: .env is created from .env.example; a prompted secret never hits stdout."""
    dash = tmp_path / "dash"
    dash.mkdir()
    (dash / ".env.example").write_text(ENV_EXAMPLE.read_text())  # no .env yet
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    _make_stub(venv / "bin" / "pip")
    secret = "ghp_TOPSECRET_value_123456"
    env = {
        "COMMANDER_DASHBOARD_DIR": str(dash),
        "COMMANDER_VENV_DIR": str(venv),
        "COMMANDER_PROJECT_DIR": str(tmp_path / "commander"),
        "SETUP_MACHINE_SKIP_PIP": "1",
    }
    proc = _run(["--setup-only"], env=env, input_text=secret + "\n")
    assert proc.returncode == 0, proc.stderr
    env_file = dash / ".env"
    assert env_file.exists(), ".env not created from .env.example"
    assert f"GH_TOKEN={secret}" in env_file.read_text()
    # the secret value must NEVER be echoed to stdout/stderr
    assert secret not in proc.stdout
    assert secret not in proc.stderr


def test_env_skipped_when_present(tmp_path):
    """AC3: existing .env is left untouched (idempotent)."""
    dash = tmp_path / "dash"
    dash.mkdir()
    (dash / ".env.example").write_text(ENV_EXAMPLE.read_text())
    (dash / ".env").write_text("DB_PATH=./preexisting.db\n")
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    _make_stub(venv / "bin" / "pip")
    env = {
        "COMMANDER_DASHBOARD_DIR": str(dash),
        "COMMANDER_VENV_DIR": str(venv),
        "COMMANDER_PROJECT_DIR": str(tmp_path / "commander"),
        "SETUP_MACHINE_SKIP_PIP": "1",
    }
    proc = _run(["--setup-only"], env=env)
    assert proc.returncode == 0, proc.stderr
    assert (dash / ".env").read_text() == "DB_PATH=./preexisting.db\n"


# ── AC4: prd/uat layout + coder/tester worktrees ──────────────────────────────


def test_layout_creates_prd_and_uat_when_missing(tmp_path):
    """AC4: missing prd/uat clones are constructed."""
    dash = tmp_path / "dash"
    dash.mkdir()
    (dash / ".env.example").write_text(ENV_EXAMPLE.read_text())
    (dash / ".env").write_text("DB_PATH=./x.db\n")
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    _make_stub(venv / "bin" / "pip")
    proj = tmp_path / "commander"
    env = {
        "COMMANDER_DASHBOARD_DIR": str(dash),
        "COMMANDER_VENV_DIR": str(venv),
        "COMMANDER_PROJECT_DIR": str(proj),
        "SETUP_MACHINE_DRY_RUN": "1",
    }
    proc = _run(["--setup-only"], env=env)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout + proc.stderr
    assert f"{proj}/prd" in out
    assert f"{proj}/uat" in out


def test_layout_documents_coder_tester_worktrees(tmp_path):
    """AC4: coder/tester worktrees are documented (or created)."""
    dash = tmp_path / "dash"
    dash.mkdir()
    (dash / ".env.example").write_text(ENV_EXAMPLE.read_text())
    (dash / ".env").write_text("DB_PATH=./x.db\n")
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    _make_stub(venv / "bin" / "pip")
    env = {
        "COMMANDER_DASHBOARD_DIR": str(dash),
        "COMMANDER_VENV_DIR": str(venv),
        "COMMANDER_PROJECT_DIR": str(tmp_path / "commander"),
        "SETUP_MACHINE_DRY_RUN": "1",
    }
    proc = _run(["--setup-only"], env=env)
    out = proc.stdout + proc.stderr
    assert "coder" in out
    assert "tester" in out


def test_layout_skipped_when_present(tmp_path):
    """AC4 + AC1: existing prd/uat clones are skipped."""
    dash = tmp_path / "dash"
    dash.mkdir()
    (dash / ".env.example").write_text(ENV_EXAMPLE.read_text())
    (dash / ".env").write_text("DB_PATH=./x.db\n")
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    _make_stub(venv / "bin" / "pip")
    proj = tmp_path / "commander"
    (proj / "prd").mkdir(parents=True)
    (proj / "uat").mkdir(parents=True)
    env = {
        "COMMANDER_DASHBOARD_DIR": str(dash),
        "COMMANDER_VENV_DIR": str(venv),
        "COMMANDER_PROJECT_DIR": str(proj),
        "SETUP_MACHINE_SKIP_PIP": "1",
    }
    proc = _run(["--setup-only"], env=env)
    assert proc.returncode == 0, proc.stderr
    out = (proc.stdout + proc.stderr).lower()
    assert "skip" in out


# ── AC5: --restore-gist hooks into backup.py ──────────────────────────────────


def test_restore_gist_invokes_backup_module(tmp_path):
    """AC5: --restore-gist dispatches to backup.py restore --gist-id <id>."""
    proc = _run(["--restore-gist", "abc123def"],
                env={"SETUP_MACHINE_DRY_RUN": "1"})
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout + proc.stderr
    assert "services.sprint_manager.backup" in out
    assert "restore" in out
    assert "--gist-id abc123def" in out


# ── AC6: --restore-db hooks into backup.py ────────────────────────────────────


def test_restore_db_invokes_backup_module(tmp_path):
    """AC6: --restore-db dispatches to backup.py restore-db --from <source>."""
    proc = _run(["--restore-db", "/tmp/some/backup/src"],
                env={"SETUP_MACHINE_DRY_RUN": "1"})
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout + proc.stderr
    assert "services.sprint_manager.backup" in out
    assert "restore-db" in out
    assert "--from /tmp/some/backup/src" in out


# ── AC7: doctor PASS/FAIL table ───────────────────────────────────────────────


def test_doctor_table_all_pass(tmp_path):
    """AC7: doctor prints a PASS/FAIL table covering all five checks."""
    env = _doctor_env(tmp_path)
    proc = _run(["--doctor"], env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = proc.stdout + proc.stderr
    for check in ["gh auth status", "claude", "tailscale", "sqlite3"]:
        assert check in out, f"check '{check}' missing from doctor output"
    assert "port" in out.lower()
    assert "PASS" in out
    # all checks pass -> no FAIL rows
    assert "FAIL" not in out


# ── AC8: nonzero exit on any FAIL ─────────────────────────────────────────────


def test_doctor_exits_nonzero_when_check_fails(tmp_path):
    """AC8: a single failing check (tailscale down) makes the script exit nonzero."""
    env = _doctor_env(tmp_path, tailscale=1)
    proc = _run(["--doctor"], env=env)
    assert proc.returncode != 0, "expected nonzero exit when a doctor check FAILs"
    out = proc.stdout + proc.stderr
    assert "FAIL" in out
    assert "tailscale" in out


def test_doctor_passes_exit_zero(tmp_path):
    """AC8 (converse): all checks pass -> exit 0."""
    env = _doctor_env(tmp_path)
    proc = _run(["--doctor"], env=env)
    assert proc.returncode == 0


# ── AC9: launchd reference on launchd-related failure ─────────────────────────


def test_doctor_references_install_launchd_on_port_failure(tmp_path):
    """AC9: a busy port (launchd-related) failure references install_launchd.sh."""
    env = _doctor_env(tmp_path, lsof_listener=True)
    proc = _run(["--doctor"], env=env)
    assert proc.returncode != 0
    out = proc.stdout + proc.stderr
    assert "install_launchd.sh" in out


# ── AC11: interactive logins detected + instructed ────────────────────────────


def test_doctor_instructs_gh_login_when_unauthed(tmp_path):
    """AC11: failed gh auth is reported with a login instruction, not skipped."""
    env = _doctor_env(tmp_path, gh=1)
    proc = _run(["--doctor"], env=env)
    assert proc.returncode != 0
    out = proc.stdout + proc.stderr
    assert "gh auth" in out and "FAIL" in out
    assert "gh auth login" in out


def test_doctor_instructs_claude_login_when_unauthed(tmp_path):
    """AC11: missing/unauthed claude is reported with a login instruction."""
    env = _doctor_env(tmp_path, claude=False)
    proc = _run(["--doctor"], env=env)
    assert proc.returncode != 0
    out = proc.stdout + proc.stderr
    assert "claude" in out and "FAIL" in out


# ── AC10: README "New machine" section ────────────────────────────────────────


def test_readme_new_machine_section():
    """AC10: README documents the one-command new-machine bootstrap."""
    readme = (REPO_ROOT / "README.md").read_text()
    assert "New machine" in readme
    assert "scripts/setup_machine.sh" in readme
