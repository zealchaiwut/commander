"""Tests for ICA lean-MCP dispatch args (payload-size fix).

Context: Commander dispatches Claude Code agents through claude-proxy to IBM
ICA, which has no prompt caching and a 180s first-token timeout. Every MCP
server attached at user scope adds its full tool-definition set to every
request. This is a large fixed cost that ICA pays on every turn (no cache to
amortize it against), and has caused first-token timeouts.

Fix: gate a `--strict-mcp-config` (+ optional `--mcp-config`) argument pair
behind COMMANDER_ICA_LEAN_MCP=1, applied ONLY to ICA-routed claude CLI
dispatches (coder + tester), inserted immediately after
--dangerously-skip-permissions.

Covers:
  - model_routing.ica_lean_cli_args(): flag off/on, with/without
    COMMANDER_ICA_MCP_JSON.
  - dispatch._dispatch_coder / _dispatch_tester: flag off → cmd unchanged;
    flag on + ICA provider → args present; flag on + anthropic provider →
    args absent.
"""
from __future__ import annotations

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

from services.sprint_manager import model_routing  # noqa: E402
from services.sprint_manager import dispatch  # noqa: E402
import services.sprint_manager.sprint_manager as sm  # noqa: E402


# ── ica_lean_cli_args() unit tests ────────────────────────────────────────────

def test_flag_unset_returns_empty(monkeypatch):
    monkeypatch.delenv("COMMANDER_ICA_LEAN_MCP", raising=False)
    monkeypatch.delenv("COMMANDER_ICA_MCP_JSON", raising=False)
    assert model_routing.ica_lean_cli_args() == []


def test_flag_off_returns_empty(monkeypatch):
    monkeypatch.setenv("COMMANDER_ICA_LEAN_MCP", "0")
    monkeypatch.delenv("COMMANDER_ICA_MCP_JSON", raising=False)
    assert model_routing.ica_lean_cli_args() == []


def test_flag_on_no_json_returns_strict_only(monkeypatch):
    monkeypatch.setenv("COMMANDER_ICA_LEAN_MCP", "1")
    monkeypatch.delenv("COMMANDER_ICA_MCP_JSON", raising=False)
    assert model_routing.ica_lean_cli_args() == ["--strict-mcp-config"]


def test_flag_on_with_json_appends_mcp_config(monkeypatch):
    monkeypatch.setenv("COMMANDER_ICA_LEAN_MCP", "1")
    monkeypatch.setenv("COMMANDER_ICA_MCP_JSON", '{"mcpServers": {}}')
    assert model_routing.ica_lean_cli_args() == [
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers": {}}',
    ]


# ── dispatch integration: coder ───────────────────────────────────────────────

@pytest.fixture
def coder_cfg(tmp_path):
    coder_dir = tmp_path / "coder"
    coder_dir.mkdir()
    (coder_dir / "PRODUCT.md").write_text("product")
    (coder_dir / "DESIGN.md").write_text("design")

    cfg = MagicMock()
    cfg.coder_model = "claude-sonnet-4-6"
    cfg.coder_by_size = None
    cfg.coder_prompt_template = None
    cfg.repo_name = "test/repo"
    cfg.api_url = None
    cfg.worktree_coder = coder_dir
    cfg.logs_dir = tmp_path / "logs"
    cfg.logs_dir.mkdir()
    cfg.coder_backend = "claude-code"
    cfg.use_cline_followups = False
    cfg.coder_profile = None
    cfg.tester_profile = None
    return cfg


def _capture_popen():
    captured_cmd: list = []

    def fake_popen(cmd, **kwargs):
        captured_cmd.extend(cmd)
        proc = MagicMock()
        proc.wait.return_value = 0
        return proc

    return captured_cmd, fake_popen


def test_dispatch_coder_flag_off_cmd_unchanged(coder_cfg, monkeypatch):
    monkeypatch.delenv("COMMANDER_ICA_LEAN_MCP", raising=False)
    captured_cmd, fake_popen = _capture_popen()

    with patch("subprocess.Popen", side_effect=fake_popen), \
         patch.object(sm, "_post_agent_event"), \
         patch.object(sm, "_load_agent_persona", return_value=None), \
         patch.object(sm, "_load_estimate", return_value=None), \
         patch.object(sm, "_dispatch_doctor", return_value=None), \
         patch.object(sm, "_worktree_hygiene", return_value=(None, None, None)), \
         patch.object(dispatch, "get_effective_llm_provider", return_value="anthropic"):
        sm._dispatch_coder(
            1, [], sprint_branch="develop", repo_name="test/repo", cfg=coder_cfg,
            sprint_label="sprint-1",
        )

    assert "--strict-mcp-config" not in captured_cmd
    assert "--dangerously-skip-permissions" in captured_cmd


def test_dispatch_coder_flag_on_ica_adds_args(coder_cfg, monkeypatch):
    monkeypatch.setenv("COMMANDER_ICA_LEAN_MCP", "1")
    monkeypatch.delenv("COMMANDER_ICA_MCP_JSON", raising=False)
    captured_cmd, fake_popen = _capture_popen()

    with patch("subprocess.Popen", side_effect=fake_popen), \
         patch.object(sm, "_post_agent_event"), \
         patch.object(sm, "_load_agent_persona", return_value=None), \
         patch.object(sm, "_load_estimate", return_value=None), \
         patch.object(sm, "_dispatch_doctor", return_value=None), \
         patch.object(sm, "_worktree_hygiene", return_value=(None, None, None)), \
         patch.object(dispatch, "get_effective_llm_provider", return_value="ica"), \
         patch.object(dispatch, "apply_ica_agent_env", lambda *a, **k: None):
        sm._dispatch_coder(
            1, [], sprint_branch="develop", repo_name="test/repo", cfg=coder_cfg,
            sprint_label="sprint-1",
        )

    assert "--strict-mcp-config" in captured_cmd
    # Must be inserted immediately after --dangerously-skip-permissions.
    perm_idx = captured_cmd.index("--dangerously-skip-permissions")
    assert captured_cmd[perm_idx + 1] == "--strict-mcp-config"


def test_dispatch_coder_flag_on_non_ica_omits_args(coder_cfg, monkeypatch):
    monkeypatch.setenv("COMMANDER_ICA_LEAN_MCP", "1")
    captured_cmd, fake_popen = _capture_popen()

    with patch("subprocess.Popen", side_effect=fake_popen), \
         patch.object(sm, "_post_agent_event"), \
         patch.object(sm, "_load_agent_persona", return_value=None), \
         patch.object(sm, "_load_estimate", return_value=None), \
         patch.object(sm, "_dispatch_doctor", return_value=None), \
         patch.object(sm, "_worktree_hygiene", return_value=(None, None, None)), \
         patch.object(dispatch, "get_effective_llm_provider", return_value="anthropic"):
        sm._dispatch_coder(
            1, [], sprint_branch="develop", repo_name="test/repo", cfg=coder_cfg,
            sprint_label="sprint-1",
        )

    assert "--strict-mcp-config" not in captured_cmd


# ── dispatch integration: tester ──────────────────────────────────────────────

@pytest.fixture
def tester_cfg(tmp_path):
    tester_app_dir = tmp_path / "tester_app"
    tester_app_dir.mkdir()
    tester_root_dir = tmp_path / "tester_root"
    tester_root_dir.mkdir()

    cfg = MagicMock()
    cfg.tester_model = "claude-sonnet-4-6"
    cfg.tester_by_risk = None
    cfg.tester_prompt_template = None
    cfg.repo_name = "test/repo"
    cfg.api_url = None
    cfg.worktree_tester_app = tester_app_dir
    cfg.worktree_tester = tester_root_dir
    cfg.logs_dir = tmp_path / "logs"
    cfg.logs_dir.mkdir()
    cfg.coder_profile = None
    cfg.tester_profile = None
    return cfg


def test_dispatch_tester_flag_off_cmd_unchanged(tester_cfg, monkeypatch):
    monkeypatch.delenv("COMMANDER_ICA_LEAN_MCP", raising=False)
    captured_cmd, fake_popen = _capture_popen()

    with patch("subprocess.Popen", side_effect=fake_popen), \
         patch.object(sm, "_post_agent_event"), \
         patch.object(sm, "_load_agent_persona", return_value=None), \
         patch.object(sm, "_worktree_hygiene", return_value=(None, None, None)), \
         patch.object(dispatch, "_get_issue_labels", return_value=[]), \
         patch.object(dispatch, "_resolve_uat_env_for_tester", return_value=({}, None)), \
         patch.object(dispatch, "get_effective_llm_provider", return_value="anthropic"):
        sm._dispatch_tester(
            1, [], sprint_branch="develop", repo_name="test/repo", cfg=tester_cfg,
            sprint_label="sprint-1",
        )

    assert "--strict-mcp-config" not in captured_cmd
    assert "--dangerously-skip-permissions" in captured_cmd


def test_dispatch_tester_flag_on_ica_adds_args(tester_cfg, monkeypatch):
    monkeypatch.setenv("COMMANDER_ICA_LEAN_MCP", "1")
    monkeypatch.delenv("COMMANDER_ICA_MCP_JSON", raising=False)
    captured_cmd, fake_popen = _capture_popen()

    with patch("subprocess.Popen", side_effect=fake_popen), \
         patch.object(sm, "_post_agent_event"), \
         patch.object(sm, "_load_agent_persona", return_value=None), \
         patch.object(sm, "_worktree_hygiene", return_value=(None, None, None)), \
         patch.object(dispatch, "_get_issue_labels", return_value=[]), \
         patch.object(dispatch, "_resolve_uat_env_for_tester", return_value=({}, None)), \
         patch.object(dispatch, "get_effective_llm_provider", return_value="ica"), \
         patch.object(dispatch, "apply_ica_agent_env", lambda *a, **k: None):
        sm._dispatch_tester(
            1, [], sprint_branch="develop", repo_name="test/repo", cfg=tester_cfg,
            sprint_label="sprint-1",
        )

    assert "--strict-mcp-config" in captured_cmd
    perm_idx = captured_cmd.index("--dangerously-skip-permissions")
    assert captured_cmd[perm_idx + 1] == "--strict-mcp-config"


def test_dispatch_tester_flag_on_non_ica_omits_args(tester_cfg, monkeypatch):
    monkeypatch.setenv("COMMANDER_ICA_LEAN_MCP", "1")
    captured_cmd, fake_popen = _capture_popen()

    with patch("subprocess.Popen", side_effect=fake_popen), \
         patch.object(sm, "_post_agent_event"), \
         patch.object(sm, "_load_agent_persona", return_value=None), \
         patch.object(sm, "_worktree_hygiene", return_value=(None, None, None)), \
         patch.object(dispatch, "_get_issue_labels", return_value=[]), \
         patch.object(dispatch, "_resolve_uat_env_for_tester", return_value=({}, None)), \
         patch.object(dispatch, "get_effective_llm_provider", return_value="anthropic"):
        sm._dispatch_tester(
            1, [], sprint_branch="develop", repo_name="test/repo", cfg=tester_cfg,
            sprint_label="sprint-1",
        )

    assert "--strict-mcp-config" not in captured_cmd
