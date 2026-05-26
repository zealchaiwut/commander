"""Tests for issue #62: auto-detect free port per project.

AC-4: load_config() reads app.default_port and app.port_strategy from
      .commander/sprint.yaml and stores them in SprintConfig.
AC-5: When SprintConfig.app_default_port is set, sprint manager calls
      find_port.py --prefer <default_port> before dispatching coder.
AC-6: Sprint manager writes chosen port to <coder_worktree>/.commander/runtime/port.
AC-7: Sprint manager passes COMMANDER_APP_PORT=<chosen_port> in env of coder
      and tester Popen calls.
AC-8: When app section is absent from sprint.yaml (or no config file), sprint
      manager skips all port-detection steps.
AC-9: Dashboard GET /api/projects includes app_port per project.
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add dashboard dir to path for importing sprint_manager and projects
DASHBOARD_DIR = Path(__file__).parent.parent
SCRIPTS_DIR = DASHBOARD_DIR / "scripts"
sys.path.insert(0, str(DASHBOARD_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))


# ── AC-4: load_config reads app section ───────────────────────────────────────

class TestLoadConfigAppSection:
    """AC-4: load_config() parses app.default_port and app.port_strategy."""

    def _write_yaml(self, tmp_path: Path, content: str) -> Path:
        """Write sprint.yaml with required fields plus custom content."""
        coder_wt  = tmp_path / "coder"
        tester_wt = tmp_path / "tester"
        coder_wt.mkdir()
        tester_wt.mkdir()

        yaml_content = (
            f"repo_name: owner/repo\n"
            f"worktrees:\n"
            f"  coder: {coder_wt}\n"
            f"  tester: {tester_wt}\n"
        ) + content

        config_dir = tmp_path / ".commander"
        config_dir.mkdir(exist_ok=True)
        config_file = config_dir / "sprint.yaml"
        config_file.write_text(yaml_content, encoding="utf-8")
        return config_file

    def test_app_default_port_and_strategy_loaded(self, tmp_path):
        """AC-4: Both default_port and port_strategy are loaded from config."""
        import sprint_manager as sm

        config_file = self._write_yaml(
            tmp_path,
            "app:\n  default_port: 8002\n  port_strategy: prefer_default\n",
        )
        cfg = sm.load_config(config_file)
        assert cfg.app_default_port == 8002
        assert cfg.app_port_strategy == "prefer_default"

    def test_app_always_random_strategy(self, tmp_path):
        """AC-4: always_random strategy is parsed correctly."""
        import sprint_manager as sm

        config_file = self._write_yaml(
            tmp_path,
            "app:\n  default_port: 9000\n  port_strategy: always_random\n",
        )
        cfg = sm.load_config(config_file)
        assert cfg.app_default_port == 9000
        assert cfg.app_port_strategy == "always_random"

    def test_app_section_absent_defaults_to_none(self, tmp_path):
        """AC-8: When app section is absent, app_default_port is None."""
        import sprint_manager as sm

        config_file = self._write_yaml(tmp_path, "")
        cfg = sm.load_config(config_file)
        assert cfg.app_default_port is None
        assert cfg.app_port_strategy == "prefer_default"

    def test_only_default_port_set_strategy_defaults_to_prefer_default(self, tmp_path):
        """AC-4: When only default_port is set, strategy defaults to prefer_default."""
        import sprint_manager as sm

        config_file = self._write_yaml(
            tmp_path,
            "app:\n  default_port: 8080\n",
        )
        cfg = sm.load_config(config_file)
        assert cfg.app_default_port == 8080
        assert cfg.app_port_strategy == "prefer_default"

    def test_sprint_config_dataclass_defaults(self):
        """AC-4: SprintConfig dataclass has correct default values for new fields."""
        import sprint_manager as sm

        cfg = sm.SprintConfig()
        assert cfg.app_default_port is None
        assert cfg.app_port_strategy == "prefer_default"


# ── AC-5: _detect_port calls find_port.py ─────────────────────────────────────

class TestDetectPort:
    """AC-5: _detect_port() calls find_port.py and returns chosen port."""

    def test_detect_port_returns_none_when_no_app_port(self, tmp_path):
        """AC-8: _detect_port returns None when app_default_port is None."""
        import sprint_manager as sm

        cfg = sm.SprintConfig(
            worktree_coder=tmp_path,
            app_default_port=None,
        )
        result = sm._detect_port(cfg)
        assert result is None

    def test_detect_port_calls_find_port_script(self, tmp_path):
        """AC-5: _detect_port calls find_port.py with --prefer and --strategy."""
        import sprint_manager as sm

        cfg = sm.SprintConfig(
            worktree_coder=tmp_path,
            scripts_dir=SCRIPTS_DIR,
            app_default_port=8002,
            app_port_strategy="prefer_default",
        )
        # find_port.py is a real script — call _detect_port for real
        result = sm._detect_port(cfg)
        assert result is not None
        assert isinstance(result, int)
        assert result > 0

    def test_detect_port_always_random_returns_different(self, tmp_path):
        """AC-5: always_random strategy produces a valid port (may differ from preferred)."""
        import sprint_manager as sm

        cfg = sm.SprintConfig(
            worktree_coder=tmp_path,
            scripts_dir=SCRIPTS_DIR,
            app_default_port=8002,
            app_port_strategy="always_random",
        )
        result = sm._detect_port(cfg)
        assert result is not None
        assert isinstance(result, int)
        assert result > 0


# ── AC-6: _write_runtime_port writes the port file ────────────────────────────

class TestWriteRuntimePort:
    """AC-6: _write_runtime_port creates .commander/runtime/port."""

    def test_writes_port_to_file(self, tmp_path):
        """AC-6: Port is written to <worktree>/.commander/runtime/port."""
        import sprint_manager as sm

        sm._write_runtime_port(tmp_path, 54321)

        port_file = tmp_path / ".commander" / "runtime" / "port"
        assert port_file.exists(), "runtime/port file was not created"
        assert port_file.read_text(encoding="utf-8").strip() == "54321"

    def test_creates_parent_dirs(self, tmp_path):
        """AC-6: Parent directories are created as needed."""
        import sprint_manager as sm

        # Ensure the runtime dir does not exist yet
        runtime_dir = tmp_path / ".commander" / "runtime"
        assert not runtime_dir.exists()

        sm._write_runtime_port(tmp_path, 12345)

        assert runtime_dir.exists()
        assert (runtime_dir / "port").exists()

    def test_overwrites_existing_file(self, tmp_path):
        """AC-6: Writing a new port overwrites the existing file."""
        import sprint_manager as sm

        sm._write_runtime_port(tmp_path, 8002)
        sm._write_runtime_port(tmp_path, 9999)

        port_file = tmp_path / ".commander" / "runtime" / "port"
        assert port_file.read_text(encoding="utf-8").strip() == "9999"


# ── AC-7: COMMANDER_APP_PORT injected into subprocess env ─────────────────────

class TestDispatchEnvVar:
    """AC-7: COMMANDER_APP_PORT is passed in coder and tester Popen env."""

    def test_dispatch_coder_injects_app_port(self, tmp_path):
        """AC-7: _dispatch_coder passes COMMANDER_APP_PORT when chosen_port is set."""
        import sprint_manager as sm

        captured_envs = []

        def fake_popen(cmd, **kwargs):
            captured_envs.append(kwargs.get("env"))
            raise FileNotFoundError("claude not found (test stub)")

        cfg = sm.SprintConfig(
            worktree_coder=tmp_path,
            logs_dir=tmp_path / "logs",
            scripts_dir=SCRIPTS_DIR,
            api_url="http://localhost:8000",
            repo_name="owner/repo",
        )

        with patch("sprint_manager.subprocess.Popen", side_effect=fake_popen):
            ok, _cat = sm._dispatch_coder(
                issue_num=1,
                alert_modes=[],
                cfg=cfg,
                chosen_port=54321,
            )

        # Stub success (FileNotFoundError treated as success)
        assert ok is True
        assert len(captured_envs) == 1
        env = captured_envs[0]
        assert env is not None, "No env was passed to Popen"
        assert env.get("COMMANDER_APP_PORT") == "54321"

    def test_dispatch_coder_no_port_no_env_override(self, tmp_path):
        """AC-8: When chosen_port is None, no COMMANDER_APP_PORT in env."""
        import sprint_manager as sm

        captured_envs = []

        def fake_popen(cmd, **kwargs):
            captured_envs.append(kwargs.get("env"))
            raise FileNotFoundError("claude not found (test stub)")

        cfg = sm.SprintConfig(
            worktree_coder=tmp_path,
            logs_dir=tmp_path / "logs",
            scripts_dir=SCRIPTS_DIR,
            api_url="http://localhost:8000",
            repo_name="owner/repo",
        )

        with patch("sprint_manager.subprocess.Popen", side_effect=fake_popen):
            ok, _cat = sm._dispatch_coder(
                issue_num=1,
                alert_modes=[],
                cfg=cfg,
                chosen_port=None,
            )

        assert ok is True
        # env should be None (no override)
        assert captured_envs[0] is None, (
            "Expected no env override when chosen_port is None"
        )

    def test_dispatch_tester_injects_app_port(self, tmp_path):
        """AC-7: _dispatch_tester passes COMMANDER_APP_PORT when chosen_port is set."""
        import sprint_manager as sm

        captured_envs = []

        def fake_popen(cmd, **kwargs):
            captured_envs.append(kwargs.get("env"))
            raise FileNotFoundError("claude not found (test stub)")

        # Create a log file first (tester opens in append mode)
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()

        cfg = sm.SprintConfig(
            worktree_tester=tmp_path,
            worktree_coder=tmp_path,
            logs_dir=logs_dir,
            scripts_dir=SCRIPTS_DIR,
            api_url="http://localhost:8000",
            repo_name="owner/repo",
        )

        with patch("sprint_manager.subprocess.Popen", side_effect=fake_popen):
            rc, _cat = sm._dispatch_tester(
                issue_num=1,
                alert_modes=[],
                cfg=cfg,
                chosen_port=54321,
            )

        # Stub success
        assert rc == 0
        assert len(captured_envs) == 1
        env = captured_envs[0]
        assert env is not None
        assert env.get("COMMANDER_APP_PORT") == "54321"

    def test_dispatch_tester_no_port_no_env_override(self, tmp_path):
        """AC-8: When chosen_port is None, no COMMANDER_APP_PORT in tester env."""
        import sprint_manager as sm

        captured_envs = []

        def fake_popen(cmd, **kwargs):
            captured_envs.append(kwargs.get("env"))
            raise FileNotFoundError("claude not found (test stub)")

        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()

        cfg = sm.SprintConfig(
            worktree_tester=tmp_path,
            worktree_coder=tmp_path,
            logs_dir=logs_dir,
            scripts_dir=SCRIPTS_DIR,
            api_url="http://localhost:8000",
            repo_name="owner/repo",
        )

        with patch("sprint_manager.subprocess.Popen", side_effect=fake_popen):
            rc, _cat = sm._dispatch_tester(
                issue_num=1,
                alert_modes=[],
                cfg=cfg,
                chosen_port=None,
            )

        assert rc == 0
        assert captured_envs[0] is None


# ── AC-9: Dashboard GET /api/projects returns app_port ────────────────────────

class TestProjectsAppPort:
    """AC-9: get_all_projects() returns app_port per project."""

    def test_app_port_present_in_project_when_runtime_file_exists(self, tmp_path, monkeypatch):
        """AC-9: app_port is populated from runtime/port when file exists."""
        import projects

        # Create a fake runtime/port file
        runtime_dir = tmp_path / ".commander" / "runtime"
        runtime_dir.mkdir(parents=True)
        (runtime_dir / "port").write_text("8002", encoding="utf-8")

        # Monkeypatch _worktree_path_for_repo to return our tmp_path
        monkeypatch.setattr(
            projects,
            "_worktree_path_for_repo",
            lambda repo: tmp_path,
        )

        result = projects._read_runtime_port(tmp_path)
        assert result == 8002

    def test_app_port_none_when_no_runtime_file(self, tmp_path):
        """AC-9: app_port is None when runtime/port does not exist."""
        import projects

        result = projects._read_runtime_port(tmp_path)
        assert result is None

    def test_read_runtime_port_invalid_content(self, tmp_path):
        """AC-9: _read_runtime_port returns None when file content is not an int."""
        import projects

        runtime_dir = tmp_path / ".commander" / "runtime"
        runtime_dir.mkdir(parents=True)
        (runtime_dir / "port").write_text("not-a-number", encoding="utf-8")

        result = projects._read_runtime_port(tmp_path)
        assert result is None

    def test_get_all_projects_includes_app_port_key(self, tmp_path, monkeypatch):
        """AC-9: Each project in get_all_projects() result has an app_port key."""
        import projects

        # Patch away GitHub and DB calls
        monkeypatch.setattr(projects, "load_projects", lambda: [
            {"repo": "owner/testrepo", "name": "Test", "icon": "ti-folder",
             "color": "blue", "active_sprints": {}}
        ])
        monkeypatch.setattr(
            projects.github_client, "list_open_issues", lambda **kw: []
        )
        monkeypatch.setattr(
            projects.github_client, "list_issues", lambda *a, **kw: []
        )
        monkeypatch.setattr(
            projects.db, "get_tokens_today", lambda **kw: {"input_tokens": 0, "output_tokens": 0}
        )
        monkeypatch.setattr(
            projects, "_worktree_path_for_repo", lambda repo: None
        )

        result = projects.get_all_projects(agents=[])
        assert "projects" in result
        assert len(result["projects"]) == 1
        proj = result["projects"][0]
        assert "app_port" in proj, "app_port key missing from project response"
        assert proj["app_port"] is None  # no runtime file

    def test_get_all_projects_app_port_populated_from_runtime_file(
        self, tmp_path, monkeypatch
    ):
        """AC-9: app_port is populated when runtime/port file exists."""
        import projects

        # Write a runtime port file
        runtime_dir = tmp_path / ".commander" / "runtime"
        runtime_dir.mkdir(parents=True)
        (runtime_dir / "port").write_text("9001", encoding="utf-8")

        monkeypatch.setattr(projects, "load_projects", lambda: [
            {"repo": "owner/testrepo", "name": "Test", "icon": "ti-folder",
             "color": "blue", "active_sprints": {}}
        ])
        monkeypatch.setattr(
            projects.github_client, "list_open_issues", lambda **kw: []
        )
        monkeypatch.setattr(
            projects.github_client, "list_issues", lambda *a, **kw: []
        )
        monkeypatch.setattr(
            projects.db, "get_tokens_today", lambda **kw: {"input_tokens": 0, "output_tokens": 0}
        )
        monkeypatch.setattr(
            projects, "_worktree_path_for_repo", lambda repo: tmp_path
        )

        result = projects.get_all_projects(agents=[])
        proj = result["projects"][0]
        assert proj["app_port"] == 9001
