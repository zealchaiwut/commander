"""Tests for issue #719: Link activity log agent rows to GitHub issues.

AC-1: sprint_manager includes issue number and role in the agent name when
      dispatching (role + issue threaded through env → hook-composed name).
AC-2: /api/agent-event parses and persists issue_num and role from
      agent_started / agent_finished events.
AC-3: Activity log rows render as `<role> <action> #<issue>`.
AC-4: `#<issue>` is a clickable link to the GitHub issue.
AC-5: All four roles are labeled: coder, tester, estimator, reviewer.
AC-6: Rows with no issue/role data fall back to the raw UUID (no regression).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
HOOKS_DIR = REPO_ROOT / "hooks"

for p in (str(DASHBOARD_DIR), str(REPO_ROOT), str(HOOKS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")

import db as _db_module  # noqa: E402
import services.sprint_manager.sprint_manager as sm  # noqa: E402

import tool_used  # noqa: E402  (hooks/tool_used.py)
import agent_finished  # noqa: E402  (hooks/agent_finished.py)


FAKE_PROJECT = [{"repo": "owner/myproj", "name": "My Project"}]


@pytest.fixture
def fresh_db(tmp_path):
    db_file = tmp_path / "test_719.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


def _get_test_client(fresh_db):
    for mod in list(sys.modules.keys()):
        if mod in ("server", "db"):
            del sys.modules[mod]
    from fastapi.testclient import TestClient
    import db as db_mod
    db_mod.DB_PATH = fresh_db.DB_PATH
    import server as srv
    return TestClient(srv.app, raise_server_exceptions=False), srv


@pytest.fixture
def clean_agent_env():
    """Remove agent identity env vars so each test controls them explicitly."""
    saved = {k: os.environ.get(k) for k in ("CLAUDE_AGENT_ROLE", "CLAUDE_AGENT_ISSUE")}
    for k in saved:
        os.environ.pop(k, None)
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


# ── AC-1 / AC-5: hook composes role + issue into the agent name ───────────────

class TestHookNameComposition:
    """The agent name carries the role and issue number once sprint_manager
    sets CLAUDE_AGENT_ROLE / CLAUDE_AGENT_ISSUE (AC-1, AC-5)."""

    @pytest.mark.parametrize("compose_mod", [tool_used, agent_finished])
    @pytest.mark.parametrize("role", ["coder", "tester", "estimator", "reviewer"])
    def test_name_includes_role_and_issue(self, compose_mod, role, clean_agent_env):
        os.environ["CLAUDE_AGENT_ROLE"] = role
        os.environ["CLAUDE_AGENT_ISSUE"] = "648"
        with patch.object(compose_mod, "_detect_git_info", return_value=("myproj", "feature/648-x")):
            name = compose_mod._compose_name("sessionabcdef", "/home/user/dev/myproj")
        assert name.split("·")[0] == role, f"role must be first name component, got {name}"
        assert "issue-648" in name, f"issue number must be embedded in name, got {name}"

    @pytest.mark.parametrize("compose_mod", [tool_used, agent_finished])
    def test_name_without_issue_has_no_issue_token(self, compose_mod, clean_agent_env):
        """No CLAUDE_AGENT_ISSUE → name keeps the legacy format, no issue token."""
        os.environ["CLAUDE_AGENT_ROLE"] = "coder"
        with patch.object(compose_mod, "_detect_git_info", return_value=("myproj", "feature/1-x")):
            name = compose_mod._compose_name("sessionabcdef", "/home/user/dev/myproj")
        assert "issue-" not in name, f"no issue token expected, got {name}"
        assert name.startswith("coder·")


# ── AC-5: sprint_manager sets role + issue env for all four roles ─────────────

class TestAgentIdentityEnv:
    """sprint_manager._agent_identity_env tags each dispatched role (AC-5)."""

    @pytest.mark.parametrize("role", ["coder", "tester", "estimator", "reviewer"])
    def test_env_sets_role_and_issue(self, role):
        env = sm._agent_identity_env(role, 700)
        assert env["CLAUDE_AGENT_ROLE"] == role
        assert env["CLAUDE_AGENT_ISSUE"] == "700"

    def test_env_omits_issue_when_none(self):
        env = sm._agent_identity_env("reviewer", None)
        assert env["CLAUDE_AGENT_ROLE"] == "reviewer"
        assert "CLAUDE_AGENT_ISSUE" not in env

    def test_env_omits_issue_when_zero(self):
        """issue_num=0 is the reviewer sentinel — must not emit a #0 link."""
        env = sm._agent_identity_env("reviewer", 0)
        assert "CLAUDE_AGENT_ISSUE" not in env


# ── AC-2: /api/agent-event parses identity from the name ──────────────────────

class TestParseAgentIdentity:
    """server._parse_agent_identity pulls role + issue_num from the name."""

    def _srv(self, fresh_db):
        _client, srv = _get_test_client(fresh_db)
        return srv

    @pytest.mark.parametrize("role", ["coder", "tester", "estimator", "reviewer"])
    def test_parses_role_and_issue(self, fresh_db, role):
        srv = self._srv(fresh_db)
        name = f"{role}·myproj·feature/700-x·#abc123·issue-700"
        parsed_role, issue_num = srv._parse_agent_identity(name)
        assert parsed_role == role
        assert issue_num == 700

    def test_legacy_name_without_issue(self, fresh_db):
        srv = self._srv(fresh_db)
        # AC-6: legacy 4-part name → role present, issue None (no crash)
        parsed_role, issue_num = srv._parse_agent_identity("coder·myproj·feature/1·#abc123")
        assert parsed_role == "coder"
        assert issue_num is None

    def test_raw_uuid_name(self, fresh_db):
        srv = self._srv(fresh_db)
        # AC-6: a bare UUID with no separators → no role, no issue
        parsed_role, issue_num = srv._parse_agent_identity("8f3c1d2e-aaaa-bbbb")
        assert parsed_role is None
        assert issue_num is None


# ── AC-2: persisted events carry issue_num + role ─────────────────────────────

class TestEventPersistsIssue:
    """agent_started / agent_finished detail must include issue_num and role."""

    def _detail_for(self, fresh_db, event_type, name):
        client, srv = _get_test_client(fresh_db)
        payload = {
            "session_id": "sess-719-" + event_type,
            "event_type": event_type,
            "working_dir": "/home/user/dev/myproj",
            "name": name,
        }
        if event_type == "tool_use":
            payload["status"] = "working"
            payload["tool_name"] = "Read"
            db_type = "agent_started"
        else:
            payload["status"] = "done"
            db_type = "agent_finished"
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            resp = client.post("/api/agent-event", json=payload)
        assert resp.status_code == 200
        with fresh_db.get_conn() as conn:
            rows = conn.execute(
                "SELECT detail FROM events WHERE type=?", (db_type,)
            ).fetchall()
        assert rows, f"no {db_type} event written"
        return json.loads(dict(rows[0])["detail"])

    def test_agent_started_persists_issue_and_role(self, fresh_db):
        detail = self._detail_for(
            fresh_db, "tool_use", "coder·myproj·feature/700-x·#abc123·issue-700"
        )
        assert detail.get("issue_num") == 700
        assert detail.get("role") == "coder"

    def test_agent_finished_persists_issue_and_role(self, fresh_db):
        detail = self._detail_for(
            fresh_db, "agent_stop", "tester·myproj·feature/700-x·#abc123·issue-700"
        )
        assert detail.get("issue_num") == 700
        assert detail.get("role") == "tester"

    def test_legacy_event_falls_back_to_none(self, fresh_db):
        """AC-6: legacy name without issue → issue_num None, no crash."""
        detail = self._detail_for(
            fresh_db, "tool_use", "coder·myproj·feature/1·#abc123"
        )
        assert detail.get("issue_num") is None
        assert detail.get("role") == "coder"


# ── AC-3 / AC-4 / AC-6: frontend renders the linked row ───────────────────────

class TestFrontendRendering:
    """project.html must render agent rows as `<role> <action> #<issue>` with a
    GitHub issue link, and fall back to the raw UUID when data is missing."""

    PROJECT_HTML = (DASHBOARD_DIR / "static" / "project.html").read_text()

    def test_handles_agent_started_and_finished(self):
        assert "agent_started" in self.PROJECT_HTML
        assert "agent_finished" in self.PROJECT_HTML

    def test_reads_issue_num_from_detail(self):
        assert "issue_num" in self.PROJECT_HTML, "renderer must consult detail.issue_num"

    def test_builds_github_issue_link(self):
        # AC-4: clickable link to the GitHub issue
        assert "/issues/" in self.PROJECT_HTML
        assert "github.com" in self.PROJECT_HTML

    def test_has_fallback_marker(self):
        # AC-6: a named fallback path for rows lacking role/issue
        assert "evl-agent-row" in self.PROJECT_HTML or "evlAgentDesc" in self.PROJECT_HTML
