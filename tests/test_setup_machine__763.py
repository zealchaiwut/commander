"""Tester AC verification for issue #763 — setup_machine.sh bootstrap + doctor.

This feature is a bash bootstrap script with no HTTP surface, so it is verified
by invoking the script directly via subprocess (dry-run + stubbed PATH + path
overrides), not against the UAT dashboard server. One test per acceptance
criterion. The clean-macOS-account boot (AC end-to-end on a bare account) is a
manual UAT step and is marked manual in the report.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / "scripts" / "setup_machine.sh"
ENV_EXAMPLE = REPO_ROOT / "apps" / "dashboard" / ".env.example"


def _run(args, env=None, input_text=None):
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.run(
        ["bash", str(SCRIPT)] + args,
        capture_output=True, text=True, env=full_env, input=input_text,
    )


def _stub(path: Path, exit_code: int = 0, stdout: str = ""):
    body = "#!/usr/bin/env bash\n"
    if stdout:
        body += f"printf '%s\\n' {stdout!r}\n"
    body += f"exit {exit_code}\n"
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _base_env(tmp_path, *, env_present=True, dry_run=False):
    """A dashboard dir + present venv + project dir override bundle."""
    dash = tmp_path / "dash"
    dash.mkdir(exist_ok=True)
    (dash / ".env.example").write_text(ENV_EXAMPLE.read_text())
    if env_present:
        (dash / ".env").write_text("DB_PATH=./x.db\n")
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True, exist_ok=True)
    _stub(venv / "bin" / "pip")
    env = {
        "COMMANDER_DASHBOARD_DIR": str(dash),
        "COMMANDER_VENV_DIR": str(venv),
        "COMMANDER_PROJECT_DIR": str(tmp_path / "commander"),
    }
    if dry_run:
        env["SETUP_MACHINE_DRY_RUN"] = "1"
    else:
        env["SETUP_MACHINE_SKIP_PIP"] = "1"
    return env, dash


def _doctor_env(tmp_path, *, gh=0, claude=True, tailscale=0, sqlite3=True,
                lsof_listener=False):
    bindir = tmp_path / "bin"; bindir.mkdir(exist_ok=True)
    home = tmp_path / "home"; home.mkdir(exist_ok=True)
    _stub(bindir / "gh", exit_code=gh)
    _stub(bindir / "tailscale", exit_code=tailscale)
    if claude:
        _stub(bindir / "claude")
        creds = home / ".claude"; creds.mkdir(parents=True, exist_ok=True)
        (creds / ".credentials.json").write_text("{}")
    if sqlite3:
        _stub(bindir / "sqlite3")
    _stub(bindir / "lsof", exit_code=0 if lsof_listener else 1,
          stdout="12345" if lsof_listener else "")
    return {"PATH": f"{bindir}:{os.environ['PATH']}", "HOME": str(home)}


# AC1 — idempotent: re-run = no errors, no unintended side effects
def test_setup_machine__idempotent_rerun(tmp_path):
    env, _ = _base_env(tmp_path)
    proj = tmp_path / "commander"
    (proj / "prd").mkdir(parents=True); (proj / "uat").mkdir(parents=True)
    first = _run(["--setup-only"], env=env)
    second = _run(["--setup-only"], env=env)
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert "skip" in (second.stdout + second.stderr).lower()


# AC2 — creates venv + pip install when venv absent
def test_setup_machine__venv_created_and_pip_installed(tmp_path):
    env, _ = _base_env(tmp_path, dry_run=True)
    env["COMMANDER_VENV_DIR"] = str(tmp_path / "absent-venv")
    proc = _run(["--setup-only"], env=env)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout + proc.stderr
    assert "pip install" in out and "requirements.txt" in out


# AC3 — .env from .env.example, prompted secret never echoed
def test_setup_machine__env_from_example_secret_hidden(tmp_path):
    env, dash = _base_env(tmp_path, env_present=False)
    secret = "ghp_TOPSECRET_value_123456"
    proc = _run(["--setup-only"], env=env, input_text=secret + "\n")
    assert proc.returncode == 0, proc.stderr
    assert (dash / ".env").exists()
    assert f"GH_TOKEN={secret}" in (dash / ".env").read_text()
    assert secret not in proc.stdout and secret not in proc.stderr


# AC4 — constructs prd/uat layout; documents coder/tester worktrees
def test_setup_machine__prd_uat_layout_and_worktrees(tmp_path):
    env, _ = _base_env(tmp_path, dry_run=True)
    proj = str(tmp_path / "commander")
    proc = _run(["--setup-only"], env=env)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, proc.stderr
    assert f"{proj}/prd" in out and f"{proj}/uat" in out
    assert "coder" in out and "tester" in out


# AC5 — --restore-gist hooks into backup.py config-restore path
def test_setup_machine__restore_gist_hooks_backup(tmp_path):
    proc = _run(["--restore-gist", "abc123def"], env={"SETUP_MACHINE_DRY_RUN": "1"})
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout + proc.stderr
    assert "services.sprint_manager.backup" in out
    assert "restore" in out and "--gist-id abc123def" in out


# AC6 — --restore-db hooks into backup.py db-restore path
def test_setup_machine__restore_db_hooks_backup(tmp_path):
    proc = _run(["--restore-db", "/tmp/src"], env={"SETUP_MACHINE_DRY_RUN": "1"})
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout + proc.stderr
    assert "services.sprint_manager.backup" in out
    assert "restore-db" in out and "--from /tmp/src" in out


# AC7 — doctor prints PASS/FAIL table for all five checks
def test_setup_machine__doctor_table_all_checks(tmp_path):
    proc = _run(["--doctor"], env=_doctor_env(tmp_path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = proc.stdout + proc.stderr
    for check in ["gh auth status", "claude", "tailscale", "sqlite3"]:
        assert check in out, f"missing check: {check}"
    assert "port" in out.lower() and "PASS" in out


# AC8 — exits nonzero if any doctor check FAILs
def test_setup_machine__doctor_nonzero_on_fail(tmp_path):
    proc = _run(["--doctor"], env=_doctor_env(tmp_path, tailscale=1))
    assert proc.returncode != 0
    assert "FAIL" in (proc.stdout + proc.stderr)


# AC9 — doctor references install_launchd.sh on launchd-related (port) failure
def test_setup_machine__doctor_references_install_launchd(tmp_path):
    proc = _run(["--doctor"], env=_doctor_env(tmp_path, lsof_listener=True))
    assert proc.returncode != 0
    assert "install_launchd.sh" in (proc.stdout + proc.stderr)


# AC10 — README "New machine" section reduced to clone -> setup_machine.sh
def test_setup_machine__readme_new_machine_section():
    readme = (REPO_ROOT / "README.md").read_text()
    assert "New machine" in readme
    assert "scripts/setup_machine.sh" in readme


# AC11 — interactive gh/claude logins detected + instructed, not silently skipped
def test_setup_machine__login_instructed_not_skipped(tmp_path):
    proc = _run(["--doctor"], env=_doctor_env(tmp_path, gh=1))
    assert proc.returncode != 0
    out = proc.stdout + proc.stderr
    assert "gh auth" in out and "FAIL" in out and "gh auth login" in out


# AC end-to-end on a clean macOS account — not HTTP/subprocess-testable here
def test_setup_machine__clean_account_boot_manual():
    pytest.skip("manual — clean macOS account end-to-end boot is a UAT step")
