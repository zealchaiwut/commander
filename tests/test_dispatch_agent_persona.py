"""Tests for the headless-agent persona loader used by the sprint runner.

A `claude -p` subprocess does not auto-load project subagents or run the
interactive `/coder` // `/tester` slash commands, so the runner must pass the
agent persona via --append-system-prompt. _load_agent_persona() supplies it.

Covers: frontmatter stripping, base_dir precedence, REPO_ROOT fallback, and the
graceful empty-string when no persona file exists.
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))


@pytest.fixture()
def sm():
    import importlib
    import sprint_manager as sm  # noqa: WPS433
    return importlib.reload(sm)


def _write_agent(base: Path, role: str, text: str) -> None:
    d = base / ".claude" / "agents"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{role}.md").write_text(text, encoding="utf-8")


def test_strips_yaml_frontmatter(sm, tmp_path):
    _write_agent(tmp_path, "coder", "---\nname: coder\ndescription: x\n---\n\nYou are the coder.\nDo the work.")
    persona = sm._load_agent_persona("coder", tmp_path)
    assert persona == "You are the coder.\nDo the work."
    assert "name: coder" not in persona


def test_no_frontmatter_returned_as_is(sm, tmp_path):
    _write_agent(tmp_path, "tester", "You are the tester.")
    assert sm._load_agent_persona("tester", tmp_path) == "You are the tester."


def test_base_dir_takes_precedence_over_repo_root(sm, tmp_path):
    _write_agent(tmp_path, "coder", "LOCAL CLONE PERSONA")
    assert sm._load_agent_persona("coder", tmp_path) == "LOCAL CLONE PERSONA"


def test_falls_back_to_repo_root_when_base_missing(sm, tmp_path):
    # tmp_path has no .claude/agents → falls back to the real REPO_ROOT/.claude/agents/coder.md
    persona = sm._load_agent_persona("coder", tmp_path)
    assert persona  # the committed coder.md is non-empty
    assert "---" not in persona.splitlines()[0]  # frontmatter stripped


def test_missing_role_returns_empty(sm, tmp_path):
    assert sm._load_agent_persona("does-not-exist", tmp_path) == ""


def test_real_coder_and_tester_personas_load(sm):
    # The committed personas must be discoverable from REPO_ROOT (no base_dir).
    assert sm._load_agent_persona("coder")
    assert sm._load_agent_persona("tester")
