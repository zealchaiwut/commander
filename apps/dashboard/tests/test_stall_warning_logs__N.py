"""Tests for stall warning log viewer endpoints and sprint_manager CLI-not-found handling.

AC1 — GET /api/sprints/{label}/dispatch-log returns {found: false} when no log exists
AC2 — GET /api/sprints/{label}/dispatch-log returns last N lines when log file exists
AC3 — GET /api/sprints/{label}/issue/{N}/log returns tail of sprint-issue-N.log when present
AC4 — _dispatch_coder with COMMANDER_ALLOW_STUB_SUCCESS unset returns (False, CRASH)
       on FileNotFoundError, writes error to log, dispatches alert
AC5 — _dispatch_coder with COMMANDER_ALLOW_STUB_SUCCESS=1 returns (True, None) on
       FileNotFoundError (preserves test behavior)
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

SCRIPTS_DIR   = Path(__file__).parent.parent / "scripts"
SPRINT_SCRIPT = SCRIPTS_DIR / "sprint_manager.py"


# ---------------------------------------------------------------------------
# Helper: import sprint_manager with minimal stubs
# ---------------------------------------------------------------------------

def _import_sprint_manager(mod_name: str = "sprint_manager_stall_import"):
    dotenv_stub = types.ModuleType("dotenv")
    dotenv_stub.load_dotenv = lambda *a, **kw: None
    sys.modules.setdefault("dotenv", dotenv_stub)

    gc_stub = types.ModuleType("github_client")
    gc_stub.repo = lambda: "zealchaiwut/commander"
    gc_stub.update_labels = lambda *a, **kw: None
    gc_stub.add_comment = lambda *a, **kw: None
    gc_stub.get_issue = lambda *a, **kw: {"title": "test issue"}
    sys.modules.setdefault("github_client", gc_stub)

    if mod_name in sys.modules:
        del sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, SPRINT_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client(base_url):
    import httpx
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        yield c


# ---------------------------------------------------------------------------
# AC1 — dispatch-log returns {found: false} when no log file exists
# ---------------------------------------------------------------------------

class TestAC1DispatchLogNotFound:
    def test_no_log_returns_found_false(self, client, tmp_path, monkeypatch):
        """GET /api/sprints/sprint-10/dispatch-log returns found=false when no log file exists."""
        import server

        monkeypatch.setattr(server, "_PROJECTS_BASE", tmp_path)
        commander_dir = tmp_path / "commander" / ".commander"
        commander_dir.mkdir(parents=True)
        (commander_dir / "logs").mkdir()

        r = client.get("/api/sprints/sprint-10/dispatch-log?project=owner/commander")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["found"] is False
        assert "log_dir" in data
        assert data["tail"] == ""

    def test_invalid_sprint_label_returns_400(self, client):
        """Invalid sprint label returns HTTP 400."""
        r = client.get("/api/sprints/not-a-sprint/dispatch-log?project=owner/repo")
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# AC2 — dispatch-log returns last N lines when log file exists
# ---------------------------------------------------------------------------

class TestAC2DispatchLogFound:
    def test_returns_tail_lines(self, client, tmp_path, monkeypatch):
        """GET /api/sprints/sprint-10/dispatch-log returns the last N lines of the most recent log."""
        import server

        monkeypatch.setattr(server, "_PROJECTS_BASE", tmp_path)
        log_dir = tmp_path / "commander" / ".commander" / "logs"
        log_dir.mkdir(parents=True)

        content_lines = [f"line {i}" for i in range(1, 101)]
        log_file = log_dir / "sprint-run-sprint-10-20260101T000000.log"
        log_file.write_text("\n".join(content_lines), encoding="utf-8")

        r = client.get("/api/sprints/sprint-10/dispatch-log?project=owner/commander&tail_lines=50")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["found"] is True
        assert "path" in data
        tail_lines = data["tail"].splitlines()
        assert len(tail_lines) == 50
        assert tail_lines[0] == "line 51"
        assert tail_lines[-1] == "line 100"

    def test_most_recent_log_selected(self, client, tmp_path, monkeypatch):
        """When multiple log files exist, the most recently modified is returned."""
        import server
        import time

        monkeypatch.setattr(server, "_PROJECTS_BASE", tmp_path)
        log_dir = tmp_path / "commander" / ".commander" / "logs"
        log_dir.mkdir(parents=True)

        old_log = log_dir / "sprint-run-sprint-10-20260101T000000.log"
        old_log.write_text("old log content", encoding="utf-8")
        time.sleep(0.05)
        new_log = log_dir / "sprint-run-sprint-10-20260101T120000.log"
        new_log.write_text("new log content", encoding="utf-8")

        r = client.get("/api/sprints/sprint-10/dispatch-log?project=owner/commander")
        assert r.status_code == 200
        data = r.json()
        assert data["found"] is True
        assert data["tail"] == "new log content"

    def test_tail_lines_capped_at_2000(self, client, tmp_path, monkeypatch):
        """tail_lines is capped at 2000 even if caller requests more."""
        import server

        monkeypatch.setattr(server, "_PROJECTS_BASE", tmp_path)
        log_dir = tmp_path / "commander" / ".commander" / "logs"
        log_dir.mkdir(parents=True)
        log_file = log_dir / "sprint-run-sprint-10-20260101T000000.log"
        log_file.write_text("\n".join(f"l{i}" for i in range(3000)), encoding="utf-8")

        r = client.get("/api/sprints/sprint-10/dispatch-log?project=owner/commander&tail_lines=9999")
        assert r.status_code == 200
        data = r.json()
        assert data["found"] is True
        assert len(data["tail"].splitlines()) == 2000


# ---------------------------------------------------------------------------
# AC3 — per-issue log endpoint returns tail when file exists
# ---------------------------------------------------------------------------

class TestAC3IssueLogFound:
    def test_issue_log_tail_returned(self, client, tmp_path, monkeypatch):
        """GET /api/sprints/sprint-10/issue/58/log returns tail of sprint-issue-58.log."""
        import server

        monkeypatch.setattr(server, "_PROJECTS_BASE", tmp_path)
        log_dir = tmp_path / "commander" / ".commander" / "logs"
        log_dir.mkdir(parents=True)
        log_file = log_dir / "sprint-issue-58.log"
        log_file.write_text("coder started\ncoder done\ntester passed", encoding="utf-8")

        r = client.get("/api/sprints/sprint-10/issue/58/log?project=owner/commander")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["found"] is True
        assert "tester passed" in data["tail"]

    def test_issue_log_not_found_returns_found_false(self, client, tmp_path, monkeypatch):
        """When sprint-issue-N.log does not exist, found=false with candidate paths."""
        import server

        monkeypatch.setattr(server, "_PROJECTS_BASE", tmp_path)
        log_dir = tmp_path / "commander" / ".commander" / "logs"
        log_dir.mkdir(parents=True)

        r = client.get("/api/sprints/sprint-10/issue/999/log?project=owner/commander")
        assert r.status_code == 200
        data = r.json()
        assert data["found"] is False
        assert "candidates" in data


# ---------------------------------------------------------------------------
# AC4 — _dispatch_coder without COMMANDER_ALLOW_STUB_SUCCESS returns CRASH
# ---------------------------------------------------------------------------

class TestAC4DispatchCoderNoStub:
    def test_missing_claude_returns_crash_without_stub_flag(self, tmp_path, monkeypatch):
        """Without COMMANDER_ALLOW_STUB_SUCCESS, FileNotFoundError must return (False, CRASH)."""
        monkeypatch.delenv("COMMANDER_ALLOW_STUB_SUCCESS", raising=False)
        sm = _import_sprint_manager("sm_ac4")

        def fake_popen(cmd, **kwargs):
            raise FileNotFoundError("claude not found")

        alerts_dispatched = []

        def fake_dispatch_alerts(modes, title, body, **kwargs):
            alerts_dispatched.append({"title": title, "body": body})

        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        cfg = sm.SprintConfig(
            repo_name="owner/repo",
            worktree_coder=tmp_path,
            logs_dir=logs_dir,
        )

        with mock.patch("subprocess.Popen", side_effect=fake_popen), \
             mock.patch.object(sm, "dispatch_alerts", fake_dispatch_alerts):
            ok, category = sm._dispatch_coder(
                issue_num=58,
                alert_modes=["dashboard-banner"],
                cfg=cfg,
            )

        assert ok is False, "Should return False when claude CLI is missing without stub flag"
        assert category == sm.FailureCategory.CRASH

    def test_missing_claude_writes_error_to_log(self, tmp_path, monkeypatch):
        """Without COMMANDER_ALLOW_STUB_SUCCESS, error message is written to log_path."""
        monkeypatch.delenv("COMMANDER_ALLOW_STUB_SUCCESS", raising=False)
        sm = _import_sprint_manager("sm_ac4_log")

        def fake_popen(cmd, **kwargs):
            raise FileNotFoundError("claude not found")

        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        cfg = sm.SprintConfig(
            repo_name="owner/repo",
            worktree_coder=tmp_path,
            logs_dir=logs_dir,
        )

        with mock.patch("subprocess.Popen", side_effect=fake_popen), \
             mock.patch.object(sm, "dispatch_alerts", lambda *a, **kw: None):
            sm._dispatch_coder(
                issue_num=58,
                alert_modes=[],
                cfg=cfg,
            )

        log_path = logs_dir / "sprint-issue-58.log"
        assert log_path.exists(), "Log file must exist after dispatch attempt"
        content = log_path.read_text(encoding="utf-8")
        assert "claude CLI not found" in content or "claude" in content.lower(), \
            f"Log must mention claude CLI not found; got: {content!r}"

    def test_missing_claude_dispatches_alert(self, tmp_path, monkeypatch):
        """Without COMMANDER_ALLOW_STUB_SUCCESS, dispatch_alerts is called with CRASH category."""
        monkeypatch.delenv("COMMANDER_ALLOW_STUB_SUCCESS", raising=False)
        sm = _import_sprint_manager("sm_ac4_alert")

        def fake_popen(cmd, **kwargs):
            raise FileNotFoundError("claude not found")

        alerts = []

        def fake_dispatch_alerts(modes, title, body, **kwargs):
            alerts.append({"title": title, "body": body, "category": kwargs.get("category")})

        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        cfg = sm.SprintConfig(
            repo_name="owner/repo",
            worktree_coder=tmp_path,
            logs_dir=logs_dir,
        )

        with mock.patch("subprocess.Popen", side_effect=fake_popen), \
             mock.patch.object(sm, "dispatch_alerts", fake_dispatch_alerts):
            sm._dispatch_coder(
                issue_num=58,
                alert_modes=["dashboard-banner"],
                cfg=cfg,
            )

        assert len(alerts) == 1, "One alert must be dispatched"
        assert "claude" in alerts[0]["title"].lower() or "claude" in alerts[0]["body"].lower()
        assert alerts[0]["category"] == sm.FailureCategory.CRASH


# ---------------------------------------------------------------------------
# AC5 — _dispatch_coder with COMMANDER_ALLOW_STUB_SUCCESS=1 returns (True, None)
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("allow_stub_success")
class TestAC5DispatchCoderWithStub:
    def test_missing_claude_returns_stub_success_when_flag_set(self, tmp_path):
        """With COMMANDER_ALLOW_STUB_SUCCESS=1, FileNotFoundError returns (True, None)."""
        sm = _import_sprint_manager("sm_ac5")

        def fake_popen(cmd, **kwargs):
            raise FileNotFoundError("claude not found")

        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        cfg = sm.SprintConfig(
            repo_name="owner/repo",
            worktree_coder=tmp_path,
            logs_dir=logs_dir,
        )

        with mock.patch("subprocess.Popen", side_effect=fake_popen):
            ok, category = sm._dispatch_coder(
                issue_num=1,
                alert_modes=[],
                cfg=cfg,
            )

        assert ok is True, "Should return True (stub success) when COMMANDER_ALLOW_STUB_SUCCESS=1"
        assert category is None
