"""Tests for issue #700: agent models configurable via sprint.yaml.

AC1: sprint_manager reads agent_config.default_model → fallback for all agents
AC2: per-agent overrides (coder_model etc.) take precedence over default_model
AC3: missing per-agent key falls back to default_model
AC4: missing default_model falls back to hardcoded values
AC5: changing only default_model affects all agents
AC6: coder_model override isolates to coder
AC7: no agent_config section → hardcoded defaults
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

# Current hardcoded defaults (must not change when no agent_config present)
HARDCODED = {
    "coder_model":      "claude-sonnet-4-6",
    "tester_model":     "claude-sonnet-4-6",
    "reviewer_model":   "claude-haiku-4-5",
    "estimator_model":  "claude-sonnet-4-6",
    "documentor_model": "claude-sonnet-4-6",
}


def _make_sprint_yaml(tmp_path: Path, extra: str = "") -> Path:
    """Return a valid sprint.yaml with real tmp worktree paths."""
    coder_wt = tmp_path / "coder"
    tester_wt = tmp_path / "tester"
    coder_wt.mkdir()
    tester_wt.mkdir()
    yaml_path = tmp_path / "sprint.yaml"
    yaml_path.write_text(
        f"repo_name: owner/testrepo\n"
        f"worktrees:\n"
        f"  coder: {coder_wt}\n"
        f"  tester: {tester_wt}\n"
        + extra,
        encoding="utf-8",
    )
    return yaml_path


def _load(yaml_path: Path):
    import services.sprint_manager.sprint_manager as sm
    return sm.load_config(yaml_path)


# ── AC7: no agent_config → hardcoded defaults ──────────────────────────────

def test_no_agent_config_uses_hardcoded_defaults(tmp_path):
    """AC7: sprint.yaml with no agent_config section uses hardcoded defaults."""
    cfg = _load(_make_sprint_yaml(tmp_path))
    for field, expected in HARDCODED.items():
        assert getattr(cfg, field) == expected, (
            f"{field}: expected hardcoded {expected!r}, got {getattr(cfg, field)!r}"
        )


# ── AC1/AC5: default_model fallback ────────────────────────────────────────

def test_default_model_sets_all_agents(tmp_path):
    """AC1/AC5: default_model in agent_config applies to all 5 agents."""
    yaml_path = _make_sprint_yaml(
        tmp_path,
        "agent_config:\n  default_model: claude-haiku-4-5\n",
    )
    cfg = _load(yaml_path)
    for field in HARDCODED:
        assert getattr(cfg, field) == "claude-haiku-4-5", (
            f"{field}: expected default_model 'claude-haiku-4-5', got {getattr(cfg, field)!r}"
        )


def test_default_model_different_value(tmp_path):
    """AC1: changing default_model causes all agents to use the new model."""
    yaml_path = _make_sprint_yaml(
        tmp_path,
        "agent_config:\n  default_model: claude-opus-4-8\n",
    )
    cfg = _load(yaml_path)
    for field in HARDCODED:
        assert getattr(cfg, field) == "claude-opus-4-8", (
            f"{field}: expected 'claude-opus-4-8', got {getattr(cfg, field)!r}"
        )


# ── AC2/AC3: per-agent overrides ───────────────────────────────────────────

def test_per_agent_override_takes_precedence_over_default_model(tmp_path):
    """AC2: per-agent override trumps default_model."""
    yaml_path = _make_sprint_yaml(
        tmp_path,
        "agent_config:\n"
        "  default_model: claude-haiku-4-5\n"
        "  coder_model: claude-sonnet-4-6\n",
    )
    cfg = _load(yaml_path)
    assert cfg.coder_model == "claude-sonnet-4-6", "coder_model override not respected"
    # other agents fall back to default_model
    assert cfg.tester_model == "claude-haiku-4-5"
    assert cfg.reviewer_model == "claude-haiku-4-5"
    assert cfg.estimator_model == "claude-haiku-4-5"
    assert cfg.documentor_model == "claude-haiku-4-5"


def test_absent_per_agent_key_falls_back_to_default_model(tmp_path):
    """AC3: when per-agent key absent, falls back to default_model."""
    yaml_path = _make_sprint_yaml(
        tmp_path,
        "agent_config:\n  default_model: claude-opus-4-8\n",
    )
    cfg = _load(yaml_path)
    for field in HARDCODED:
        assert getattr(cfg, field) == "claude-opus-4-8", (
            f"{field}: expected default_model fallback 'claude-opus-4-8', "
            f"got {getattr(cfg, field)!r}"
        )


def test_all_per_agent_overrides_respected(tmp_path):
    """AC2: all 5 per-agent overrides can be set independently."""
    yaml_path = _make_sprint_yaml(
        tmp_path,
        "agent_config:\n"
        "  default_model: claude-haiku-4-5\n"
        "  coder_model: model-coder\n"
        "  tester_model: model-tester\n"
        "  reviewer_model: model-reviewer\n"
        "  estimator_model: model-estimator\n"
        "  documentor_model: model-documentor\n",
    )
    cfg = _load(yaml_path)
    assert cfg.coder_model == "model-coder"
    assert cfg.tester_model == "model-tester"
    assert cfg.reviewer_model == "model-reviewer"
    assert cfg.estimator_model == "model-estimator"
    assert cfg.documentor_model == "model-documentor"


# ── AC4: absent default_model → hardcoded ──────────────────────────────────

def test_per_agent_set_but_no_default_model_others_use_hardcoded(tmp_path):
    """AC4: per-agent override works; agents without override use hardcoded (no default_model)."""
    yaml_path = _make_sprint_yaml(
        tmp_path,
        "agent_config:\n  coder_model: claude-opus-4-8\n",
    )
    cfg = _load(yaml_path)
    assert cfg.coder_model == "claude-opus-4-8"
    # No default_model, no per-agent keys for others → hardcoded
    assert cfg.tester_model == HARDCODED["tester_model"]
    assert cfg.reviewer_model == HARDCODED["reviewer_model"]
    assert cfg.estimator_model == HARDCODED["estimator_model"]
    assert cfg.documentor_model == HARDCODED["documentor_model"]


def test_configured_model_is_used_not_silently_discarded(tmp_path):
    """AC4/UAT4: a configured model string (even unusual) is used as-is, not silently fallen back."""
    yaml_path = _make_sprint_yaml(
        tmp_path,
        "agent_config:\n  default_model: future-model-3000\n",
    )
    cfg = _load(yaml_path)
    # Must use the configured value, not the hardcoded default
    assert cfg.coder_model == "future-model-3000", (
        "Configured default_model must be used, not silently discarded"
    )
    assert cfg.tester_model == "future-model-3000"


# ── AC6: coder_model isolates to coder only ────────────────────────────────

def test_coder_model_override_does_not_affect_other_agents(tmp_path):
    """AC6: setting coder_model different from default_model affects only coder."""
    yaml_path = _make_sprint_yaml(
        tmp_path,
        "agent_config:\n"
        "  default_model: claude-haiku-4-5\n"
        "  coder_model: claude-sonnet-4-6\n",
    )
    cfg = _load(yaml_path)
    assert cfg.coder_model == "claude-sonnet-4-6"
    assert cfg.tester_model == "claude-haiku-4-5"
    assert cfg.reviewer_model == "claude-haiku-4-5"
    assert cfg.estimator_model == "claude-haiku-4-5"
    assert cfg.documentor_model == "claude-haiku-4-5"


# ── Structural: SprintConfig must expose model fields ──────────────────────

def test_sprint_config_has_all_model_fields():
    """SprintConfig dataclass must have all 5 agent model fields."""
    import services.sprint_manager.sprint_manager as sm
    src = inspect.getsource(sm.SprintConfig)
    for field in HARDCODED:
        assert field in src, f"SprintConfig must declare {field!r}"


# ── Dispatch sites: sprint_manager must not have bare hardcoded model strings

def test_dispatch_coder_uses_cfg_model_not_hardcoded():
    """sprint_manager._dispatch_coder must read model from cfg, not a bare literal."""
    import services.sprint_manager.sprint_manager as sm
    src = inspect.getsource(sm._dispatch_coder)
    assert "coder_model" in src, "_dispatch_coder must reference cfg.coder_model"


def test_dispatch_tester_uses_cfg_model_not_hardcoded():
    """sprint_manager._dispatch_tester must read model from cfg, not a bare literal."""
    import services.sprint_manager.sprint_manager as sm
    src = inspect.getsource(sm._dispatch_tester)
    assert "tester_model" in src, "_dispatch_tester must reference cfg.tester_model"


# ── settings_schema: reviewer_model + documentor_model present ─────────────

def test_settings_schema_has_reviewer_model():
    """settings_schema.KNOWN_FIELDS must include reviewer_model."""
    from services.sprint_manager.settings_schema import KNOWN_FIELDS
    assert "reviewer_model" in KNOWN_FIELDS, (
        "settings_schema.KNOWN_FIELDS must include reviewer_model"
    )


def test_settings_schema_has_documentor_model():
    """settings_schema.KNOWN_FIELDS must include documentor_model."""
    from services.sprint_manager.settings_schema import KNOWN_FIELDS
    assert "documentor_model" in KNOWN_FIELDS, (
        "settings_schema.KNOWN_FIELDS must include documentor_model"
    )
