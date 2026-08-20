"""AC tests for issue #2334: dispatch must not bill the API for agent runs.

`dispatch_runner` built its agent subprocess env with `dict(os.environ)`, which
inherited ANTHROPIC_API_KEY from the dashboard process (loaded there by
`load_dotenv` in server.py). The claude CLI prefers that key over the claude.ai
subscription, so every dispatched agent ran on metered API billing — contradicting
CLAUDE.md's pricing table, which states these agents are subscription-funded and
cost $0.00.

Run fa3dd092e22a surfaced it only because the key is unfunded:
`api_error_status: 400, "Credit balance is too low"`.

These call the real env builder with a patched environment. No agent is spawned.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(REPO_ROOT), str(REPO_ROOT / "apps" / "dashboard")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.sprint_manager.dispatch_runner import build_agent_env  # noqa: E402


def test_api_key_is_stripped_from_the_agent_env():
    """The regression: an inherited key routes the agent to API billing."""
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-should-not-leak"}):
        env = build_agent_env("coder", 2329)
    assert "ANTHROPIC_API_KEY" not in env


def test_stripping_does_not_mutate_the_parent_environment():
    """The dashboard keeps its own key; only the subprocess copy loses it."""
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-parent"}):
        build_agent_env("tester", 2329)
        assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-parent"


def test_absent_key_is_not_an_error():
    """A machine with no key configured must dispatch normally."""
    with patch.dict(os.environ, {}, clear=True):
        env = build_agent_env("coder", 7)
    assert "ANTHROPIC_API_KEY" not in env
    assert env["CLAUDE_AGENT_ISSUE"] == "7"


def test_telemetry_attribution_vars_survive():
    """Without these a run lands unattributed and the Running view is blind."""
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-x"}):
        env = build_agent_env("tester", 2329)
    assert env["CLAUDE_AGENT_ROLE"] == "tester"
    assert env["CLAUDE_AGENT_ISSUE"] == "2329"


def test_unrelated_environment_is_passed_through():
    """Only the auth key is removed — the agent still needs PATH, HOME, etc."""
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk", "PATH": "/usr/bin"}):
        env = build_agent_env("coder", 1)
    assert env["PATH"] == "/usr/bin"


def test_role_is_recorded_per_step():
    with patch.dict(os.environ, {}, clear=True):
        assert build_agent_env("coder", 1)["CLAUDE_AGENT_ROLE"] == "coder"
        assert build_agent_env("tester", 1)["CLAUDE_AGENT_ROLE"] == "tester"
