"""Tests for issue #1671: Per-role LLM provider routing via profile override.

AC items verified:
  AC1  model_routing.py exposes get_role_profile(role, cfg) -> str | None that
       returns None when no override is set
  AC2  SprintConfig (and sprint.yaml) support coder_profile and tester_profile
       fields; unset fields produce None
  AC3  _dispatch_coder sets sub_env["CCPROXY_PROFILE"] to the coder profile
       when coder_profile is configured
  AC4  _dispatch_tester sets sub_env["CCPROXY_PROFILE"] to the tester profile
       when tester_profile is configured
  AC5  When coder_profile / tester_profile is absent, CCPROXY_PROFILE falls
       back to whatever the global env supplies (no regression)
  AC6  No other dispatch paths (reviewer etc.) are affected — only coder and
       tester set CCPROXY_PROFILE via get_role_profile
  AC7  Unit test: coder subprocess env contains correct profile when set
  AC8  Unit test: tester subprocess env contains correct profile when set
  AC9  Unit test: subprocess env inherits global CCPROXY_PROFILE when no
       per-role override is configured
"""
from __future__ import annotations

import os
import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SM_DIR = REPO_ROOT / "services" / "sprint_manager"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))
sys.path.insert(0, str(SM_DIR))

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")

import services.sprint_manager.sprint_manager as sm
from services.sprint_manager.model_routing import get_role_profile
from services.sprint_manager.config import SprintConfig


# ── helpers ───────────────────────────────────────────────────────────────────

def _minimal_cfg(tmp_path, extra_yaml: str = "") -> SprintConfig:
    """Build a minimal SprintConfig from YAML with optional extra top-level fields."""
    coder_dir = tmp_path / "coder"
    coder_dir.mkdir(exist_ok=True)
    tester_dir = tmp_path / "tester"
    tester_dir.mkdir(exist_ok=True)
    base = textwrap.dedent(f"""\
        repo_name: test/repo
        worktrees:
          coder: {coder_dir}
          tester: {tester_dir}
    """)
    yaml_text = base + ("\n" + textwrap.dedent(extra_yaml) if extra_yaml else "")
    config_path = tmp_path / "sprint.yaml"
    config_path.write_text(yaml_text)
    return sm.load_config(config_path)


def _popen_capture(captured: dict):
    """Return a fake Popen that captures cmd and env into `captured`."""
    def _fake_popen(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        captured["env"] = dict(kwargs.get("env") or {})
        proc = MagicMock()
        proc.wait.return_value = 0
        return proc
    return _fake_popen


# ── AC1: get_role_profile function exists and returns None when not set ───────

class TestAC1GetRoleProfile:
    def test_function_exists_in_model_routing(self):
        """AC1: get_role_profile is importable from model_routing."""
        assert callable(get_role_profile), "get_role_profile must be callable"

    def test_returns_none_for_coder_when_cfg_is_none(self):
        """AC1: returns None when cfg is None (no config loaded)."""
        result = get_role_profile("coder", None)
        assert result is None

    def test_returns_none_for_tester_when_cfg_is_none(self):
        """AC1: returns None for tester when cfg is None."""
        result = get_role_profile("tester", None)
        assert result is None

    def test_returns_none_for_unknown_role(self, tmp_path):
        """AC1: returns None for an unrecognized role name."""
        cfg = _minimal_cfg(tmp_path)
        result = get_role_profile("reviewer", cfg)
        assert result is None

    def test_returns_none_when_coder_profile_not_set(self, tmp_path):
        """AC1: returns None when coder has no profile override."""
        cfg = _minimal_cfg(tmp_path)
        result = get_role_profile("coder", cfg)
        assert result is None

    def test_returns_none_when_tester_profile_not_set(self, tmp_path):
        """AC1: returns None when tester has no profile override."""
        cfg = _minimal_cfg(tmp_path)
        result = get_role_profile("tester", cfg)
        assert result is None

    def test_returns_coder_profile_string(self, tmp_path):
        """AC1: returns the coder_profile string when configured."""
        cfg = _minimal_cfg(tmp_path, "agent_config:\n  coder:\n    profile: anthropic")
        result = get_role_profile("coder", cfg)
        assert result == "anthropic"

    def test_returns_tester_profile_string(self, tmp_path):
        """AC1: returns the tester_profile string when configured."""
        cfg = _minimal_cfg(tmp_path, "agent_config:\n  tester:\n    profile: ica")
        result = get_role_profile("tester", cfg)
        assert result == "ica"


# ── AC2: SprintConfig + yaml support coder_profile / tester_profile ──────────

class TestAC2ConfigFields:
    def test_sprintconfig_has_coder_profile_field(self):
        """AC2: SprintConfig dataclass has coder_profile attribute."""
        assert hasattr(SprintConfig, "__dataclass_fields__"), "SprintConfig must be a dataclass"
        assert "coder_profile" in SprintConfig.__dataclass_fields__, (
            "SprintConfig must have a coder_profile field"
        )

    def test_sprintconfig_has_tester_profile_field(self):
        """AC2: SprintConfig dataclass has tester_profile attribute."""
        assert "tester_profile" in SprintConfig.__dataclass_fields__, (
            "SprintConfig must have a tester_profile field"
        )

    def test_coder_profile_defaults_to_none(self):
        """AC2: coder_profile defaults to None."""
        cfg = SprintConfig()
        assert cfg.coder_profile is None

    def test_tester_profile_defaults_to_none(self):
        """AC2: tester_profile defaults to None."""
        cfg = SprintConfig()
        assert cfg.tester_profile is None

    def test_load_config_reads_coder_profile(self, tmp_path):
        """AC2: load_config parses agent_config.coder.profile from YAML."""
        cfg = _minimal_cfg(
            tmp_path,
            "agent_config:\n  coder:\n    profile: anthropic",
        )
        assert cfg.coder_profile == "anthropic"

    def test_load_config_reads_tester_profile(self, tmp_path):
        """AC2: load_config parses agent_config.tester.profile from YAML."""
        cfg = _minimal_cfg(
            tmp_path,
            "agent_config:\n  tester:\n    profile: ica",
        )
        assert cfg.tester_profile == "ica"

    def test_load_config_coder_profile_absent_is_none(self, tmp_path):
        """AC2: coder_profile is None when not set in YAML."""
        cfg = _minimal_cfg(tmp_path)
        assert cfg.coder_profile is None

    def test_load_config_tester_profile_absent_is_none(self, tmp_path):
        """AC2: tester_profile is None when not set in YAML."""
        cfg = _minimal_cfg(tmp_path)
        assert cfg.tester_profile is None

    def test_load_config_both_profiles_set(self, tmp_path):
        """AC2: both profiles loaded independently from YAML."""
        cfg = _minimal_cfg(
            tmp_path,
            textwrap.dedent("""
                agent_config:
                  coder:
                    profile: anthropic
                  tester:
                    profile: ica
            """),
        )
        assert cfg.coder_profile == "anthropic"
        assert cfg.tester_profile == "ica"


# ── AC3 + AC7: _dispatch_coder sets CCPROXY_PROFILE when coder_profile set ───

class TestAC3CoderProfileInjected:
    def test_dispatch_coder_sets_ccproxy_profile(self, tmp_path):
        """AC3/AC7: _dispatch_coder injects CCPROXY_PROFILE=coder_profile into subprocess env."""
        cfg = _minimal_cfg(
            tmp_path,
            "agent_config:\n  coder:\n    profile: anthropic",
        )
        cfg.worktree_coder = tmp_path / "coder"
        cfg.logs_dir = tmp_path / "logs"
        cfg.logs_dir.mkdir()

        captured: dict = {}

        with patch("subprocess.Popen", side_effect=_popen_capture(captured)), \
             patch.object(sm, "_post_agent_event"), \
             patch.object(sm, "_load_agent_persona", return_value=None), \
             patch.object(sm, "_dispatch_doctor", return_value=None), \
             patch.object(sm, "_design_docs_guard", return_value=None), \
             patch.object(sm, "_worktree_hygiene", return_value=(None, None, None)), \
             patch.object(sm, "_crg_update_worktree"), \
             patch.object(sm, "_issue_log_path", return_value=tmp_path / "logs" / "1671.log"), \
             patch.object(sm, "_load_estimate", return_value=None), \
             patch.object(sm, "_build_estimate_paths_block", return_value=""), \
             patch.object(sm, "_build_design_block", return_value=""), \
             patch.object(sm, "_impeccable_context_instruction", return_value=""), \
             patch.object(sm, "_build_failure_suffix", return_value=""), \
             patch("services.sprint_manager.dispatch._fetch_dispatch_issue_body", return_value=None), \
             patch.object(sm, "HangDetector") as mock_hd, \
             patch.object(sm, "_db_update_worktree_shas_sm"):
            mock_hd.return_value.start = MagicMock()
            mock_hd.return_value.stop = MagicMock()
            mock_hd.return_value.killed = False
            sm._dispatch_coder(1671, [], cfg=cfg, sprint_label="sprint-105")

        assert "CCPROXY_PROFILE" in captured.get("env", {}), (
            "CCPROXY_PROFILE must be set in coder subprocess env when coder_profile is configured"
        )
        assert captured["env"]["CCPROXY_PROFILE"] == "anthropic", (
            f"Expected CCPROXY_PROFILE=anthropic, got {captured['env'].get('CCPROXY_PROFILE')!r}"
        )

    def test_dispatch_coder_does_not_set_ccproxy_when_profile_absent(self, tmp_path, monkeypatch):
        """AC5: when coder_profile is absent, CCPROXY_PROFILE is NOT explicitly overridden."""
        monkeypatch.delenv("CCPROXY_PROFILE", raising=False)
        cfg = _minimal_cfg(tmp_path)
        cfg.worktree_coder = tmp_path / "coder"
        cfg.logs_dir = tmp_path / "logs"
        cfg.logs_dir.mkdir()

        captured: dict = {}

        with patch("subprocess.Popen", side_effect=_popen_capture(captured)), \
             patch.object(sm, "_post_agent_event"), \
             patch.object(sm, "_load_agent_persona", return_value=None), \
             patch.object(sm, "_dispatch_doctor", return_value=None), \
             patch.object(sm, "_design_docs_guard", return_value=None), \
             patch.object(sm, "_worktree_hygiene", return_value=(None, None, None)), \
             patch.object(sm, "_crg_update_worktree"), \
             patch.object(sm, "_issue_log_path", return_value=tmp_path / "logs" / "1671.log"), \
             patch.object(sm, "_load_estimate", return_value=None), \
             patch.object(sm, "_build_estimate_paths_block", return_value=""), \
             patch.object(sm, "_build_design_block", return_value=""), \
             patch.object(sm, "_impeccable_context_instruction", return_value=""), \
             patch.object(sm, "_build_failure_suffix", return_value=""), \
             patch("services.sprint_manager.dispatch._fetch_dispatch_issue_body", return_value=None), \
             patch.object(sm, "HangDetector") as mock_hd, \
             patch.object(sm, "_db_update_worktree_shas_sm"):
            mock_hd.return_value.start = MagicMock()
            mock_hd.return_value.stop = MagicMock()
            mock_hd.return_value.killed = False
            sm._dispatch_coder(1671, [], cfg=cfg, sprint_label="sprint-105")

        # When no per-role profile is set, CCPROXY_PROFILE should not appear
        # (it was not in the environment either).
        assert "CCPROXY_PROFILE" not in captured.get("env", {}), (
            "CCPROXY_PROFILE must NOT be injected when coder_profile is absent and env has none"
        )


# ── AC4 + AC8: _dispatch_tester sets CCPROXY_PROFILE when tester_profile set ─

class TestAC4TesterProfileInjected:
    def _common_tester_patches(self, sm, tmp_path, captured):
        """Return a list of context managers for patching _dispatch_tester dependencies."""
        return [
            patch("subprocess.Popen", side_effect=_popen_capture(captured)),
            patch.object(sm, "_post_agent_event"),
            patch.object(sm, "_load_agent_persona", return_value=None),
            patch.object(sm, "_worktree_hygiene", return_value=(None, None, None)),
            patch.object(sm, "_crg_update_worktree"),
            patch.object(sm, "_issue_log_path", return_value=tmp_path / "logs" / "1671.log"),
            patch.object(sm, "_impeccable_context_instruction", return_value=""),
            patch.object(sm, "_get_issue_labels", return_value=[]),
            patch.object(sm, "_classify_risk_tier", return_value="LOW"),
            patch.object(sm, "_resolve_uat_env_for_tester", return_value=({}, None)),
            patch.object(sm, "agent_browser_runner"),
            patch.object(sm, "HangDetector"),
            patch.object(sm, "_db_update_worktree_shas_sm"),
        ]

    def test_dispatch_tester_sets_ccproxy_profile(self, tmp_path):
        """AC4/AC8: _dispatch_tester injects CCPROXY_PROFILE=tester_profile into subprocess env."""
        cfg = _minimal_cfg(
            tmp_path,
            "agent_config:\n  tester:\n    profile: ica",
        )
        cfg.worktree_tester = tmp_path / "tester"
        cfg.tester_app_subdir = ""
        cfg.logs_dir = tmp_path / "logs"
        cfg.logs_dir.mkdir()

        captured: dict = {}
        patches = self._common_tester_patches(sm, tmp_path, captured)

        from contextlib import ExitStack
        with ExitStack() as stack:
            mocks = [stack.enter_context(p) for p in patches]
            hd_mock = mocks[-3]  # HangDetector mock
            hd_mock.return_value.start = MagicMock()
            hd_mock.return_value.stop = MagicMock()
            hd_mock.return_value.killed = False
            sm._dispatch_tester(1671, [], cfg=cfg, sprint_label="sprint-105")

        assert "CCPROXY_PROFILE" in captured.get("env", {}), (
            "CCPROXY_PROFILE must be set in tester subprocess env when tester_profile is configured"
        )
        assert captured["env"]["CCPROXY_PROFILE"] == "ica", (
            f"Expected CCPROXY_PROFILE=ica, got {captured['env'].get('CCPROXY_PROFILE')!r}"
        )

    def test_dispatch_tester_does_not_set_ccproxy_when_profile_absent(self, tmp_path, monkeypatch):
        """AC5: when tester_profile is absent, CCPROXY_PROFILE is NOT explicitly injected."""
        monkeypatch.delenv("CCPROXY_PROFILE", raising=False)
        cfg = _minimal_cfg(tmp_path)
        cfg.worktree_tester = tmp_path / "tester"
        cfg.tester_app_subdir = ""
        cfg.logs_dir = tmp_path / "logs"
        cfg.logs_dir.mkdir()

        captured: dict = {}
        patches = self._common_tester_patches(sm, tmp_path, captured)

        from contextlib import ExitStack
        with ExitStack() as stack:
            mocks = [stack.enter_context(p) for p in patches]
            hd_mock = mocks[-3]
            hd_mock.return_value.start = MagicMock()
            hd_mock.return_value.stop = MagicMock()
            hd_mock.return_value.killed = False
            sm._dispatch_tester(1671, [], cfg=cfg, sprint_label="sprint-105")

        assert "CCPROXY_PROFILE" not in captured.get("env", {}), (
            "CCPROXY_PROFILE must NOT be injected when tester_profile is absent and env has none"
        )


# ── AC5 + AC9: fallback to global CCPROXY_PROFILE from environment ───────────

class TestAC5GlobalFallback:
    def test_coder_inherits_global_ccproxy_profile_when_no_override(self, tmp_path, monkeypatch):
        """AC5/AC9: when coder_profile is absent, global CCPROXY_PROFILE is preserved in sub_env."""
        monkeypatch.setenv("CCPROXY_PROFILE", "anthropic")
        cfg = _minimal_cfg(tmp_path)
        cfg.worktree_coder = tmp_path / "coder"
        cfg.logs_dir = tmp_path / "logs"
        cfg.logs_dir.mkdir()

        captured: dict = {}

        with patch("subprocess.Popen", side_effect=_popen_capture(captured)), \
             patch.object(sm, "_post_agent_event"), \
             patch.object(sm, "_load_agent_persona", return_value=None), \
             patch.object(sm, "_dispatch_doctor", return_value=None), \
             patch.object(sm, "_design_docs_guard", return_value=None), \
             patch.object(sm, "_worktree_hygiene", return_value=(None, None, None)), \
             patch.object(sm, "_crg_update_worktree"), \
             patch.object(sm, "_issue_log_path", return_value=tmp_path / "logs" / "1671.log"), \
             patch.object(sm, "_load_estimate", return_value=None), \
             patch.object(sm, "_build_estimate_paths_block", return_value=""), \
             patch.object(sm, "_build_design_block", return_value=""), \
             patch.object(sm, "_impeccable_context_instruction", return_value=""), \
             patch.object(sm, "_build_failure_suffix", return_value=""), \
             patch("services.sprint_manager.dispatch._fetch_dispatch_issue_body", return_value=None), \
             patch.object(sm, "HangDetector") as mock_hd, \
             patch.object(sm, "_db_update_worktree_shas_sm"):
            mock_hd.return_value.start = MagicMock()
            mock_hd.return_value.stop = MagicMock()
            mock_hd.return_value.killed = False
            sm._dispatch_coder(1671, [], cfg=cfg, sprint_label="sprint-105")

        assert captured.get("env", {}).get("CCPROXY_PROFILE") == "anthropic", (
            "Global CCPROXY_PROFILE must be preserved in coder subprocess env when no per-role override"
        )

    def test_tester_inherits_global_ccproxy_profile_when_no_override(self, tmp_path, monkeypatch):
        """AC5/AC9: when tester_profile is absent, global CCPROXY_PROFILE is preserved."""
        monkeypatch.setenv("CCPROXY_PROFILE", "ica")
        cfg = _minimal_cfg(tmp_path)
        cfg.worktree_tester = tmp_path / "tester"
        cfg.tester_app_subdir = ""
        cfg.logs_dir = tmp_path / "logs"
        cfg.logs_dir.mkdir()

        captured: dict = {}

        with patch("subprocess.Popen", side_effect=_popen_capture(captured)), \
             patch.object(sm, "_post_agent_event"), \
             patch.object(sm, "_load_agent_persona", return_value=None), \
             patch.object(sm, "_worktree_hygiene", return_value=(None, None, None)), \
             patch.object(sm, "_crg_update_worktree"), \
             patch.object(sm, "_issue_log_path", return_value=tmp_path / "logs" / "1671.log"), \
             patch.object(sm, "_impeccable_context_instruction", return_value=""), \
             patch.object(sm, "_get_issue_labels", return_value=[]), \
             patch.object(sm, "_classify_risk_tier", return_value="LOW"), \
             patch.object(sm, "_resolve_uat_env_for_tester", return_value=({}, None)), \
             patch.object(sm, "agent_browser_runner"), \
             patch.object(sm, "HangDetector") as mock_hd, \
             patch.object(sm, "_db_update_worktree_shas_sm"):
            mock_hd.return_value.start = MagicMock()
            mock_hd.return_value.stop = MagicMock()
            mock_hd.return_value.killed = False
            sm._dispatch_tester(1671, [], cfg=cfg, sprint_label="sprint-105")

        assert captured.get("env", {}).get("CCPROXY_PROFILE") == "ica", (
            "Global CCPROXY_PROFILE must be preserved in tester subprocess env when no per-role override"
        )

    def test_coder_profile_overrides_global(self, tmp_path, monkeypatch):
        """AC5: per-role coder_profile overrides the global CCPROXY_PROFILE."""
        monkeypatch.setenv("CCPROXY_PROFILE", "global-default")
        cfg = _minimal_cfg(
            tmp_path,
            "agent_config:\n  coder:\n    profile: anthropic",
        )
        cfg.worktree_coder = tmp_path / "coder"
        cfg.logs_dir = tmp_path / "logs"
        cfg.logs_dir.mkdir()

        captured: dict = {}

        with patch("subprocess.Popen", side_effect=_popen_capture(captured)), \
             patch.object(sm, "_post_agent_event"), \
             patch.object(sm, "_load_agent_persona", return_value=None), \
             patch.object(sm, "_dispatch_doctor", return_value=None), \
             patch.object(sm, "_design_docs_guard", return_value=None), \
             patch.object(sm, "_worktree_hygiene", return_value=(None, None, None)), \
             patch.object(sm, "_crg_update_worktree"), \
             patch.object(sm, "_issue_log_path", return_value=tmp_path / "logs" / "1671.log"), \
             patch.object(sm, "_load_estimate", return_value=None), \
             patch.object(sm, "_build_estimate_paths_block", return_value=""), \
             patch.object(sm, "_build_design_block", return_value=""), \
             patch.object(sm, "_impeccable_context_instruction", return_value=""), \
             patch.object(sm, "_build_failure_suffix", return_value=""), \
             patch("services.sprint_manager.dispatch._fetch_dispatch_issue_body", return_value=None), \
             patch.object(sm, "HangDetector") as mock_hd, \
             patch.object(sm, "_db_update_worktree_shas_sm"):
            mock_hd.return_value.start = MagicMock()
            mock_hd.return_value.stop = MagicMock()
            mock_hd.return_value.killed = False
            sm._dispatch_coder(1671, [], cfg=cfg, sprint_label="sprint-105")

        assert captured.get("env", {}).get("CCPROXY_PROFILE") == "anthropic", (
            "Per-role coder_profile must override the global CCPROXY_PROFILE"
        )


# ── AC6: no other dispatch paths affected ─────────────────────────────────────

class TestAC6OtherDispatchPathsUnaffected:
    def test_dispatch_py_does_not_inject_profile_for_reviewer(self):
        """AC6: dispatch.py must not call get_role_profile with 'reviewer' role."""
        dispatch_path = REPO_ROOT / "services" / "sprint_manager" / "dispatch.py"
        source = dispatch_path.read_text(encoding="utf-8")
        # The only get_role_profile calls should reference 'coder' or 'tester'
        import re
        calls = re.findall(r'get_role_profile\s*\(\s*["\'](\w+)["\']', source)
        for role in calls:
            assert role in ("coder", "tester"), (
                f"get_role_profile must only be called for 'coder' or 'tester', got {role!r}"
            )

    def test_dispatch_py_calls_get_role_profile_for_both_roles(self):
        """AC6: dispatch.py calls get_role_profile for both 'coder' and 'tester'."""
        dispatch_path = REPO_ROOT / "services" / "sprint_manager" / "dispatch.py"
        source = dispatch_path.read_text(encoding="utf-8")
        import re
        calls = re.findall(r'get_role_profile\s*\(\s*["\'](\w+)["\']', source)
        assert "coder" in calls, "dispatch.py must call get_role_profile('coder', ...)"
        assert "tester" in calls, "dispatch.py must call get_role_profile('tester', ...)"
