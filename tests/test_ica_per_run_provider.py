"""Per-run LLM provider routing (run modal → plan.json → agent env).

Covers the commander↔claude-proxy integration follow-up to #1667/#1668/#1671:

- _plan_json_llm_provider reads the run modal's choice from plan.json.
- get_effective_llm_provider resolution: plan.json > global setting > anthropic.
- apply_ica_agent_env injects ANTHROPIC_BASE_URL / ANTHROPIC_CUSTOM_HEADERS /
  CCPROXY_PROFILE (telemetry) / dummy auth, strips the metered key, and
  respects an existing per-role profile (#1671 precedence).
- check_ica_readiness requests /health?profile=ica so the preflight checks
  the profile the run will actually use.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))

from services.sprint_manager import model_routing  # noqa: E402
from services.sprint_manager import ica_preflight  # noqa: E402


@pytest.fixture
def plan_dir(tmp_path, monkeypatch):
    """Point plan.json resolution at a temp sprints dir."""
    sprints = tmp_path / "sprints"
    sprints.mkdir()

    def _fake_plan_path(sprint_label, cfg=None):
        return sprints / f"{sprint_label}-plan.json"

    monkeypatch.setattr(model_routing, "_plan_json_path", _fake_plan_path)
    return sprints


def _write_plan(plan_dir: Path, label: str, **fields) -> None:
    (plan_dir / f"{label}-plan.json").write_text(json.dumps(fields))


# ── _plan_json_llm_provider ───────────────────────────────────────────────────

def test_plan_provider_read(plan_dir):
    _write_plan(plan_dir, "sprint-9", llm_provider="ica")
    assert model_routing._plan_json_llm_provider("sprint-9") == "ica"


def test_plan_provider_absent_returns_none(plan_dir):
    _write_plan(plan_dir, "sprint-9", state="running")
    assert model_routing._plan_json_llm_provider("sprint-9") is None


def test_plan_provider_missing_file_returns_none(plan_dir):
    assert model_routing._plan_json_llm_provider("sprint-404") is None


# ── get_effective_llm_provider resolution chain ───────────────────────────────

def test_effective_plan_json_wins_over_global(plan_dir, monkeypatch):
    _write_plan(plan_dir, "sprint-9", llm_provider="ica")
    fake_sr = MagicMock()
    fake_sr.get_setting.return_value = {"llmProvider": "anthropic"}
    monkeypatch.setitem(sys.modules, "settings_repo", fake_sr)
    assert model_routing.get_effective_llm_provider("sprint-9", None, "o/r") == "ica"


def test_effective_falls_back_to_global(plan_dir, monkeypatch):
    _write_plan(plan_dir, "sprint-9", state="running")  # no llm_provider field
    fake_sr = MagicMock()
    fake_sr.get_setting.return_value = {"llmProvider": "ica"}
    monkeypatch.setitem(sys.modules, "settings_repo", fake_sr)
    assert model_routing.get_effective_llm_provider("sprint-9", None, "o/r") == "ica"


def test_effective_default_anthropic(plan_dir, monkeypatch):
    fake_sr = MagicMock()
    fake_sr.get_setting.return_value = {}
    monkeypatch.setitem(sys.modules, "settings_repo", fake_sr)
    assert model_routing.get_effective_llm_provider("sprint-9", None, "o/r") == "anthropic"


def test_effective_settings_error_defaults_anthropic(plan_dir, monkeypatch):
    fake_sr = MagicMock()
    fake_sr.get_setting.side_effect = RuntimeError("no settings store")
    monkeypatch.setitem(sys.modules, "settings_repo", fake_sr)
    assert model_routing.get_effective_llm_provider("sprint-9", None, "o/r") == "anthropic"


# ── apply_ica_agent_env ───────────────────────────────────────────────────────

def test_apply_ica_env_sets_routing_and_strips_key(monkeypatch):
    monkeypatch.delenv("COMMANDER_PROXY_URL", raising=False)
    sub_env = {
        "ANTHROPIC_API_KEY": "sk-real-metered-key",
        "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-real-subscription-token",
    }
    model_routing.apply_ica_agent_env(sub_env)
    assert sub_env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8788"
    assert sub_env["ANTHROPIC_CUSTOM_HEADERS"] == "X-CCProxy-Profile: ica"
    assert sub_env["CCPROXY_PROFILE"] == "ica"
    assert "ANTHROPIC_API_KEY" not in sub_env, "metered key must never reach the proxy path"
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in sub_env, (
        "subscription OAuth token must be stripped or the CLI validates the "
        "model against api.anthropic.com and the ICA-only model id fails"
    )
    assert sub_env["ANTHROPIC_AUTH_TOKEN"] == "commander-ica-proxy"


def test_apply_ica_env_honors_commander_proxy_url(monkeypatch):
    monkeypatch.setenv("COMMANDER_PROXY_URL", "http://127.0.0.1:9999/")
    sub_env: dict = {}
    model_routing.apply_ica_agent_env(sub_env)
    assert sub_env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:9999"


def test_apply_ica_env_respects_per_role_profile(monkeypatch):
    """#1671 precedence: an existing per-role CCPROXY_PROFILE wins for both the
    env var (telemetry) and the per-request header (routing)."""
    monkeypatch.delenv("COMMANDER_PROXY_URL", raising=False)
    sub_env = {"CCPROXY_PROFILE": "ica-tester-special"}
    model_routing.apply_ica_agent_env(sub_env, "ica")
    assert sub_env["CCPROXY_PROFILE"] == "ica-tester-special"
    assert sub_env["ANTHROPIC_CUSTOM_HEADERS"] == "X-CCProxy-Profile: ica-tester-special"


# ── preflight ?profile= ───────────────────────────────────────────────────────

def test_preflight_requests_profile_scoped_health(monkeypatch):
    captured: dict = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"model_map": {"claude-sonnet-4-6": "x", "claude-haiku-4-5": "y"}}

    def _fake_get(url, timeout):
        captured["url"] = url
        return _Resp()

    monkeypatch.setattr(ica_preflight.httpx, "get", _fake_get)
    ica_preflight.check_ica_readiness(proxy_url="http://127.0.0.1:8788")
    assert captured["url"] == "http://127.0.0.1:8788/health?profile=ica"


def test_preflight_missing_model_raises(monkeypatch):
    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"model_map": {"claude-sonnet-4-6": "x"}}

    monkeypatch.setattr(ica_preflight.httpx, "get", lambda url, timeout: _Resp())
    with pytest.raises(ica_preflight.IcaPreflightError, match="claude-haiku-4-5"):
        ica_preflight.check_ica_readiness(proxy_url="http://127.0.0.1:8788")
