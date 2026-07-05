"""COMMANDER_ICA_ROLES scopes apply_provider_env to specific agent roles."""
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
for _p in (str(_REPO), str(_REPO / "services" / "sprint_manager")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.sprint_manager import model_routing as mr


@pytest.fixture()
def force_ica(monkeypatch):
    monkeypatch.setattr(mr, "get_effective_llm_provider", lambda *a, **k: "ica")


def test_unset_roles_all_route(monkeypatch, force_ica):
    monkeypatch.delenv("COMMANDER_ICA_ROLES", raising=False)
    env = {}
    model = mr.apply_provider_env(env, "claude-haiku-4-5", role="documenter")
    assert model == mr.ICA_FORCED_MODEL
    assert env.get("CCPROXY_PROFILE") == "ica"


def test_scoped_roles_allowed_role_routes(monkeypatch, force_ica):
    monkeypatch.setenv("COMMANDER_ICA_ROLES", "coder,tester,estimator")
    env = {}
    model = mr.apply_provider_env(env, "claude-haiku-4-5", role="estimator")
    assert model == mr.ICA_FORCED_MODEL
    assert env.get("CCPROXY_PROFILE") == "ica"


def test_scoped_roles_other_role_stays_anthropic(monkeypatch, force_ica):
    monkeypatch.setenv("COMMANDER_ICA_ROLES", "coder,tester")
    env = {}
    model = mr.apply_provider_env(env, "claude-haiku-4-5", role="documenter")
    assert model == "claude-haiku-4-5"
    assert "CCPROXY_PROFILE" not in env
    assert "ANTHROPIC_BASE_URL" not in env


def test_scoped_roles_no_role_stays_anthropic(monkeypatch, force_ica):
    monkeypatch.setenv("COMMANDER_ICA_ROLES", "coder,tester")
    env = {}
    model = mr.apply_provider_env(env, "claude-haiku-4-5")
    assert model == "claude-haiku-4-5"
    assert "CCPROXY_PROFILE" not in env


def test_role_matching_case_insensitive(monkeypatch, force_ica):
    monkeypatch.setenv("COMMANDER_ICA_ROLES", "Coder, TESTER")
    env = {}
    model = mr.apply_provider_env(env, "claude-haiku-4-5", role="tester")
    assert model == mr.ICA_FORCED_MODEL


def test_anthropic_provider_ignores_roles(monkeypatch):
    monkeypatch.setattr(mr, "get_effective_llm_provider", lambda *a, **k: "anthropic")
    monkeypatch.setenv("COMMANDER_ICA_ROLES", "coder")
    env = {}
    model = mr.apply_provider_env(env, "claude-haiku-4-5", role="coder")
    assert model == "claude-haiku-4-5"
    assert "CCPROXY_PROFILE" not in env
