"""Tests for issue #2232: agent md files export role and issue env vars for manual sessions.

AC-1: .claude/agents/coder.md exports CLAUDE_AGENT_ROLE=coder and CLAUDE_AGENT_ISSUE=<N>
      at the start of its workflow
AC-2: .claude/agents/tester.md does the same with role tester
AC-3: Events from a manual session resolve to correct role, issue, project in activity feed
AC-4: Token usage appears under the right agent/model in /api/debug/token-usage/by-agent-model

Note on AC-1/AC-2: these use structural checks on agent markdown files because there is
no environment-free behavioral alternative — running a real Claude Code agent session in
a test context is not feasible. The markdown files ARE the instruction set: verifying
they contain the export directives proves the agent WILL be told to export the env vars.
This is analogous to the CSS deduplication structural check sanctioned in issue #2181.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = REPO_ROOT / ".claude" / "agents"
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
HOOKS_DIR = REPO_ROOT / "hooks"

for p in (str(DASHBOARD_DIR), str(REPO_ROOT), str(HOOKS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")

import db as _db_module  # noqa: E402
import tool_used  # noqa: E402  (hooks/tool_used.py)
import post_tool_used  # noqa: E402  (hooks/post_tool_used.py)


CODER_MD = (AGENTS_DIR / "coder.md").read_text()
TESTER_MD = (AGENTS_DIR / "tester.md").read_text()


# ── AC-1: coder.md instructs the agent to export its role and issue ───────────

class TestCoderMdExports:
    """coder.md must instruct the agent to export CLAUDE_AGENT_ROLE=coder and
    CLAUDE_AGENT_ISSUE=<N> so that hooks fired during a manual /coder session
    carry the correct attribution (AC-1)."""

    def test_exports_claude_agent_role_coder(self):
        assert "CLAUDE_AGENT_ROLE=coder" in CODER_MD, (
            "coder.md must contain 'CLAUDE_AGENT_ROLE=coder' export instruction"
        )

    def test_exports_claude_agent_issue(self):
        assert "CLAUDE_AGENT_ISSUE" in CODER_MD, (
            "coder.md must contain CLAUDE_AGENT_ISSUE export instruction"
        )

    def test_export_appears_near_workflow_start(self):
        """Export instructions must appear early in the workflow — before Step 3."""
        role_pos = CODER_MD.find("CLAUDE_AGENT_ROLE=coder")
        step3_pos = CODER_MD.find("### Step 3")
        assert role_pos != -1, "CLAUDE_AGENT_ROLE=coder not found in coder.md"
        assert step3_pos != -1, "Step 3 header not found in coder.md"
        assert role_pos < step3_pos, (
            "CLAUDE_AGENT_ROLE=coder must be exported before Step 3 (read the ticket)"
        )


# ── AC-2: tester.md instructs the agent to export its role and issue ──────────

class TestTesterMdExports:
    """tester.md must instruct the agent to export CLAUDE_AGENT_ROLE=tester and
    CLAUDE_AGENT_ISSUE=<N> so that hooks fired during a manual /tester session
    carry the correct attribution (AC-2)."""

    def test_exports_claude_agent_role_tester(self):
        assert "CLAUDE_AGENT_ROLE=tester" in TESTER_MD, (
            "tester.md must contain 'CLAUDE_AGENT_ROLE=tester' export instruction"
        )

    def test_exports_claude_agent_issue(self):
        assert "CLAUDE_AGENT_ISSUE" in TESTER_MD, (
            "tester.md must contain CLAUDE_AGENT_ISSUE export instruction"
        )

    def test_export_appears_near_workflow_start(self):
        """Export instructions must appear early in the workflow — before Step 0."""
        role_pos = TESTER_MD.find("CLAUDE_AGENT_ROLE=tester")
        step0_pos = TESTER_MD.find("## Step 0")
        assert role_pos != -1, "CLAUDE_AGENT_ROLE=tester not found in tester.md"
        assert step0_pos != -1, "Step 0 header not found in tester.md"
        assert role_pos < step0_pos, (
            "CLAUDE_AGENT_ROLE=tester must be exported before Step 0"
        )


# ── AC-3: hook + server correctly attributes a manual session's events ────────

@pytest.fixture
def fresh_db(tmp_path):
    db_file = tmp_path / "test_2232.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


@pytest.fixture
def clean_agent_env():
    saved = {k: os.environ.get(k) for k in ("CLAUDE_AGENT_ROLE", "CLAUDE_AGENT_ISSUE")}
    for k in saved:
        os.environ.pop(k, None)
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _get_test_client(fresh_db):
    for mod in list(sys.modules.keys()):
        if mod in ("server", "db"):
            del sys.modules[mod]
    from fastapi.testclient import TestClient
    import db as db_mod
    db_mod.DB_PATH = fresh_db.DB_PATH
    import server as srv
    return TestClient(srv.app, raise_server_exceptions=False), srv


FAKE_PROJECT = [{"repo": "owner/myproj", "name": "My Project"}]


class TestManualSessionAttribution:
    """When CLAUDE_AGENT_ROLE=coder/tester and CLAUDE_AGENT_ISSUE=<N> are set
    (as the updated agent md files now instruct), events are composed with the
    correct role and issue, which the server then parses for attribution (AC-3).

    The hook→compose→parse round-trip is tested here; DB persistence of a single
    event is already covered by test_719.TestEventPersistsIssue.
    """

    @pytest.mark.parametrize("role", ["coder", "tester"])
    def test_hook_composes_name_with_role_and_issue(self, role, clean_agent_env):
        """The hook _compose_name includes role and issue when env vars are set."""
        os.environ["CLAUDE_AGENT_ROLE"] = role
        os.environ["CLAUDE_AGENT_ISSUE"] = "2232"
        with patch.object(tool_used, "_detect_git_info", return_value=("myproj", "feature/2232-x")):
            name = tool_used._compose_name("abcdef123456", "/home/user/dev/myproj")
        assert name.startswith(f"{role}·"), f"name must start with role, got: {name}"
        assert "issue-2232" in name, f"name must include issue token, got: {name}"

    @pytest.mark.parametrize("role", ["coder", "tester"])
    def test_logs_service_parses_role_and_issue_from_composed_name(self, role, clean_agent_env):
        """logs_service._parse_agent_identity resolves role and issue_num from hook-composed name."""
        import importlib
        import importlib.util as _ilu
        spec = _ilu.spec_from_file_location(
            "_logs_svc_2232", DASHBOARD_DIR / "routers" / "logs_service.py"
        )
        _logs_svc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_logs_svc)

        os.environ["CLAUDE_AGENT_ROLE"] = role
        os.environ["CLAUDE_AGENT_ISSUE"] = "2232"
        with patch.object(tool_used, "_detect_git_info", return_value=("myproj", "feature/2232-x")):
            name = tool_used._compose_name("abcdef123456", "/home/user/dev/myproj")

        parsed_role, issue_num = _logs_svc._parse_agent_identity(name)
        assert parsed_role == role, f"expected role={role}, got {parsed_role}"
        assert issue_num == 2232, f"expected issue_num=2232, got {issue_num}"


# ── AC-4: token usage is tagged with agent_role in the hook payload ───────────

class TestTokenUsageAttribution:
    """The PostToolUse hook includes agent_role from CLAUDE_AGENT_ROLE in the
    token_usage event so /api/debug/token-usage/by-agent-model can group by role
    (AC-4)."""

    def test_post_tool_used_reads_agent_role_from_env(self, tmp_path, clean_agent_env):
        """The hook reads CLAUDE_AGENT_ROLE and writes it as agent_role in the event."""
        os.environ["CLAUDE_AGENT_ROLE"] = "coder"

        # Write a minimal JSONL transcript with token usage so main() doesn't early-exit
        transcript = tmp_path / "session.jsonl"
        transcript.write_text(json.dumps({
            "type": "assistant",
            "message": {"usage": {"input_tokens": 100, "output_tokens": 50,
                                  "cache_read_input_tokens": 0,
                                  "cache_creation_input_tokens": 0}},
        }) + "\n")

        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data)
            raise Exception("intercepted")

        import urllib.request as _urllib_req
        with patch.object(_urllib_req, "urlopen", side_effect=fake_urlopen):
            import io
            stdin_payload = json.dumps({
                "session_id": "sess-2232-token",
                "transcript_path": str(transcript),
            })
            with patch("sys.stdin", io.StringIO(stdin_payload)):
                try:
                    post_tool_used.main()
                except (Exception, SystemExit):
                    pass

        body = captured.get("body", {})
        assert body, "token_usage event must have been sent to the dashboard"
        assert body.get("agent_role") == "coder", (
            f"token_usage event must carry agent_role=coder, got: {body.get('agent_role')}"
        )
        assert body.get("event_type") == "token_usage"

    def test_token_usage_db_groups_by_agent_role(self, fresh_db):
        """db.get_token_usage_by_agent_model returns rows with agent_role populated from CLAUDE_AGENT_ROLE."""
        with fresh_db.get_conn() as conn:
            conn.execute(
                "INSERT INTO token_usage "
                "(session_id, input_tokens, output_tokens, agent_role, model_name, project, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("sess-2232-coder", 200, 100, "coder", "claude-sonnet-4-6", "owner/myproj",
                 "2026-08-12T00:00:00Z"),
            )
            conn.commit()

        rows = fresh_db.get_token_usage_by_agent_model()
        roles = [r.get("agent_role") for r in rows]
        assert "coder" in roles, (
            f"get_token_usage_by_agent_model must return rows with agent_role=coder, got: {rows}"
        )
