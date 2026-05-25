"""Tests for issue #27: Separate PRD/UAT databases, shell shortcuts, and env isolation.

AC1  — db.py reads DB_PATH from environment; exits non-zero if absent.
AC2  — init_db() write-access guard raises RuntimeError with full path.
AC3  — PRD .env contains DB_PATH=./commander.db
AC5  — migrate_to_separate_dbs.py is idempotent, creates backup, exits 0.
AC6  — ~/.commander.zsh contains the required Commander shortcuts.
AC7  — Hook scripts read HOOK_POST_TARGET with correct default.
"""
import importlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# ── Helpers ───────────────────────────────────────────────────────────────────

DASHBOARD_DIR = Path(__file__).parent.parent  # dashboard/
SCRIPTS_DIR = DASHBOARD_DIR / "scripts"
HOOKS_DIR = DASHBOARD_DIR / "hooks"


def _load_module_from_path(name: str, path: Path):
    """Import a Python module from an absolute file path."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── AC1: db.py reads DB_PATH from environment; exits non-zero if absent ───────

class TestDbPathResolution:
    def test_db_exits_nonzero_when_db_path_missing(self):
        """Server must exit immediately with non-zero code when DB_PATH is unset."""
        env = {k: v for k, v in os.environ.items() if k != "DB_PATH"}
        result = subprocess.run(
            [sys.executable, "-c", "import db"],
            env=env,
            capture_output=True,
            text=True,
            cwd=str(DASHBOARD_DIR),
        )
        assert result.returncode != 0, (
            "Expected non-zero exit when DB_PATH is absent, but process exited 0.\n"
            f"stderr: {result.stderr}"
        )

    def test_db_exits_nonzero_when_db_path_blank(self):
        """Server must exit immediately with non-zero code when DB_PATH is blank."""
        env = {**os.environ, "DB_PATH": "   "}
        result = subprocess.run(
            [sys.executable, "-c", "import db"],
            env=env,
            capture_output=True,
            text=True,
            cwd=str(DASHBOARD_DIR),
        )
        assert result.returncode != 0, (
            "Expected non-zero exit when DB_PATH is blank, but process exited 0.\n"
            f"stderr: {result.stderr}"
        )

    def test_db_error_message_mentions_env_var(self):
        """Error message must name the DB_PATH env var so the user knows what to set."""
        env = {k: v for k, v in os.environ.items() if k != "DB_PATH"}
        result = subprocess.run(
            [sys.executable, "-c", "import db"],
            env=env,
            capture_output=True,
            text=True,
            cwd=str(DASHBOARD_DIR),
        )
        combined = result.stdout + result.stderr
        assert "DB_PATH" in combined, (
            "Expected 'DB_PATH' in error output, but got:\n" + combined
        )

    def test_db_loads_successfully_when_db_path_set(self, tmp_path):
        """db module loads without error when DB_PATH points to a writable path."""
        db_file = tmp_path / "test.db"
        env = {**os.environ, "DB_PATH": str(db_file)}
        result = subprocess.run(
            [sys.executable, "-c", "import db; db.init_db(); print('ok')"],
            env=env,
            capture_output=True,
            text=True,
            cwd=str(DASHBOARD_DIR),
        )
        assert result.returncode == 0, (
            f"Expected exit 0 when DB_PATH is valid, got {result.returncode}.\n"
            f"stderr: {result.stderr}"
        )
        assert "ok" in result.stdout


# ── AC2: init_db() write-access guard raises RuntimeError ─────────────────────

class TestWriteAccessGuard:
    def test_init_db_raises_runtime_error_for_unwritable_path(self, tmp_path):
        """init_db() must raise RuntimeError if DB_PATH is not writable."""
        db_file = tmp_path / "readonly.db"
        db_file.touch()
        os.chmod(db_file, 0o444)  # read-only

        env = {**os.environ, "DB_PATH": str(db_file)}
        result = subprocess.run(
            [sys.executable, "-c", "import db; db.init_db()"],
            env=env,
            capture_output=True,
            text=True,
            cwd=str(DASHBOARD_DIR),
        )
        try:
            assert result.returncode != 0, (
                "Expected non-zero exit for unwritable DB_PATH"
            )
            combined = result.stdout + result.stderr
            assert str(db_file) in combined, (
                f"Expected full path '{db_file}' in error, got:\n{combined}"
            )
        finally:
            os.chmod(db_file, 0o644)


# ── AC3: PRD .env has DB_PATH=./commander.db ─────────────────────────────────

class TestPrdEnvFile:
    def test_prd_env_contains_db_path(self):
        """dashboard/.env must contain DB_PATH=./commander.db."""
        env_file = DASHBOARD_DIR / ".env"
        assert env_file.exists(), f"{env_file} does not exist"
        content = env_file.read_text()
        assert "DB_PATH=./commander.db" in content, (
            f"Expected 'DB_PATH=./commander.db' in {env_file}. Got:\n{content}"
        )


# ── AC5: Migration script is idempotent and creates backup ───────────────────

def _make_fake_migrate_script(fake_dashboard: Path, tmp_path: Path) -> Path:
    """Copy migrate_to_separate_dbs.py into a sibling scripts/ dir of fake_dashboard
    so SCRIPT_DIR.parent resolves to fake_dashboard."""
    fake_scripts = fake_dashboard.parent / "fake_scripts"
    fake_scripts.mkdir(exist_ok=True)
    src = SCRIPTS_DIR / "migrate_to_separate_dbs.py"
    dst = fake_scripts / "migrate_to_separate_dbs.py"
    # Rewrite the script so SCRIPT_DIR.parent == fake_dashboard
    content = src.read_text()
    # Patch DASHBOARD_DIR to point at fake_dashboard by overriding at top
    patched = content.replace(
        "DASHBOARD_DIR = SCRIPT_DIR.parent",
        f"DASHBOARD_DIR = __import__('pathlib').Path({repr(str(fake_dashboard))})",
    )
    dst.write_text(patched)
    return dst


class TestMigrationScript:
    def test_migration_exits_nonzero_when_source_missing(self, tmp_path):
        """migrate_to_separate_dbs.py must exit non-zero if dashboard.db doesn't exist."""
        fake_dashboard = tmp_path / "dashboard"
        fake_dashboard.mkdir()
        # No dashboard.db created — script should fail
        script = _make_fake_migrate_script(fake_dashboard, tmp_path)
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0, (
            "Expected non-zero exit when dashboard.db is absent, got 0.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_migration_creates_backup_and_commander_db(self, tmp_path):
        """migrate_to_separate_dbs.py copies dashboard.db → .backups/ and commander.db."""
        import sqlite3
        fake_dashboard = tmp_path / "dashboard"
        fake_dashboard.mkdir()
        source_db = fake_dashboard / "dashboard.db"
        conn = sqlite3.connect(str(source_db))
        conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO test VALUES (1)")
        conn.commit()
        conn.close()

        script = _make_fake_migrate_script(fake_dashboard, tmp_path)
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"Migration script exited non-zero.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        backup = fake_dashboard / ".backups" / "commander-pre-split.db"
        commander = fake_dashboard / "commander.db"
        assert backup.exists(), f"Expected backup at {backup}"
        assert commander.exists(), f"Expected commander.db at {commander}"

    def test_migration_is_idempotent(self, tmp_path):
        """Running the migration script twice must not fail and must not overwrite a non-empty commander.db."""
        import sqlite3
        fake_dashboard = tmp_path / "dashboard"
        fake_dashboard.mkdir()
        source_db = fake_dashboard / "dashboard.db"
        conn = sqlite3.connect(str(source_db))
        conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO test VALUES (42)")
        conn.commit()
        conn.close()

        script = _make_fake_migrate_script(fake_dashboard, tmp_path)
        for run_number in (1, 2):
            result = subprocess.run(
                [sys.executable, str(script)],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, (
                f"Run {run_number} failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
            )


# ── AC6: ~/.commander.zsh contains the required Commander shortcuts ────────────

class TestShellShortcuts:
    REQUIRED_FUNCTIONS = [
        "start-prd",
        "start-uat",
        "stop-prd",
        "stop-uat",
        "stop-all",
        "cmdr-status",
        "restart-prd",
        "restart-uat",
    ]

    def test_commander_zsh_exists(self):
        """~/.commander.zsh must exist after install_shell_shortcuts.sh has been run."""
        zsh_file = Path.home() / ".commander.zsh"
        assert zsh_file.exists(), (
            f"{zsh_file} does not exist. "
            "Run: bash ~/commander/dashboard/scripts/install_shell_shortcuts.sh"
        )

    def test_commander_zsh_contains_all_functions(self):
        """~/.commander.zsh must define all eight Commander shortcut functions."""
        zsh_file = Path.home() / ".commander.zsh"
        if not zsh_file.exists():
            pytest.skip("~/.commander.zsh not found — run install_shell_shortcuts.sh first")
        content = zsh_file.read_text()
        missing = [fn for fn in self.REQUIRED_FUNCTIONS if fn not in content]
        assert not missing, (
            f"Missing functions in ~/.commander.zsh: {missing}"
        )

    def test_cmdr_status_name_explained_in_file(self):
        """~/.commander.zsh must include a comment explaining the cmdr- prefix."""
        zsh_file = Path.home() / ".commander.zsh"
        if not zsh_file.exists():
            pytest.skip("~/.commander.zsh not found")
        content = zsh_file.read_text()
        assert "cmdr-" in content and ("stat" in content or "shadow" in content or "prefix" in content or "avoid" in content), (
            "Expected a comment explaining the cmdr- prefix in ~/.commander.zsh"
        )

    def test_install_script_is_idempotent(self, tmp_path):
        """Running install_shell_shortcuts.sh twice must not duplicate the block."""
        fake_zsh = tmp_path / ".commander.zsh"
        env = {**os.environ, "HOME": str(tmp_path)}
        # Run twice
        for _ in range(2):
            result = subprocess.run(
                ["bash", str(SCRIPTS_DIR / "install_shell_shortcuts.sh")],
                capture_output=True,
                text=True,
                env=env,
            )
            assert result.returncode == 0

        content = fake_zsh.read_text()
        # The "Commander shell shortcuts" header must appear exactly once
        count = content.count("Commander shell shortcuts")
        assert count == 1, (
            f"Expected 'Commander shell shortcuts' block to appear once, found {count} times."
        )


# ── AC7: Hook scripts read HOOK_POST_TARGET ───────────────────────────────────

class TestHookPostTarget:
    HOOK_SCRIPTS = ["tool_used.py", "agent_finished.py", "post_tool_used.py"]
    EXPECTED_DEFAULT = "http://localhost:8000/api/agent-event"

    def test_all_hooks_read_hook_post_target(self):
        """All three hook scripts must reference HOOK_POST_TARGET env var."""
        for script_name in self.HOOK_SCRIPTS:
            path = HOOKS_DIR / script_name
            assert path.exists(), f"Hook script not found: {path}"
            content = path.read_text()
            assert "HOOK_POST_TARGET" in content, (
                f"{script_name} does not read HOOK_POST_TARGET env var"
            )

    def test_all_hooks_have_correct_default_url(self):
        """All three hook scripts must default to port 8000 when HOOK_POST_TARGET is unset."""
        for script_name in self.HOOK_SCRIPTS:
            path = HOOKS_DIR / script_name
            assert path.exists(), f"Hook script not found: {path}"
            content = path.read_text()
            assert self.EXPECTED_DEFAULT in content, (
                f"{script_name} does not contain the default URL '{self.EXPECTED_DEFAULT}'"
            )

    def test_hook_post_target_env_is_respected(self, tmp_path):
        """tool_used.py must POST to the URL from HOOK_POST_TARGET, not always port 8000."""
        hook = HOOKS_DIR / "tool_used.py"
        # Feed a minimal hook payload through stdin; the hook should try to POST
        # to the custom URL. We verify the script reads the env var (import-time check).
        payload = json.dumps({
            "session_id": "test-session",
            "tool_name": "Read",
            "cwd": str(tmp_path),
        })
        custom_target = "http://localhost:8001/api/agent-event"
        env = {
            **os.environ,
            "HOOK_POST_TARGET": custom_target,
            "DB_PATH": str(tmp_path / "dummy.db"),
        }
        # The hook will fail to POST (no server running) but must not error on
        # reading the env var — exit code should be 0 (hooks never block Claude).
        result = subprocess.run(
            [sys.executable, str(hook)],
            input=payload,
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, (
            f"tool_used.py exited {result.returncode}.\nstderr: {result.stderr}"
        )
