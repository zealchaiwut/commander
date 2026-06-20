"""Tests for issue #1403: Route docs-only and config-only tickets to Haiku.

AC items verified:
  AC1  docs-only risk flag → Haiku (unless XL); routing_reason = "docs-only:flag"
  AC2  All docs/config paths → Haiku (unless XL); routing_reason = "docs-only:paths"
  AC3  size-M + files=[docs/workflow.md, CHANGELOG.md] → Haiku
  AC4  Mixed paths (server.py + docs/workflow.md) → falls through to size routing
  AC5  XL + docs-only heuristic → Sonnet (XL override, not rerouted)
  AC6  routing_reason persisted on agent_runs via IssueState.coder_routing_reason
  AC7  IssueState.to_dict() includes coder_routing_reason (frontend badge receives it)
  AC8  _is_docs_only is isolated as standalone hook point
  AC9  Existing _resolve_coder_model tests continue to pass
"""
from __future__ import annotations

import os
import sys
import textwrap
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SM_DIR = REPO_ROOT / "services" / "sprint_manager"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))
sys.path.insert(0, str(SM_DIR))

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))

import services.sprint_manager.sprint_manager as sm  # noqa: E402


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def minimal_cfg(tmp_path):
    """Minimal SprintConfig with default coder_by_size."""
    coder_dir = tmp_path / "coder"
    coder_dir.mkdir()
    tester_dir = tmp_path / "tester"
    tester_dir.mkdir()
    yaml_text = textwrap.dedent(f"""
        repo_name: test/repo
        worktrees:
          coder: {coder_dir}
          tester: {tester_dir}
    """)
    config_path = tmp_path / "sprint.yaml"
    config_path.write_text(yaml_text)
    return sm.load_config(config_path)


# ── AC1: docs-only risk flag → Haiku (unless XL) ─────────────────────────────

def test_docs_only_flag_routes_haiku_for_size_m(minimal_cfg):
    """AC1: docs-only flag on a size-M ticket routes to Haiku."""
    estimate = {"size": "M", "risk_flags": ["docs-only"]}
    model, reason = sm._resolve_coder_model(1, minimal_cfg, estimate=estimate)
    assert "haiku" in model.lower(), f"docs-only flag must override M→sonnet; got {model!r}"
    assert reason == "docs-only:flag"


def test_docs_only_flag_routes_haiku_for_size_l(minimal_cfg):
    """AC1: docs-only flag on a size-L ticket routes to Haiku."""
    estimate = {"size": "L", "risk_flags": ["docs-only"]}
    model, reason = sm._resolve_coder_model(1, minimal_cfg, estimate=estimate)
    assert "haiku" in model.lower(), f"docs-only flag must override L→sonnet; got {model!r}"
    assert reason == "docs-only:flag"


def test_docs_only_flag_routing_reason_is_flag(minimal_cfg):
    """AC1: routing_reason must be exactly 'docs-only:flag' when the risk flag is set."""
    estimate = {"size": "M", "risk_flags": ["docs-only"]}
    _, reason = sm._resolve_coder_model(1, minimal_cfg, estimate=estimate)
    assert reason == "docs-only:flag", f"Expected 'docs-only:flag', got {reason!r}"


def test_docs_only_flag_without_size_routes_haiku(minimal_cfg):
    """AC1: docs-only flag without a size field still routes to Haiku."""
    estimate = {"risk_flags": ["docs-only"]}
    model, reason = sm._resolve_coder_model(1, minimal_cfg, estimate=estimate)
    assert "haiku" in model.lower(), f"docs-only flag with no size must still use haiku; got {model!r}"
    assert reason == "docs-only:flag"


# ── AC2: docs-only paths → Haiku (unless XL) ─────────────────────────────────

def test_docs_only_paths_routes_haiku_for_size_m(minimal_cfg):
    """AC2: all docs paths on a size-M ticket routes to Haiku."""
    estimate = {"size": "M", "files_likely_affected": ["docs/workflow.md", "CHANGELOG.md"]}
    model, reason = sm._resolve_coder_model(1, minimal_cfg, estimate=estimate)
    assert "haiku" in model.lower(), f"docs-only paths must route M to haiku; got {model!r}"
    assert reason == "docs-only:paths"


def test_docs_only_paths_routing_reason_is_paths(minimal_cfg):
    """AC2: routing_reason must be exactly 'docs-only:paths' for path-based routing."""
    estimate = {"size": "M", "files_likely_affected": ["docs/workflow.md", "CHANGELOG.md"]}
    _, reason = sm._resolve_coder_model(1, minimal_cfg, estimate=estimate)
    assert reason == "docs-only:paths", f"Expected 'docs-only:paths', got {reason!r}"


def test_yaml_json_only_paths_routes_haiku(minimal_cfg):
    """AC2: yaml/json-only paths (no code extensions) route to Haiku."""
    estimate = {"size": "M", "files_likely_affected": ["config/settings.yaml", ".env.example.json"]}
    model, reason = sm._resolve_coder_model(1, minimal_cfg, estimate=estimate)
    assert "haiku" in model.lower(), f"yaml/json-only paths should route to haiku; got {model!r}"
    assert reason == "docs-only:paths"


def test_docs_prefix_only_paths_routes_haiku(minimal_cfg):
    """AC2: paths under docs/ (no code extensions) route to Haiku."""
    estimate = {"size": "L", "files_likely_affected": ["docs/architecture.md", "docs/quickstart.md"]}
    model, reason = sm._resolve_coder_model(1, minimal_cfg, estimate=estimate)
    assert "haiku" in model.lower(), f"docs/-only paths should route to haiku; got {model!r}"
    assert reason == "docs-only:paths"


# ── AC3: size-M with specific example from AC ─────────────────────────────────

def test_ac3_size_m_docs_files_dispatches_haiku(minimal_cfg):
    """AC3: size-M, files=[docs/workflow.md, CHANGELOG.md] → Haiku (exact AC example)."""
    estimate = {"size": "M", "files_likely_affected": ["docs/workflow.md", "CHANGELOG.md"]}
    model, reason = sm._resolve_coder_model(1, minimal_cfg, estimate=estimate)
    assert "haiku" in model.lower(), (
        f"size-M + [docs/workflow.md, CHANGELOG.md] must dispatch Haiku; got {model!r}"
    )
    assert "docs-only" in reason, f"routing_reason must indicate docs-only; got {reason!r}"


# ── AC4: mixed paths fall through to size routing ────────────────────────────

def test_mixed_paths_falls_through_to_size_routing(minimal_cfg):
    """AC4: mixed paths (code + docs) fall through to existing size-based routing."""
    estimate = {"size": "M", "files_likely_affected": ["server.py", "docs/workflow.md"]}
    model, reason = sm._resolve_coder_model(1, minimal_cfg, estimate=estimate)
    assert "sonnet" in model.lower(), (
        f"mixed paths must use size routing (M→sonnet); got {model!r}"
    )
    assert reason == "size=M", f"mixed paths must yield size routing reason; got {reason!r}"


def test_single_code_file_in_list_falls_through(minimal_cfg):
    """AC4: even one .py file in the list prevents docs-only routing."""
    estimate = {"size": "L", "files_likely_affected": ["README.md", "requirements.txt.py"]}
    model, reason = sm._resolve_coder_model(1, minimal_cfg, estimate=estimate)
    # requirements.txt.py ends in .py so is a code path → falls through
    assert reason == "size=L", f"presence of .py path must force size routing; got {reason!r}"


def test_ts_file_in_list_falls_through(minimal_cfg):
    """AC4: a .ts file in the list prevents docs-only routing."""
    estimate = {"size": "M", "files_likely_affected": ["docs/guide.md", "src/app.ts"]}
    model, reason = sm._resolve_coder_model(1, minimal_cfg, estimate=estimate)
    assert reason == "size=M", f".ts file must force size routing; got {reason!r}"


# ── AC5: XL override — docs-only not applied to XL ───────────────────────────

def test_xl_with_docs_flag_stays_sonnet(minimal_cfg):
    """AC5: XL ticket with docs-only risk flag is NOT rerouted; stays Sonnet."""
    estimate = {"size": "XL", "risk_flags": ["docs-only"]}
    model, reason = sm._resolve_coder_model(1, minimal_cfg, estimate=estimate)
    assert "sonnet" in model.lower(), (
        f"XL + docs-only flag must stay on Sonnet; got {model!r}"
    )
    assert reason == "size=XL", f"XL must use size routing reason; got {reason!r}"


def test_xl_with_docs_paths_stays_sonnet(minimal_cfg):
    """AC5: XL ticket with all-docs paths is NOT rerouted; stays Sonnet."""
    estimate = {"size": "XL", "files_likely_affected": ["docs/workflow.md"]}
    model, reason = sm._resolve_coder_model(1, minimal_cfg, estimate=estimate)
    assert "sonnet" in model.lower(), (
        f"XL + docs-only paths must stay on Sonnet; got {model!r}"
    )
    assert reason == "size=XL", f"XL must use size routing reason; got {reason!r}"


# ── AC6 & AC7: routing_reason in IssueState ─────────────────────────────────

def test_issue_state_has_coder_routing_reason_field():
    """AC6/AC7: IssueState must have a coder_routing_reason attribute."""
    ist = sm.IssueState(number=1, title="test")
    assert hasattr(ist, "coder_routing_reason"), (
        "IssueState must have coder_routing_reason field"
    )


def test_issue_state_coder_routing_reason_defaults_none():
    """AC6/AC7: coder_routing_reason defaults to None (backward compat)."""
    ist = sm.IssueState(number=1, title="test")
    assert ist.coder_routing_reason is None


def test_issue_state_to_dict_includes_coder_routing_reason():
    """AC7: to_dict() must include coder_routing_reason so the frontend badge receives it."""
    ist = sm.IssueState(number=1, title="test")
    ist.coder_routing_reason = "docs-only:paths"
    d = ist.to_dict()
    assert "coder_routing_reason" in d, (
        "to_dict() must include coder_routing_reason for the frontend"
    )
    assert d["coder_routing_reason"] == "docs-only:paths"


def test_issue_state_from_dict_restores_coder_routing_reason():
    """AC6: from_dict() round-trips coder_routing_reason correctly."""
    d = {
        "number": 42,
        "title": "Test ticket",
        "coder_routing_reason": "docs-only:flag",
    }
    ist = sm.IssueState.from_dict(d)
    assert ist.coder_routing_reason == "docs-only:flag", (
        f"from_dict must restore coder_routing_reason; got {ist.coder_routing_reason!r}"
    )


def test_issue_state_from_dict_routing_reason_null_when_absent():
    """AC6: from_dict() yields None when coder_routing_reason is missing (backward compat)."""
    d = {"number": 1, "title": "old ticket"}
    ist = sm.IssueState.from_dict(d)
    assert ist.coder_routing_reason is None


# ── AC8: _is_docs_only is isolated (hook point) ──────────────────────────────

def test_is_docs_only_function_exists():
    """AC8: _is_docs_only must be a callable at module level."""
    assert hasattr(sm, "_is_docs_only"), "_is_docs_only must be defined in sprint_manager"
    assert callable(sm._is_docs_only)


def test_is_docs_only_returns_true_for_flag():
    """AC8: _is_docs_only returns (True, 'docs-only:flag') when risk flag is set."""
    result, reason = sm._is_docs_only({"risk_flags": ["docs-only"]})
    assert result is True
    assert reason == "docs-only:flag"


def test_is_docs_only_returns_true_for_all_docs_paths():
    """AC8: _is_docs_only returns (True, 'docs-only:paths') for all-docs paths."""
    result, reason = sm._is_docs_only({
        "files_likely_affected": ["docs/guide.md", "CHANGELOG.md", "config.yaml"]
    })
    assert result is True
    assert reason == "docs-only:paths"


def test_is_docs_only_returns_false_for_empty_estimate():
    """AC8: _is_docs_only returns (False, '') for empty/None estimate."""
    assert sm._is_docs_only({}) == (False, "")
    assert sm._is_docs_only(None) == (False, "")


def test_is_docs_only_returns_false_for_mixed_paths():
    """AC8: _is_docs_only returns (False, '') when code paths are present."""
    result, reason = sm._is_docs_only({
        "files_likely_affected": ["server.py", "docs/guide.md"]
    })
    assert result is False
    assert reason == ""


def test_is_docs_only_returns_false_for_empty_paths():
    """AC8: _is_docs_only returns (False, '') when files_likely_affected is empty."""
    result, reason = sm._is_docs_only({"files_likely_affected": []})
    assert result is False
    assert reason == ""


# ── AC9: existing size-based tests still pass ────────────────────────────────

def test_existing_s_routes_haiku_without_docs_flag(minimal_cfg):
    """AC9: S ticket without docs-only context still routes to Haiku via size routing."""
    estimate = {"size": "S"}
    model, reason = sm._resolve_coder_model(1, minimal_cfg, estimate=estimate)
    assert "haiku" in model.lower()
    # routing_reason may be "size=S" or "docs-only:paths" (no files_likely_affected → no docs-only)
    # Since no files_likely_affected and no risk_flags, must fall through to size=S
    assert reason == "size=S", f"S with no docs-only cues must use size routing; got {reason!r}"


def test_existing_m_routes_sonnet_without_docs_context(minimal_cfg):
    """AC9: M ticket without docs-only context still routes to Sonnet."""
    estimate = {"size": "M"}
    model, reason = sm._resolve_coder_model(1, minimal_cfg, estimate=estimate)
    assert "sonnet" in model.lower()
    assert reason == "size=M"


def test_existing_unestimated_fallback_unchanged(minimal_cfg):
    """AC9: unestimated ticket still falls back to flat default."""
    model, reason = sm._resolve_coder_model(1, minimal_cfg, estimate=None)
    assert model == minimal_cfg.coder_model
    assert reason == "unestimated:default"
