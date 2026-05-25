"""Tests for issue #66: init_project.py should also create UAT clone for new projects.

AC1 — init_project.py creates 4 clones: main, uat, coder, tester
AC2 — UAT clone is checked out on develop branch
AC3 — UAT has a separate database from PRD (never shared)
AC4 — UAT port is auto-detected (distinct from PRD port)
AC5 — sprint.yaml includes a uat: config section (enabled, auto_sync, db_path)
AC6 — --skip-uat flag skips UAT clone creation and sets uat.enabled: false
AC7 — Rollback (--rollback) removes uat/ folder cleanly
AC8 — Existing projects can opt-in via migration script (migrate_add_uat.py)
"""
import socket
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import init_project  # noqa: E402
import migrate_add_uat  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_fake_repo_dir(tmp_path: Path, repo_name: str) -> Path:
    """Create a minimal fake project directory that looks like a cloned repo."""
    repo_dir = tmp_path / repo_name
    repo_dir.mkdir(parents=True)
    (repo_dir / ".git").mkdir()
    (repo_dir / "README.md").write_text(f"# {repo_name}\n")
    (repo_dir / ".gitignore").write_text("*.pyc\n")
    commander_dir = repo_dir / ".commander"
    commander_dir.mkdir()
    for sub in ("logs", "sprints", "alerts"):
        (commander_dir / sub).mkdir()
        (commander_dir / sub / ".gitkeep").touch()
    return repo_dir


def _write_sprint_yaml(repo_dir: Path, extra: str = "") -> Path:
    """Write a minimal sprint.yaml for testing."""
    content = (
        "repo_name: owner/testrepo\n"
        "worktrees:\n"
        "  coder: /dev/testrepo-coder\n"
        "  tester: /dev/testrepo-tester\n"
        "  tester_app_subdir: \"\"\n"
    ) + extra
    sprint_yaml = repo_dir / ".commander" / "sprint.yaml"
    sprint_yaml.write_text(content)
    return sprint_yaml


# ─────────────────────────────────────────────────────────────────────────────
# AC1 — four clones created: main, uat, coder, tester
# ─────────────────────────────────────────────────────────────────────────────


class TestAC1FourClones:
    """AC1: init_project creates main, uat, coder, and tester clones."""

    def test_step4b_creates_uat_dir(self, tmp_path):
        """step4b_uat_clone creates ~/dev/<project>/uat/ when it does not exist."""
        repo_dir = _make_fake_repo_dir(tmp_path, "myapp")
        uat_dir = repo_dir / "uat"

        with patch.object(init_project, "_run") as mock_run, \
             patch.object(init_project, "_try_run", return_value=(True, "develop", "")) as mock_try:
            # git clone call creates the directory in reality; simulate by creating it
            def fake_run(*args, **kwargs):
                if "clone" in args:
                    uat_dir.mkdir(parents=True, exist_ok=True)
                return MagicMock(returncode=0)
            mock_run.side_effect = fake_run

            result = init_project.step4b_uat_clone("owner", "myapp", repo_dir, uat_port=8001)

        assert result == uat_dir
        assert uat_dir.exists()

    def test_step4b_skipped_when_uat_already_exists(self, tmp_path, capsys):
        """step4b_uat_clone skips clone when uat/ already exists (idempotent)."""
        repo_dir = _make_fake_repo_dir(tmp_path, "myapp")
        uat_dir = repo_dir / "uat"
        uat_dir.mkdir()

        with patch.object(init_project, "_run") as mock_run:
            result = init_project.step4b_uat_clone("owner", "myapp", repo_dir, uat_port=8001)

        mock_run.assert_not_called()
        assert result == uat_dir
        captured = capsys.readouterr()
        assert "already exists" in captured.out


# ─────────────────────────────────────────────────────────────────────────────
# AC2 — UAT clone checked out on develop
# ─────────────────────────────────────────────────────────────────────────────


class TestAC2DevelopBranch:
    """AC2: UAT clone is checked out on the develop branch."""

    def test_step4b_checks_out_develop(self, tmp_path):
        """step4b_uat_clone runs 'git checkout develop' after cloning."""
        repo_dir = _make_fake_repo_dir(tmp_path, "myapp")
        uat_dir = repo_dir / "uat"
        checkout_called_with = []

        def fake_run(*args, **kwargs):
            if "clone" in args:
                uat_dir.mkdir(parents=True, exist_ok=True)
            return MagicMock(returncode=0)

        def fake_try_run(*args, **kwargs):
            checkout_called_with.append(args)
            return True, "develop", ""

        with patch.object(init_project, "_run", side_effect=fake_run), \
             patch.object(init_project, "_try_run", side_effect=fake_try_run):
            init_project.step4b_uat_clone("owner", "myapp", repo_dir, uat_port=8001)

        # Verify checkout develop was called
        assert any("checkout" in args and "develop" in args for args in checkout_called_with), \
            "Expected 'git checkout develop' to be called"

    def test_step4b_fetches_and_tracks_when_checkout_fails(self, tmp_path):
        """step4b_uat_clone fetches and tracks origin/develop if plain checkout fails."""
        repo_dir = _make_fake_repo_dir(tmp_path, "myapp")
        uat_dir = repo_dir / "uat"
        run_calls = []
        try_calls = []
        call_count = [0]

        def fake_run(*args, **kwargs):
            if "clone" in args:
                uat_dir.mkdir(parents=True, exist_ok=True)
            run_calls.append(args)
            return MagicMock(returncode=0)

        def fake_try_run(*args, **kwargs):
            call_count[0] += 1
            if "checkout" in args and "develop" in args and call_count[0] == 1:
                return False, "", "pathspec 'develop' did not match"
            try_calls.append(args)
            return True, "", ""

        with patch.object(init_project, "_run", side_effect=fake_run), \
             patch.object(init_project, "_try_run", side_effect=fake_try_run):
            init_project.step4b_uat_clone("owner", "myapp", repo_dir, uat_port=8001)

        # fetch + track should have been called
        fetch_calls = [c for c in run_calls if "fetch" in c]
        assert fetch_calls, "Expected 'git fetch origin' to be called after checkout failure"


# ─────────────────────────────────────────────────────────────────────────────
# AC3 — UAT has a separate database from PRD (never shared)
# ─────────────────────────────────────────────────────────────────────────────


class TestAC3SeparateDatabase:
    """AC3: UAT uses a distinct database file, never the same as PRD."""

    def test_step4b_writes_env_with_separate_db(self, tmp_path):
        """step4b_uat_clone writes .env with a UAT-specific DB_PATH."""
        repo_dir = _make_fake_repo_dir(tmp_path, "myapp")
        uat_dir = repo_dir / "uat"

        with patch.object(init_project, "_run") as mock_run, \
             patch.object(init_project, "_try_run", return_value=(True, "develop", "")):
            def fake_run(*args, **kwargs):
                if "clone" in args:
                    uat_dir.mkdir(parents=True, exist_ok=True)
                return MagicMock(returncode=0)
            mock_run.side_effect = fake_run

            init_project.step4b_uat_clone("owner", "myapp", repo_dir, uat_port=8001)

        env_file = uat_dir / ".env"
        assert env_file.exists(), ".env was not written to uat dir"
        env_content = env_file.read_text()
        assert "DB_PATH=./myapp-uat.db" in env_content
        assert "ENVIRONMENT=uat" in env_content

    def test_uat_db_name_differs_from_prd(self, tmp_path):
        """UAT DB filename contains '-uat' suffix, clearly separate from PRD."""
        repo_dir = _make_fake_repo_dir(tmp_path, "commander")
        uat_dir = repo_dir / "uat"

        with patch.object(init_project, "_run") as mock_run, \
             patch.object(init_project, "_try_run", return_value=(True, "develop", "")):
            def fake_run(*args, **kwargs):
                if "clone" in args:
                    uat_dir.mkdir(parents=True, exist_ok=True)
                return MagicMock(returncode=0)
            mock_run.side_effect = fake_run

            init_project.step4b_uat_clone("owner", "commander", repo_dir, uat_port=8001)

        env_content = (uat_dir / ".env").read_text()
        # UAT DB must contain "-uat" to distinguish from PRD
        assert "commander-uat.db" in env_content
        # PRD db would typically be commander.db — not present in UAT .env
        assert "DB_PATH=./commander.db" not in env_content


# ─────────────────────────────────────────────────────────────────────────────
# AC4 — UAT port is auto-detected (distinct from PRD port)
# ─────────────────────────────────────────────────────────────────────────────


class TestAC4AutoDetectPort:
    """AC4: UAT port is auto-detected and is always different from the PRD port."""

    def test_find_free_port_returns_preferred_when_free(self):
        """_find_free_port returns preferred port when it is available."""
        with patch.object(init_project, "_is_port_free", return_value=True):
            port = init_project._find_free_port(prefer=8001)
        assert port == 8001

    def test_find_free_port_falls_back_when_occupied(self):
        """_find_free_port returns OS-assigned port when preferred is occupied."""
        with patch.object(init_project, "_is_port_free", return_value=False), \
             patch.object(init_project, "_get_free_port", return_value=54321):
            port = init_project._find_free_port(prefer=8001)
        assert port == 54321

    def test_find_free_port_always_random_strategy(self):
        """_find_free_port with always_random always calls _get_free_port."""
        with patch.object(init_project, "_get_free_port", return_value=55555) as mock_random:
            port = init_project._find_free_port(prefer=8001, strategy="always_random")
        mock_random.assert_called_once()
        assert port == 55555

    def test_uat_port_distinct_from_prd_port(self):
        """When UAT preferred port equals PRD port, a different UAT port is chosen."""
        # Simulate both 8000 and 8001 being occupied — UAT must get something different
        free_ports_returned = iter([9000, 9001])

        with patch.object(init_project, "_is_port_free", return_value=False), \
             patch.object(init_project, "_get_free_port", side_effect=free_ports_returned):
            prd_port = init_project._find_free_port(prefer=8000)
            uat_port = init_project._find_free_port(prefer=8001)

        assert prd_port != uat_port

    def test_step4b_uses_given_uat_port_in_env(self, tmp_path):
        """step4b_uat_clone writes the given uat_port into .env PORT line."""
        repo_dir = _make_fake_repo_dir(tmp_path, "myapp")
        uat_dir = repo_dir / "uat"

        with patch.object(init_project, "_run") as mock_run, \
             patch.object(init_project, "_try_run", return_value=(True, "develop", "")):
            def fake_run(*args, **kwargs):
                if "clone" in args:
                    uat_dir.mkdir(parents=True, exist_ok=True)
                return MagicMock(returncode=0)
            mock_run.side_effect = fake_run

            init_project.step4b_uat_clone("owner", "myapp", repo_dir, uat_port=9999)

        env_content = (uat_dir / ".env").read_text()
        assert "PORT=9999" in env_content


# ─────────────────────────────────────────────────────────────────────────────
# AC5 — sprint.yaml includes uat: section
# ─────────────────────────────────────────────────────────────────────────────


class TestAC5SprintYamlUatSection:
    """AC5: sprint.yaml gets a uat: section with enabled, auto_sync, db_path."""

    def _call_step5(
        self,
        tmp_path: Path,
        repo_name: str = "myapp",
        skip_uat: bool = False,
        prd_port: int = 8000,
        uat_port: int = 8001,
    ) -> Path:
        """Helper: call step5_sprint_config and return sprint.yaml path."""
        projects_dir = tmp_path / "dev"
        projects_dir.mkdir()
        repo_dir = projects_dir / repo_name
        repo_dir.mkdir()
        (repo_dir / ".gitignore").write_text("*.pyc\n")

        with patch.object(init_project, "_run"):
            init_project.step5_sprint_config(
                owner="owner",
                repo_name=repo_name,
                repo_dir=repo_dir,
                projects_dir=projects_dir,
                tester_app_subdir="",
                skip_uat=skip_uat,
                prd_port=prd_port,
                uat_port=uat_port,
                port_strategy="prefer_default",
            )

        return repo_dir / ".commander" / "sprint.yaml"

    def test_uat_section_written_when_enabled(self, tmp_path):
        """sprint.yaml contains uat: section with enabled: true by default."""
        sprint_yaml = self._call_step5(tmp_path)
        content = sprint_yaml.read_text()
        assert "uat:" in content
        assert "enabled: true" in content
        assert "auto_sync: false" in content

    def test_uat_db_path_in_sprint_yaml(self, tmp_path):
        """sprint.yaml uat.db_path contains the project-specific UAT db name."""
        sprint_yaml = self._call_step5(tmp_path, repo_name="commander")
        content = sprint_yaml.read_text()
        assert "db_path: commander-uat.db" in content

    def test_uat_port_in_sprint_yaml_app_section(self, tmp_path):
        """sprint.yaml app section contains uat_port and prd_port."""
        sprint_yaml = self._call_step5(tmp_path, prd_port=8000, uat_port=8001)
        content = sprint_yaml.read_text()
        assert "prd_port: 8000" in content
        assert "uat_port: 8001" in content
        assert "port_strategy:" in content

    def test_skip_uat_sets_enabled_false(self, tmp_path):
        """When --skip-uat is used, sprint.yaml has uat.enabled: false."""
        sprint_yaml = self._call_step5(tmp_path, skip_uat=True)
        content = sprint_yaml.read_text()
        assert "enabled: false" in content

    def test_sprint_yaml_not_overwritten_if_exists(self, tmp_path, capsys):
        """step5_sprint_config skips if sprint.yaml already exists."""
        projects_dir = tmp_path / "dev"
        projects_dir.mkdir()
        repo_dir = projects_dir / "myapp"
        repo_dir.mkdir()
        commander_dir = repo_dir / ".commander"
        commander_dir.mkdir()
        sprint_yaml = commander_dir / "sprint.yaml"
        sprint_yaml.write_text("original: content\n")

        with patch.object(init_project, "_run"):
            init_project.step5_sprint_config(
                owner="owner",
                repo_name="myapp",
                repo_dir=repo_dir,
                projects_dir=projects_dir,
                tester_app_subdir="",
                skip_uat=False,
                prd_port=8000,
                uat_port=8001,
            )

        # Content must remain unchanged
        assert sprint_yaml.read_text() == "original: content\n"
        captured = capsys.readouterr()
        assert "already exists" in captured.out


# ─────────────────────────────────────────────────────────────────────────────
# AC6 — --skip-uat flag skips UAT clone creation and sets uat.enabled: false
# ─────────────────────────────────────────────────────────────────────────────


class TestAC6SkipUat:
    """AC6: --skip-uat skips clone creation and records enabled: false."""

    def test_skip_uat_prevents_uat_dir_creation(self, tmp_path):
        """When --skip-uat is passed, step4b_uat_clone is never called."""
        repo_dir = _make_fake_repo_dir(tmp_path, "myapp")
        uat_dir = repo_dir / "uat"

        with patch.object(init_project, "step4b_uat_clone") as mock_uat_clone, \
             patch.object(init_project, "preflight"), \
             patch.object(init_project, "step1_init_repo", return_value=repo_dir), \
             patch.object(init_project, "step2_develop_branch"), \
             patch.object(init_project, "step3_labels"), \
             patch.object(init_project, "step4_clones"), \
             patch.object(init_project, "step5_sprint_config"), \
             patch.object(init_project, "step6_register_project"), \
             patch.object(init_project, "step7_tracked_repos"), \
             patch.object(init_project, "step8_restart_dashboard"), \
             patch.object(init_project, "step9_verify"), \
             patch.object(init_project, "step10_summary"), \
             patch.object(init_project, "_load_machine_config", return_value={"default_owner": "owner"}), \
             patch.object(init_project, "_load_projects_json", return_value=[]), \
             patch.object(init_project, "_find_free_port", return_value=8000):

            sys.argv = ["init_project.py", "myapp", "--skip-uat"]
            init_project.main()

        # step4b_uat_clone must not have been called
        mock_uat_clone.assert_not_called()
        assert not uat_dir.exists()

    def test_skip_uat_enabled_false_in_sprint_yaml(self, tmp_path):
        """With --skip-uat, sprint.yaml uat.enabled is false."""
        projects_dir = tmp_path / "dev"
        projects_dir.mkdir()
        repo_dir = projects_dir / "myapp"
        repo_dir.mkdir()
        (repo_dir / ".gitignore").write_text("*.pyc\n")

        with patch.object(init_project, "_run"):
            init_project.step5_sprint_config(
                owner="owner",
                repo_name="myapp",
                repo_dir=repo_dir,
                projects_dir=projects_dir,
                tester_app_subdir="",
                skip_uat=True,  # ← key flag
                prd_port=8000,
                uat_port=8001,
            )

        content = (repo_dir / ".commander" / "sprint.yaml").read_text()
        assert "enabled: false" in content
        assert "enabled: true" not in content


# ─────────────────────────────────────────────────────────────────────────────
# AC7 — Rollback removes uat/ folder cleanly
# ─────────────────────────────────────────────────────────────────────────────


class TestAC7Rollback:
    """AC7: --rollback removes uat/ folder in addition to the other clones."""

    def test_rollback_removes_uat_dir(self, tmp_path):
        """rollback() removes <project>/uat/ when it exists."""
        projects_dir = tmp_path / "dev"
        projects_dir.mkdir()
        repo_dir = projects_dir / "myapp"
        repo_dir.mkdir()
        uat_dir = repo_dir / "uat"
        uat_dir.mkdir()

        with patch.object(init_project, "_load_projects_json", return_value=[]), \
             patch.object(init_project, "_save_projects_json"):
            init_project.rollback(
                owner="owner",
                repo_name="myapp",
                projects_dir=projects_dir,
                confirm_delete=False,
            )

        assert not uat_dir.exists(), "uat/ should have been removed by rollback"

    def test_rollback_skips_missing_uat_dir(self, tmp_path, capsys):
        """rollback() does not error when uat/ does not exist."""
        projects_dir = tmp_path / "dev"
        projects_dir.mkdir()
        repo_dir = projects_dir / "myapp"
        repo_dir.mkdir()
        # uat_dir intentionally not created

        with patch.object(init_project, "_load_projects_json", return_value=[]), \
             patch.object(init_project, "_save_projects_json"):
            # Should not raise
            init_project.rollback(
                owner="owner",
                repo_name="myapp",
                projects_dir=projects_dir,
                confirm_delete=False,
            )

        captured = capsys.readouterr()
        assert "does not exist" in captured.out

    def test_rollback_removes_all_four_clones(self, tmp_path):
        """rollback() removes main, uat, coder, and tester directories."""
        projects_dir = tmp_path / "dev"
        projects_dir.mkdir()

        # Create all four directories
        repo_dir = projects_dir / "myapp"
        repo_dir.mkdir()
        uat_dir = repo_dir / "uat"
        uat_dir.mkdir()
        coder_dir = projects_dir / "myapp-coder"
        coder_dir.mkdir()
        tester_dir = projects_dir / "myapp-tester"
        tester_dir.mkdir()

        with patch.object(init_project, "_load_projects_json", return_value=[]), \
             patch.object(init_project, "_save_projects_json"):
            init_project.rollback(
                owner="owner",
                repo_name="myapp",
                projects_dir=projects_dir,
                confirm_delete=False,
            )

        assert not uat_dir.exists(), "uat/ not removed"
        assert not repo_dir.exists(), "main project dir not removed"
        assert not coder_dir.exists(), "coder dir not removed"
        assert not tester_dir.exists(), "tester dir not removed"


# ─────────────────────────────────────────────────────────────────────────────
# AC8 — Migration script adds uat/ to existing projects
# ─────────────────────────────────────────────────────────────────────────────


class TestAC8MigrationScript:
    """AC8: migrate_add_uat.py adds uat/ clone to an existing project."""

    def test_migrate_creates_uat_clone(self, tmp_path):
        """migrate() clones and checks out develop when uat/ does not exist."""
        projects_dir = tmp_path / "dev"
        projects_dir.mkdir()
        repo_dir = projects_dir / "myapp"
        repo_dir.mkdir()
        commander_dir = repo_dir / ".commander"
        commander_dir.mkdir()
        _write_sprint_yaml(repo_dir)
        uat_dir = repo_dir / "uat"

        with patch.object(migrate_add_uat, "_run") as mock_run, \
             patch.object(migrate_add_uat, "_try_run", return_value=(True, "develop", "")):
            def fake_run(*args, **kwargs):
                if "clone" in args:
                    uat_dir.mkdir(parents=True, exist_ok=True)
                return MagicMock(returncode=0)
            mock_run.side_effect = fake_run

            migrate_add_uat.migrate(
                owner="owner",
                repo_name="myapp",
                projects_dir=projects_dir,
                uat_port=8001,
                port_strategy="prefer_default",
            )

        assert uat_dir.exists()

    def test_migrate_writes_env_with_separate_db(self, tmp_path):
        """migrate() writes .env with UAT-specific DB path, not PRD path."""
        projects_dir = tmp_path / "dev"
        projects_dir.mkdir()
        repo_dir = projects_dir / "myapp"
        repo_dir.mkdir()
        commander_dir = repo_dir / ".commander"
        commander_dir.mkdir()
        _write_sprint_yaml(repo_dir)
        uat_dir = repo_dir / "uat"

        with patch.object(migrate_add_uat, "_run") as mock_run, \
             patch.object(migrate_add_uat, "_try_run", return_value=(True, "develop", "")):
            def fake_run(*args, **kwargs):
                if "clone" in args:
                    uat_dir.mkdir(parents=True, exist_ok=True)
                return MagicMock(returncode=0)
            mock_run.side_effect = fake_run

            migrate_add_uat.migrate(
                owner="owner",
                repo_name="myapp",
                projects_dir=projects_dir,
                uat_port=8001,
                port_strategy="prefer_default",
            )

        env_file = uat_dir / ".env"
        assert env_file.exists()
        env_content = env_file.read_text()
        assert "DB_PATH=./myapp-uat.db" in env_content
        assert "ENVIRONMENT=uat" in env_content
        assert "PORT=8001" in env_content

    def test_migrate_updates_sprint_yaml_uat_section(self, tmp_path):
        """migrate() adds uat: block to existing sprint.yaml."""
        projects_dir = tmp_path / "dev"
        projects_dir.mkdir()
        repo_dir = projects_dir / "myapp"
        repo_dir.mkdir()
        commander_dir = repo_dir / ".commander"
        commander_dir.mkdir()
        sprint_yaml = _write_sprint_yaml(repo_dir)
        uat_dir = repo_dir / "uat"

        with patch.object(migrate_add_uat, "_run") as mock_run, \
             patch.object(migrate_add_uat, "_try_run", return_value=(True, "develop", "")):
            def fake_run(*args, **kwargs):
                if "clone" in args:
                    uat_dir.mkdir(parents=True, exist_ok=True)
                return MagicMock(returncode=0)
            mock_run.side_effect = fake_run

            migrate_add_uat.migrate(
                owner="owner",
                repo_name="myapp",
                projects_dir=projects_dir,
                uat_port=8001,
                port_strategy="prefer_default",
            )

        content = sprint_yaml.read_text()
        assert "uat:" in content
        assert "enabled: true" in content
        assert "auto_sync: false" in content
        assert "myapp-uat.db" in content

    def test_migrate_errors_when_project_missing(self, tmp_path):
        """migrate() exits with error when project directory does not exist."""
        projects_dir = tmp_path / "dev"
        projects_dir.mkdir()

        with pytest.raises(SystemExit) as exc_info:
            migrate_add_uat.migrate(
                owner="owner",
                repo_name="nonexistent-project",
                projects_dir=projects_dir,
                uat_port=8001,
                port_strategy="prefer_default",
            )

        assert exc_info.value.code == 1

    def test_migrate_skips_clone_when_uat_already_exists(self, tmp_path, capsys):
        """migrate() skips clone step when uat/ already exists."""
        projects_dir = tmp_path / "dev"
        projects_dir.mkdir()
        repo_dir = projects_dir / "myapp"
        repo_dir.mkdir()
        commander_dir = repo_dir / ".commander"
        commander_dir.mkdir()
        _write_sprint_yaml(repo_dir)
        uat_dir = repo_dir / "uat"
        uat_dir.mkdir()

        with patch.object(migrate_add_uat, "_run") as mock_run, \
             patch.object(migrate_add_uat, "_try_run", return_value=(True, "develop", "")):
            migrate_add_uat.migrate(
                owner="owner",
                repo_name="myapp",
                projects_dir=projects_dir,
                uat_port=8001,
                port_strategy="prefer_default",
            )

        # git clone should NOT have been called
        clone_calls = [c for c in mock_run.call_args_list if "clone" in str(c)]
        assert not clone_calls, "git clone should be skipped when uat/ already exists"

    def test_update_sprint_yaml_adds_uat_block(self, tmp_path):
        """_update_sprint_yaml() adds uat: block to a yaml without one."""
        sprint_yaml = tmp_path / "sprint.yaml"
        sprint_yaml.write_text(
            "repo_name: owner/myapp\n"
            "worktrees:\n"
            "  coder: /dev/myapp-coder\n"
            "  tester: /dev/myapp-tester\n"
        )

        migrate_add_uat._update_sprint_yaml(sprint_yaml, "myapp-uat.db", 8001)

        content = sprint_yaml.read_text()
        assert "uat:" in content
        assert "enabled: true" in content
        assert "auto_sync: false" in content
        assert "myapp-uat.db" in content

    def test_update_sprint_yaml_replaces_existing_uat_block(self, tmp_path):
        """_update_sprint_yaml() replaces an existing uat: block."""
        sprint_yaml = tmp_path / "sprint.yaml"
        sprint_yaml.write_text(
            "repo_name: owner/myapp\n"
            "uat:\n"
            "  enabled: false\n"
            "  old_key: old_value\n"
        )

        migrate_add_uat._update_sprint_yaml(sprint_yaml, "myapp-uat.db", 8001)

        content = sprint_yaml.read_text()
        assert "enabled: true" in content
        assert "old_key" not in content

    def test_migrate_port_auto_detected(self):
        """migrate_add_uat._find_free_port uses auto-detection logic."""
        with patch.object(migrate_add_uat, "_is_port_free", return_value=True):
            port = migrate_add_uat._find_free_port(prefer=8001)
        assert port == 8001

        with patch.object(migrate_add_uat, "_is_port_free", return_value=False), \
             patch.object(migrate_add_uat, "_get_free_port", return_value=55000):
            port = migrate_add_uat._find_free_port(prefer=8001)
        assert port == 55000
