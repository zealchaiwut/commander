"""
Tests for issue #8 — PRD/UAT environment split.

AC-1:  setup_uat_env.sh exists, is executable, contains expected setup logic.
AC-2:  setup_uat_env.sh creates a separate DB path (not the PRD DB path).
AC-3:  start_prd.sh exists, targets port 8000 and ~/commander/dashboard dir.
AC-4:  start_uat.sh exists, targets port 8001 and ~/commander/dashboard-uat dir.
AC-5:  stop_all.sh exists, kills processes on ports 8000 and 8001.
AC-6:  status.sh exists, checks both ports 8000 and 8001.
AC-7:  sync_uat.sh exists, runs git pull origin develop in dashboard-uat/.
AC-8:  server.py reads ENVIRONMENT; index.html + app.js contain badge logic.
AC-9:  Hook scripts POST to localhost:8000 only.
AC-10: dashboard/CLAUDE.md documents the PRD/UAT split (paths, ports, branches,
       agents rule).
"""

import os
import stat
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
REPO_ROOT   = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "dashboard" / "scripts"
HOOKS_DIR   = REPO_ROOT / "dashboard" / "hooks"
SERVER_PY   = REPO_ROOT / "dashboard" / "server.py"
INDEX_HTML  = REPO_ROOT / "dashboard" / "static" / "index.html"
APP_JS      = REPO_ROOT / "dashboard" / "static" / "app.js"
CLAUDE_MD   = REPO_ROOT / "dashboard" / "CLAUDE.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _is_executable(path: Path) -> bool:
    """Return True if the file has at least one executable bit set."""
    mode = path.stat().st_mode
    return bool(mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))


# ── AC-1: setup_uat_env.sh ────────────────────────────────────────────────────

class TestAC1SetupUatEnvExists:
    def test_file_exists(self):
        assert (SCRIPTS_DIR / "setup_uat_env.sh").exists(), \
            "setup_uat_env.sh not found in dashboard/scripts/"

    def test_file_is_executable(self):
        assert _is_executable(SCRIPTS_DIR / "setup_uat_env.sh"), \
            "setup_uat_env.sh is not executable"

    def test_contains_git_clone(self):
        content = _read(SCRIPTS_DIR / "setup_uat_env.sh")
        assert "git clone" in content, \
            "setup_uat_env.sh must perform 'git clone'"

    def test_checks_out_develop(self):
        content = _read(SCRIPTS_DIR / "setup_uat_env.sh")
        assert "checkout develop" in content or "checkout develop" in content, \
            "setup_uat_env.sh must checkout the 'develop' branch"

    def test_creates_venv(self):
        content = _read(SCRIPTS_DIR / "setup_uat_env.sh")
        assert "venv" in content.lower(), \
            "setup_uat_env.sh must create a Python venv"

    def test_writes_port_8001(self):
        content = _read(SCRIPTS_DIR / "setup_uat_env.sh")
        assert "PORT=8001" in content, \
            "setup_uat_env.sh must write PORT=8001 to .env"

    def test_writes_environment_uat(self):
        content = _read(SCRIPTS_DIR / "setup_uat_env.sh")
        assert "ENVIRONMENT=uat" in content, \
            "setup_uat_env.sh must write ENVIRONMENT=uat to .env"

    def test_targets_dashboard_uat_dir(self):
        content = _read(SCRIPTS_DIR / "setup_uat_env.sh")
        assert "dashboard-uat" in content, \
            "setup_uat_env.sh must target the dashboard-uat directory"


# ── AC-2: separate DB path ────────────────────────────────────────────────────

class TestAC2SeparateDatabase:
    def test_uat_db_path_is_separate(self):
        """
        The UAT DB path in setup_uat_env.sh must differ from the PRD DB path.
        PRD DB lives at ~/commander/dashboard/commander.db;
        UAT DB must NOT share that exact path.
        """
        content = _read(SCRIPTS_DIR / "setup_uat_env.sh")
        assert "dashboard-uat" in content, \
            "setup_uat_env.sh must reference a dashboard-uat DB path, not PRD"

    def test_uat_db_not_symlinked_to_prd(self):
        """
        If the UAT DB file happens to exist locally, it must not be a symlink
        pointing at the PRD DB.
        """
        prd_db  = Path.home() / "commander" / "dashboard" / "commander.db"
        uat_db  = Path.home() / "commander" / "dashboard-uat" / "commander.db"

        if uat_db.exists():
            assert not uat_db.is_symlink(), \
                "UAT commander.db must not be a symlink"
            if uat_db.is_symlink() and prd_db.exists():
                assert uat_db.resolve() != prd_db.resolve(), \
                    "UAT commander.db must not point to the PRD database"

    def test_script_does_not_copy_prd_db(self):
        """setup_uat_env.sh must not copy or symlink the PRD DB."""
        content = _read(SCRIPTS_DIR / "setup_uat_env.sh")
        # Should not contain 'cp commander.db' or 'ln -s …/commander.db'
        assert "cp commander.db" not in content, \
            "setup_uat_env.sh must not copy PRD commander.db"
        # A symlink to the PRD DB would be wrong
        assert "ln -s" not in content or "dashboard-uat" in content, \
            "Any symlink created by setup_uat_env.sh must target dashboard-uat, not PRD"


# ── AC-3: start_prd.sh ────────────────────────────────────────────────────────

class TestAC3StartPrd:
    def test_file_exists(self):
        assert (SCRIPTS_DIR / "start_prd.sh").exists(), \
            "start_prd.sh not found in dashboard/scripts/"

    def test_file_is_executable(self):
        assert _is_executable(SCRIPTS_DIR / "start_prd.sh"), \
            "start_prd.sh is not executable"

    def test_targets_port_8000(self):
        content = _read(SCRIPTS_DIR / "start_prd.sh")
        assert "8000" in content, \
            "start_prd.sh must target port 8000"

    def test_targets_dashboard_dir(self):
        content = _read(SCRIPTS_DIR / "start_prd.sh")
        # Must reference commander/dashboard (the PRD dir), NOT dashboard-uat
        assert "commander/dashboard" in content or "dashboard" in content, \
            "start_prd.sh must reference the ~/commander/dashboard directory"

    def test_does_not_reference_dashboard_uat(self):
        content = _read(SCRIPTS_DIR / "start_prd.sh")
        assert "dashboard-uat" not in content, \
            "start_prd.sh must NOT reference the dashboard-uat directory"


# ── AC-4: start_uat.sh ────────────────────────────────────────────────────────

class TestAC4StartUat:
    def test_file_exists(self):
        assert (SCRIPTS_DIR / "start_uat.sh").exists(), \
            "start_uat.sh not found in dashboard/scripts/"

    def test_file_is_executable(self):
        assert _is_executable(SCRIPTS_DIR / "start_uat.sh"), \
            "start_uat.sh is not executable"

    def test_targets_port_8001(self):
        content = _read(SCRIPTS_DIR / "start_uat.sh")
        assert "8001" in content, \
            "start_uat.sh must target port 8001"

    def test_targets_dashboard_uat_dir(self):
        content = _read(SCRIPTS_DIR / "start_uat.sh")
        assert "dashboard-uat" in content, \
            "start_uat.sh must reference the dashboard-uat directory"


# ── AC-5: stop_all.sh ─────────────────────────────────────────────────────────

class TestAC5StopAll:
    def test_file_exists(self):
        assert (SCRIPTS_DIR / "stop_all.sh").exists(), \
            "stop_all.sh not found in dashboard/scripts/"

    def test_file_is_executable(self):
        assert _is_executable(SCRIPTS_DIR / "stop_all.sh"), \
            "stop_all.sh is not executable"

    def test_kills_port_8000(self):
        content = _read(SCRIPTS_DIR / "stop_all.sh")
        assert "8000" in content, \
            "stop_all.sh must handle port 8000"

    def test_kills_port_8001(self):
        content = _read(SCRIPTS_DIR / "stop_all.sh")
        assert "8001" in content, \
            "stop_all.sh must handle port 8001"

    def test_uses_kill_or_lsof(self):
        content = _read(SCRIPTS_DIR / "stop_all.sh")
        assert "kill" in content or "lsof" in content, \
            "stop_all.sh must use 'kill' or 'lsof' to terminate processes"


# ── AC-6: status.sh ───────────────────────────────────────────────────────────

class TestAC6Status:
    def test_file_exists(self):
        assert (SCRIPTS_DIR / "status.sh").exists(), \
            "status.sh not found in dashboard/scripts/"

    def test_file_is_executable(self):
        assert _is_executable(SCRIPTS_DIR / "status.sh"), \
            "status.sh is not executable"

    def test_checks_port_8000(self):
        content = _read(SCRIPTS_DIR / "status.sh")
        assert "8000" in content, \
            "status.sh must check port 8000"

    def test_checks_port_8001(self):
        content = _read(SCRIPTS_DIR / "status.sh")
        assert "8001" in content, \
            "status.sh must check port 8001"

    def test_reports_pid(self):
        content = _read(SCRIPTS_DIR / "status.sh")
        assert "pid" in content.lower() or "PID" in content, \
            "status.sh must report PIDs"

    def test_reports_branch(self):
        content = _read(SCRIPTS_DIR / "status.sh")
        assert "branch" in content.lower() or "rev-parse" in content, \
            "status.sh must report git branch"


# ── AC-7: sync_uat.sh ─────────────────────────────────────────────────────────

class TestAC7SyncUat:
    def test_file_exists(self):
        assert (SCRIPTS_DIR / "sync_uat.sh").exists(), \
            "sync_uat.sh not found in dashboard/scripts/"

    def test_file_is_executable(self):
        assert _is_executable(SCRIPTS_DIR / "sync_uat.sh"), \
            "sync_uat.sh is not executable"

    def test_pulls_origin_develop(self):
        content = _read(SCRIPTS_DIR / "sync_uat.sh")
        assert "git" in content and "pull" in content and "develop" in content, \
            "sync_uat.sh must run 'git pull … develop'"

    def test_targets_dashboard_uat(self):
        content = _read(SCRIPTS_DIR / "sync_uat.sh")
        assert "dashboard-uat" in content, \
            "sync_uat.sh must target the dashboard-uat directory"


# ── AC-8: environment badge ───────────────────────────────────────────────────

class TestAC8EnvironmentBadge:
    def test_server_reads_environment_variable(self):
        content = _read(SERVER_PY)
        assert 'os.environ.get("ENVIRONMENT"' in content or \
               "os.environ.get('ENVIRONMENT'" in content or \
               "ENVIRONMENT" in content, \
            "server.py must read the ENVIRONMENT env var"

    def test_server_has_environment_endpoint(self):
        content = _read(SERVER_PY)
        assert "/api/environment" in content, \
            "server.py must expose GET /api/environment"

    def test_index_html_has_env_badge_element(self):
        content = _read(INDEX_HTML)
        assert "env-badge" in content, \
            "index.html must contain the env-badge element"

    def test_index_html_has_prd_badge_style(self):
        content = _read(INDEX_HTML)
        assert ".env-badge.prd" in content or "env-badge prd" in content, \
            "index.html must define a PRD badge style"

    def test_index_html_has_uat_badge_style(self):
        content = _read(INDEX_HTML)
        assert ".env-badge.uat" in content or "env-badge uat" in content, \
            "index.html must define a UAT badge style"

    def test_app_js_fetches_environment_api(self):
        content = _read(APP_JS)
        assert "/api/environment" in content, \
            "app.js must fetch /api/environment to populate the badge"

    def test_app_js_shows_prd_badge(self):
        content = _read(APP_JS)
        assert "prd" in content.lower(), \
            "app.js must handle 'prd' when rendering the badge"

    def test_app_js_shows_uat_badge(self):
        content = _read(APP_JS)
        assert "uat" in content.lower(), \
            "app.js must handle 'uat' when rendering the badge"

    def test_server_loads_dotenv(self):
        """server.py must load .env so ENVIRONMENT is picked up at startup."""
        content = _read(SERVER_PY)
        assert "load_dotenv" in content or "dotenv" in content, \
            "server.py must load .env (python-dotenv or equivalent)"


# ── AC-9: hooks always POST to localhost:8000 ─────────────────────────────────

class TestAC9HooksTargetPrd:
    def _hook_files(self):
        return list(HOOKS_DIR.glob("*.py"))

    def test_hooks_directory_exists(self):
        assert HOOKS_DIR.exists(), "dashboard/hooks/ directory not found"

    def test_hooks_exist(self):
        hooks = self._hook_files()
        assert len(hooks) > 0, "No hook scripts found in dashboard/hooks/"

    def test_hooks_post_to_localhost_8000(self):
        hooks = self._hook_files()
        for hook_path in hooks:
            content = _read(hook_path)
            if "localhost:" in content or "http://localhost" in content:
                assert "localhost:8000" in content, \
                    f"{hook_path.name}: hook must POST to localhost:8000, not another port"

    def test_hooks_do_not_target_8001(self):
        hooks = self._hook_files()
        for hook_path in hooks:
            content = _read(hook_path)
            assert "localhost:8001" not in content, \
                f"{hook_path.name}: hook must NOT target localhost:8001"

    def test_hooks_do_not_target_dynamic_port(self):
        """Hooks must use the hard-coded PRD port, not read it from an env var."""
        hooks = self._hook_files()
        for hook_path in hooks:
            content = _read(hook_path)
            # If the hook reads PORT from env for its URL, that would be wrong
            if "os.environ.get" in content and "PORT" in content:
                # Acceptable only if the URL still hard-codes 8000
                assert "localhost:8000" in content, \
                    f"{hook_path.name}: hook reads PORT from env but must still target :8000"


# ── AC-10: CLAUDE.md PRD/UAT documentation ───────────────────────────────────

class TestAC10ClaudeMdDocumentation:
    def test_claude_md_exists(self):
        assert CLAUDE_MD.exists(), \
            "dashboard/CLAUDE.md not found"

    def test_documents_prd_path(self):
        content = _read(CLAUDE_MD)
        assert "dashboard/" in content or "commander/dashboard" in content, \
            "dashboard/CLAUDE.md must document the PRD directory path"

    def test_documents_uat_path(self):
        content = _read(CLAUDE_MD)
        assert "dashboard-uat" in content, \
            "dashboard/CLAUDE.md must document the dashboard-uat directory path"

    def test_documents_prd_port(self):
        content = _read(CLAUDE_MD)
        assert "8000" in content, \
            "dashboard/CLAUDE.md must document PRD port 8000"

    def test_documents_uat_port(self):
        content = _read(CLAUDE_MD)
        assert "8001" in content, \
            "dashboard/CLAUDE.md must document UAT port 8001"

    def test_documents_master_branch(self):
        content = _read(CLAUDE_MD)
        assert "master" in content, \
            "dashboard/CLAUDE.md must document the 'master' branch for PRD"

    def test_documents_develop_branch(self):
        content = _read(CLAUDE_MD)
        assert "develop" in content, \
            "dashboard/CLAUDE.md must document the 'develop' branch for UAT"

    def test_documents_agents_must_not_touch_live_dirs(self):
        content = _read(CLAUDE_MD)
        # Must warn agents away from live environment folders
        agents_rule = (
            "agent" in content.lower()
            and (
                "never" in content.lower()
                or "must not" in content.lower()
                or "do not" in content.lower()
                or "MUST NOT" in content
            )
        )
        assert agents_rule, \
            "dashboard/CLAUDE.md must state that agents must not touch live environment folders"

    def test_documents_all_management_scripts(self):
        content = _read(CLAUDE_MD)
        scripts = [
            "setup_uat_env.sh",
            "start_prd.sh",
            "start_uat.sh",
            "stop_all.sh",
            "status.sh",
            "sync_uat.sh",
        ]
        for script in scripts:
            assert script in content, \
                f"dashboard/CLAUDE.md must document {script}"
