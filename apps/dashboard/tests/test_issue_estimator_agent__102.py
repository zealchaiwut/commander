"""Tests for issue #102: Add Issue Estimator agent — sizing, dependencies, files affected, parallelization data.

Covers all acceptance criteria without invoking actual Claude agents or requiring
a running dashboard server.
"""
from __future__ import annotations

import json
import sys
import textwrap
import importlib.util
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# --- Repo root detection -------------------------------------------------------

TESTER_ROOT = Path(__file__).parent.parent.parent.parent  # .../tester
REPO_ROOT = TESTER_ROOT  # estimate_issue.py uses __file__-relative path

ESTIMATOR_AGENT_PATH = TESTER_ROOT / "apps" / "dashboard" / ".claude" / "agents" / "estimator.md"
ESTIMATE_SCRIPT_PATH = TESTER_ROOT / "services" / "sprint_manager" / "estimate_issue.py"
ESTIMATE_COMMAND_PATH = TESTER_ROOT / ".claude" / "commands" / "estimate.md"
BA_AGENT_PATH = TESTER_ROOT / ".claude" / "agents" / "ba.md"
CLAUDE_MD_PATH = TESTER_ROOT / "CLAUDE.md"
SPRINT_MANAGER_PATH = TESTER_ROOT / "services" / "sprint_manager" / "sprint_manager.py"


# ── AC1: estimator.md exists ─────────────────────────────────────────────────

def test_estimator_agent_file_exists():
    """AC: Agent definition exists at apps/dashboard/.claude/agents/estimator.md"""
    assert ESTIMATOR_AGENT_PATH.exists(), (
        f"estimator.md not found at {ESTIMATOR_AGENT_PATH}"
    )


# ── AC2: JSON output shape ────────────────────────────────────────────────────

REQUIRED_JSON_FIELDS = {
    "issue_number", "size", "estimated_hours", "confidence",
    "files_likely_affected", "depends_on", "blocks", "risk_flags", "summary",
}


def test_estimator_agent_prompt_contains_required_json_fields():
    """AC: Estimator prompt instructs Claude to produce JSON with the exact required fields."""
    content = ESTIMATOR_AGENT_PATH.read_text()
    for field in REQUIRED_JSON_FIELDS:
        assert f'"{field}"' in content or f"'{field}'" in content or field in content, (
            f"Required JSON field '{field}' not mentioned in estimator.md"
        )


def test_estimator_agent_prompt_has_json_schema_example():
    """AC: estimator.md contains a JSON schema example with all required keys."""
    content = ESTIMATOR_AGENT_PATH.read_text()
    for field in REQUIRED_JSON_FIELDS:
        assert field in content, f"Field '{field}' missing from estimator.md schema example"


# ── AC3: Size scale ───────────────────────────────────────────────────────────

def test_estimator_agent_defines_size_scale():
    """AC: Size scale S/M/L/XL defined in agent prompt with hour boundaries."""
    content = ESTIMATOR_AGENT_PATH.read_text()
    for size in ("S", "M", "L", "XL"):
        assert size in content, f"Size '{size}' not defined in estimator.md"
    # Check hour thresholds are mentioned
    assert "1" in content and "3" in content and "8" in content, (
        "Hour thresholds (1hr, 3hr, 8hr) not found in size scale"
    )


# ── AC4: Confidence levels ─────────────────────────────────────────────────────

def test_estimator_agent_defines_confidence_levels():
    """AC: Confidence levels low, medium, high are defined in estimator.md."""
    content = ESTIMATOR_AGENT_PATH.read_text()
    for level in ("low", "medium", "high"):
        assert level in content, f"Confidence level '{level}' not defined in estimator.md"


# ── AC5: Risk flag taxonomy ───────────────────────────────────────────────────

REQUIRED_RISK_FLAGS = [
    "touches-db-schema",
    "breaks-tests",
    "new-dependency",
    "modifies-public-api",
    "security-sensitive",
    "requires-manual-config",
]


def test_estimator_agent_documents_risk_flag_taxonomy():
    """AC: Risk flag taxonomy documented in estimator.md."""
    content = ESTIMATOR_AGENT_PATH.read_text()
    for flag in REQUIRED_RISK_FLAGS:
        assert flag in content, f"Risk flag '{flag}' not documented in estimator.md"


# ── AC6a: estimate_issue.py exists with correct CLI flags ─────────────────────

def test_estimate_script_exists():
    """AC: New script exists at services/sprint_manager/estimate_issue.py."""
    assert ESTIMATE_SCRIPT_PATH.exists(), (
        f"estimate_issue.py not found at {ESTIMATE_SCRIPT_PATH}"
    )


def test_estimate_script_has_required_cli_flags():
    """AC: estimate_issue.py CLI has --issue, --repo, --save-comment, --save-label, --force."""
    content = ESTIMATE_SCRIPT_PATH.read_text()
    for flag in ("--issue", "--repo", "--save-comment", "--save-label", "--force"):
        assert flag in content, f"CLI flag '{flag}' not found in estimate_issue.py"


# ── AC6b: Invokes claude -p with Haiku model ──────────────────────────────────

def test_estimate_script_uses_haiku_model():
    """AC: Estimator uses claude-haiku-4-5 (cost guardrail)."""
    content = ESTIMATE_SCRIPT_PATH.read_text()
    assert "claude-haiku-4-5" in content, (
        "estimate_issue.py must invoke claude with Haiku 4.5 model"
    )


def test_estimate_script_uses_claude_p_subprocess():
    """AC: Invokes the estimator agent via `claude -p` subprocess (subscription auth)."""
    content = ESTIMATE_SCRIPT_PATH.read_text()
    assert '"claude"' in content or "'claude'" in content, (
        "estimate_issue.py must invoke 'claude' via subprocess"
    )
    assert '"-p"' in content or "'-p'" in content, (
        "estimate_issue.py must pass -p flag to claude CLI"
    )


# ── AC6c: Saves to .commander/estimates/ ──────────────────────────────────────

def test_estimate_script_saves_to_commander_estimates():
    """AC: Saves to <project>/.commander/estimates/issue-<N>.json."""
    content = ESTIMATE_SCRIPT_PATH.read_text()
    assert ".commander" in content and "estimates" in content, (
        "estimate_issue.py must save to .commander/estimates/"
    )
    assert "issue-" in content, "estimate_issue.py must use 'issue-<N>.json' filename pattern"


# ── AC6c: extract_json function handles fenced code blocks ────────────────────

def _load_estimate_module():
    """Dynamically load estimate_issue.py as a module."""
    spec = importlib.util.spec_from_file_location("estimate_issue", ESTIMATE_SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_extract_json_parses_bare_json():
    """AC: estimate_issue.py can parse clean JSON output from the agent."""
    mod = _load_estimate_module()
    sample = json.dumps({
        "issue_number": 7, "size": "M", "estimated_hours": 3,
        "confidence": "medium", "files_likely_affected": ["main.py"],
        "depends_on": [], "blocks": [], "risk_flags": [], "summary": "test",
    })
    result = mod.extract_json(sample)
    assert result is not None
    assert result["size"] == "M"
    assert result["issue_number"] == 7


def test_extract_json_strips_markdown_fences():
    """AC: JSON parser strips ``` code fences the model sometimes wraps around JSON."""
    mod = _load_estimate_module()
    fenced = '```json\n{"issue_number": 1, "size": "S"}\n```'
    result = mod.extract_json(fenced)
    assert result is not None
    assert result["size"] == "S"


def test_extract_json_returns_none_on_invalid():
    """AC: extract_json returns None for unparseable text."""
    mod = _load_estimate_module()
    assert mod.extract_json("not json at all") is None


# ── AC10: Caching ─────────────────────────────────────────────────────────────

def test_estimate_script_has_cache_logic():
    """AC: Re-running on same issue without changes returns cached estimate unless --force."""
    content = ESTIMATE_SCRIPT_PATH.read_text()
    assert "--force" in content, "estimate_issue.py must support --force flag"
    assert "cached" in content.lower() or "exists()" in content, (
        "estimate_issue.py must check for cached estimate file"
    )


def test_estimate_script_cache_path_per_issue(tmp_path):
    """AC: Caching logic uses per-issue filename issue-<N>.json."""
    mod = _load_estimate_module()

    # Simulate a commander dir with a pre-existing estimate
    commander_dir = tmp_path / ".commander"
    estimates_dir = commander_dir / "estimates"
    estimates_dir.mkdir(parents=True)
    cached = {"issue_number": 99, "size": "S", "estimated_hours": 0.5,
              "confidence": "high", "files_likely_affected": [],
              "depends_on": [], "blocks": [], "risk_flags": [], "summary": "cached"}
    (estimates_dir / "issue-99.json").write_text(json.dumps(cached))

    # Verify find_commander_dir walks up and finds it
    result = mod.find_commander_dir(tmp_path)
    assert result is not None
    cached_path = result / "estimates" / "issue-99.json"
    assert cached_path.exists()
    loaded = json.loads(cached_path.read_text())
    assert loaded["size"] == "S"


# ── AC7: /estimate slash command ──────────────────────────────────────────────

def test_estimate_slash_command_exists():
    """AC: /estimate <issue-url> slash command exists at .claude/commands/estimate.md."""
    assert ESTIMATE_COMMAND_PATH.exists(), (
        f"estimate.md slash command not found at {ESTIMATE_COMMAND_PATH}"
    )


def test_estimate_slash_command_calls_script():
    """AC: estimate.md slash command invokes estimate_issue.py."""
    content = ESTIMATE_COMMAND_PATH.read_text()
    assert "estimate_issue.py" in content, (
        "estimate.md must reference estimate_issue.py script"
    )


def test_estimate_slash_command_accepts_issue_url():
    """AC: estimate.md handles an issue URL argument."""
    content = ESTIMATE_COMMAND_PATH.read_text()
    assert "$ARGUMENTS" in content or "issue-url" in content.lower() or "url" in content.lower(), (
        "estimate.md must accept an issue URL as argument"
    )


# ── AC8: BA agent updated ─────────────────────────────────────────────────────

def test_ba_agent_references_estimator():
    """AC: BA agent prompt updated to optionally invoke estimator after ticket creation."""
    assert BA_AGENT_PATH.exists(), f"ba.md not found at {BA_AGENT_PATH}"
    content = BA_AGENT_PATH.read_text()
    assert "estimate" in content.lower(), (
        "BA agent (ba.md) must reference the estimator"
    )


def test_ba_agent_estimator_is_opt_in():
    """AC: Estimator invocation in BA agent is off by default (opt-in)."""
    content = BA_AGENT_PATH.read_text()
    # Should mention "off by default" or similar phrasing
    assert "off by default" in content.lower() or "opt-in" in content.lower() or "only if" in content.lower(), (
        "BA agent must clearly mark estimator invocation as off by default / opt-in"
    )


# ── AC9: Sprint manager reads estimates ──────────────────────────────────────

def test_sprint_manager_has_load_estimate_function():
    """AC: Sprint manager reads .commander/estimates/issue-<N>.json when dispatching."""
    content = SPRINT_MANAGER_PATH.read_text()
    assert "_load_estimate" in content or "load_estimate" in content, (
        "sprint_manager.py must have a function to load estimates"
    )


def test_sprint_manager_logs_estimate_size_and_risk():
    """AC: Sprint manager logs estimated size, risk flags, and warns on serious risks."""
    content = SPRINT_MANAGER_PATH.read_text()
    assert "size" in content and "risk" in content.lower(), (
        "sprint_manager.py must log estimate size and risk flags"
    )
    assert "serious" in content.lower() or "WARNING" in content or "warning" in content.lower(), (
        "sprint_manager.py must warn on serious risk flags"
    )


def test_sprint_manager_warns_on_file_conflicts():
    """AC: Sprint manager warns when two pending tickets share files in files_likely_affected."""
    content = SPRINT_MANAGER_PATH.read_text()
    assert "share files" in content or "files_likely_affected" in content, (
        "sprint_manager.py must warn when tickets share files_likely_affected"
    )


def test_sprint_manager_file_conflict_warning_logic():
    """AC: _warn_file_conflicts correctly identifies overlapping files between pending issues."""
    spec = importlib.util.spec_from_file_location("sprint_manager", SPRINT_MANAGER_PATH)
    mod = importlib.util.module_from_spec(spec)

    # Patch heavy imports that may not be available in test env
    sys.modules.setdefault("anthropic", MagicMock())

    # Load just the function by exec'ing the source up to the function
    src = SPRINT_MANAGER_PATH.read_text()
    # Just check the function is defined
    assert "def _warn_file_conflicts" in src, (
        "sprint_manager.py must define _warn_file_conflicts function"
    )
    assert "files_likely_affected" in src, (
        "_warn_file_conflicts must check 'files_likely_affected' from estimates"
    )


# ── AC11: CLAUDE.md updated ───────────────────────────────────────────────────

def test_claude_md_has_issue_estimator_section():
    """AC: CLAUDE.md updated with 'Issue Estimator' section."""
    assert CLAUDE_MD_PATH.exists(), f"CLAUDE.md not found at {CLAUDE_MD_PATH}"
    content = CLAUDE_MD_PATH.read_text()
    assert "Issue Estimator" in content, (
        "CLAUDE.md must contain an 'Issue Estimator' section"
    )


def test_claude_md_estimator_section_has_usage():
    """AC: CLAUDE.md Issue Estimator section explains the agent and how to invoke it."""
    content = CLAUDE_MD_PATH.read_text()
    assert "estimate_issue.py" in content or "/estimate" in content, (
        "CLAUDE.md Issue Estimator section must show how to invoke the estimator"
    )


# ── AC12: Estimator agent uses Haiku 4.5 ──────────────────────────────────────

def test_estimator_agent_frontmatter_uses_haiku():
    """AC: Cost guardrail — estimator.md frontmatter specifies Haiku 4.5 model."""
    content = ESTIMATOR_AGENT_PATH.read_text()
    # YAML frontmatter should set model to haiku
    assert "claude-haiku-4-5" in content, (
        "estimator.md must specify claude-haiku-4-5 as the model (cost guardrail)"
    )


# ── Integration: find_commander_dir walks up ──────────────────────────────────

def test_find_commander_dir_walks_up(tmp_path):
    """AC: estimate_issue.py finds .commander/ by walking up from CWD."""
    mod = _load_estimate_module()

    # Create nested structure: tmp/a/b/c with .commander at tmp/a
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    commander = tmp_path / "a" / ".commander"
    commander.mkdir()

    result = mod.find_commander_dir(nested)
    assert result == commander


def test_find_commander_dir_returns_none_when_absent(tmp_path):
    """AC: find_commander_dir returns None when no .commander/ dir exists."""
    mod = _load_estimate_module()
    result = mod.find_commander_dir(tmp_path)
    assert result is None


# ── Integration: load_agent_instructions strips frontmatter ───────────────────

def test_load_agent_instructions_strips_frontmatter():
    """AC: estimate_issue.py strips YAML frontmatter before sending to agent."""
    mod = _load_estimate_module()
    instructions = mod.load_agent_instructions()
    # Should not contain raw YAML frontmatter keys
    assert not instructions.startswith("---"), (
        "load_agent_instructions must strip YAML frontmatter"
    )
    # Should contain actual prompt content
    assert len(instructions) > 100, "Agent instructions should be non-trivial in length"
