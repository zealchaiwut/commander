"""Tests for issue #657: Add Sprint and Agent Logs to Activity Tab.

AC-1: Sprint start, completion, and failure events are logged in the Activity tab
AC-2: Each sprint log entry shows sprint ID, status, timestamp, and duration
AC-3: Agent invocation events (start, finish, error) appear as log entries
AC-4: Each agent log entry shows agent name/ID, action taken, timestamp, and outcome
AC-5: Log entries are ordered chronologically (newest first), consistently
AC-6: Log entries persist across page refreshes / tab switches
AC-7: Metrics-relevant fields (duration, status, agent name) are present in the data model
AC-8: No existing Activity tab entries or UI are broken
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))
os.environ["COMMANDER_MAX_FIX_ROUNDS"] = "1"


# ── DB fixture ────────────────────────────────────────────────────────────────

import db as _db_module  # noqa: E402
import services.sprint_manager.sprint_manager as sm  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path):
    db_file = tmp_path / "test_657.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


# ── Sprint manager mock helpers ────────────────────────────────────────────────

def _apply_base_mocks(monkeypatch, tmp_path, issues=None):
    if issues is None:
        issues = [
            {"number": 11, "title": "First ticket"},
            {"number": 12, "title": "Second ticket"},
        ]
    monkeypatch.setattr(sm, "list_backlog_issues", lambda *a, **kw: issues)
    monkeypatch.setattr(sm, "_dispatch_coder", lambda *a, **kw: (True, None))
    monkeypatch.setattr(sm, "_find_feature_branch", lambda n: f"feature/{n}-test")
    monkeypatch.setattr(sm, "_dispatch_tester", lambda *a, **kw: (0, None))
    monkeypatch.setattr(sm, "handle_post_tester", lambda *a, **kw: (True, "Merged", None))
    monkeypatch.setattr(sm, "_post_sprint_status", lambda *a, **kw: None)
    monkeypatch.setattr(sm, "_neon_ticket_status", lambda *a, **kw: None)
    monkeypatch.setattr(sm, "_neon_sprint_init", lambda *a, **kw: None)
    monkeypatch.setattr(sm, "_neon_sprint_status", lambda *a, **kw: None)
    monkeypatch.setattr(sm, "_setup_pid_file", lambda *a, **kw: None)
    monkeypatch.setattr(sm, "_wait_if_paused", lambda *a, **kw: None)
    monkeypatch.setattr(sm, "_create_sprint_branch", lambda *a, **kw: None)
    monkeypatch.setattr(sm, "_warn_file_conflicts", lambda *a, **kw: None)
    monkeypatch.setattr(sm, "_load_estimate", lambda *a, **kw: None)
    monkeypatch.setattr(sm, "_transition_safe", lambda *a, **kw: None)
    monkeypatch.setattr(sm, "SPRINTS_DIR", tmp_path / "sprints")
    monkeypatch.setattr(sm, "structured_log", MagicMock())
    monkeypatch.delenv("COMMANDER_RUN_ID", raising=False)
    sm._sprint_user_cancelled.clear()


@pytest.fixture
def event_capture(tmp_path, monkeypatch):
    calls = []

    def fake_record(project, source, actor, type, target, detail, action_id=None):
        calls.append({
            "project": project,
            "source": source,
            "actor": actor,
            "type": type,
            "target": target,
            "detail": detail,
            "action_id": action_id,
        })

    monkeypatch.setattr(sm, "_db_record_event", fake_record, raising=False)
    monkeypatch.setattr(sm, "_RECORD_EVENT_AVAILABLE", True, raising=False)
    _apply_base_mocks(monkeypatch, tmp_path)
    return calls


def _run_sprint(label="sprint-99", repo_name="owner/myrepo", **kwargs):
    defaults = dict(
        label=label,
        skip_gates=True,
        gate_pytest=False,
        gate_lint=False,
        gate_merge_preview=False,
        gate_typecheck=False,
        gate_design=False,
        gate_frontend_lint=False,
        skip_estimator=True,
        repo_name=repo_name,
    )
    defaults.update(kwargs)
    return sm.run_sprint(**defaults)


# ── Server test client helper ─────────────────────────────────────────────────

FAKE_PROJECT = [{"repo": "owner/myproj", "name": "My Project"}]


def _get_test_client(fresh_db):
    for mod in list(sys.modules.keys()):
        if mod in ("server", "db"):
            del sys.modules[mod]

    from fastapi.testclient import TestClient
    import db as db_mod
    db_mod.DB_PATH = fresh_db.DB_PATH
    import server as srv
    return TestClient(srv.app, raise_server_exceptions=False), srv


# ── AC-1: Sprint events are logged ────────────────────────────────────────────

class TestSprintEventsLogged:
    """AC-1: Sprint start, completion, and failure events are logged."""

    def test_sprint_started_fires(self, event_capture):
        _run_sprint()
        assert any(c["type"] == "sprint_started" for c in event_capture)

    def test_sprint_finished_fires(self, event_capture):
        _run_sprint()
        assert any(c["type"] == "sprint_finished" for c in event_capture)

    def test_sprint_cancelled_fires_on_system_exit(self, event_capture, tmp_path, monkeypatch):
        coder_calls = [0]

        def coder_that_cancels(*args, **kwargs):
            coder_calls[0] += 1
            if coder_calls[0] > 1:
                raise SystemExit(130)
            return (True, None)

        monkeypatch.setattr(sm, "_dispatch_coder", coder_that_cancels)
        with pytest.raises(SystemExit):
            _run_sprint(label="sprint-cancel", repo_name="owner/myrepo")

        assert any(c["type"] == "sprint_cancelled" for c in event_capture)

    def test_sprint_events_use_agent_source(self, event_capture):
        """source must be 'agent' — 'sprint_manager' violates the DB CHECK constraint."""
        _run_sprint()
        sprint_events = [c for c in event_capture if c["type"].startswith("sprint_")]
        assert sprint_events, "No sprint events recorded"
        for ev in sprint_events:
            assert ev["source"] == "agent", (
                f"Event {ev['type']} has source={ev['source']!r}; "
                "expected 'agent' (sprint_manager violates DB CHECK constraint)"
            )

    def test_ticket_dispatch_events_use_agent_source(self, event_capture):
        """ticket_dispatched and ticket_agent_finished must also use source='agent'."""
        _run_sprint()
        ticket_events = [
            c for c in event_capture
            if c["type"] in ("ticket_dispatched", "ticket_agent_finished")
        ]
        assert ticket_events, "No ticket events recorded"
        for ev in ticket_events:
            assert ev["source"] == "agent", f"Expected source='agent', got {ev['source']!r}"


# ── AC-2: Sprint entry has required fields ─────────────────────────────────────

class TestSprintEntryFields:
    """AC-2: Each sprint log entry shows sprint ID, status, timestamp, and duration."""

    def test_sprint_started_target_is_sprint_id(self, event_capture):
        _run_sprint(label="sprint-99")
        ev = next(c for c in event_capture if c["type"] == "sprint_started")
        assert "sprint-99" in ev["target"]

    def test_sprint_started_has_ticket_count(self, event_capture):
        _run_sprint()
        ev = next(c for c in event_capture if c["type"] == "sprint_started")
        assert "ticket_count" in ev["detail"]
        assert ev["detail"]["ticket_count"] >= 1

    def test_sprint_finished_has_duration(self, event_capture):
        _run_sprint()
        ev = next(c for c in event_capture if c["type"] == "sprint_finished")
        assert "duration" in ev["detail"], "sprint_finished must include duration for AC-2 and AC-7"

    def test_sprint_finished_has_status_fields(self, event_capture):
        _run_sprint()
        ev = next(c for c in event_capture if c["type"] == "sprint_finished")
        for field in ("done", "failed", "skipped"):
            assert field in ev["detail"], f"Missing status field: {field}"


# ── AC-1+AC-5: Sprint events stored under repo project key ────────────────────

class TestSprintEventProjectKey:
    """Sprint events must use the repo name (e.g. 'owner/myrepo'), not the sprint label.

    This is what the /api/projects/{slug}/events endpoint queries against.
    Events stored under the sprint label would never appear in the Activity tab.
    """

    def test_sprint_started_project_is_repo_name(self, event_capture):
        _run_sprint(label="sprint-99", repo_name="zealchaiwut/commander")
        ev = next(c for c in event_capture if c["type"] == "sprint_started")
        assert ev["project"] == "zealchaiwut/commander", (
            f"Expected project='zealchaiwut/commander', got {ev['project']!r}. "
            "Events stored under sprint label are invisible to /api/projects/{slug}/events"
        )

    def test_sprint_finished_project_is_repo_name(self, event_capture):
        _run_sprint(label="sprint-55", repo_name="owner/testrepo")
        ev = next(c for c in event_capture if c["type"] == "sprint_finished")
        assert ev["project"] == "owner/testrepo", (
            f"Expected project='owner/testrepo', got {ev['project']!r}"
        )

    def test_ticket_events_use_repo_project(self, event_capture):
        _run_sprint(label="sprint-99", repo_name="owner/testrepo")
        ticket_events = [
            c for c in event_capture
            if c["type"] in ("ticket_dispatched", "ticket_agent_finished")
        ]
        assert ticket_events
        for ev in ticket_events:
            assert ev["project"] == "owner/testrepo", (
                f"Ticket event {ev['type']} has project={ev['project']!r}"
            )


# ── AC-3/AC-4: Agent events in Activity tab ───────────────────────────────────

class TestAgentEventLogged:
    """AC-3/AC-4: Agent invocation events appear in the events table."""

    def test_agent_finished_written_to_events_table(self, fresh_db):
        """Posting agent_stop to /api/agent-event writes agent_finished to events table."""
        client, srv = _get_test_client(fresh_db)
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            resp = client.post("/api/agent-event", json={
                "session_id": "sess-abc",
                "event_type": "agent_stop",
                "working_dir": "/home/user/dev/myproj",
                "status": "done",
                "name": "coder·myproj·feature/123·#sessab",
            })
        assert resp.status_code == 200

        with fresh_db.get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE type='agent_finished'"
            ).fetchall()
        assert rows, "agent_finished event must be written to events table on agent_stop"

    def test_agent_finished_event_has_required_fields(self, fresh_db):
        """AC-4: agent_finished entry has name/ID, action, timestamp, outcome."""
        client, srv = _get_test_client(fresh_db)
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            client.post("/api/agent-event", json={
                "session_id": "sess-xyz",
                "event_type": "agent_stop",
                "working_dir": "/home/user/dev/myproj",
                "status": "done",
                "name": "coder·myproj·feature/456·#sessxy",
            })

        with fresh_db.get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE type='agent_finished'"
            ).fetchall()
        assert rows
        ev = dict(rows[0])
        detail = json.loads(ev["detail"])

        assert ev["actor"], "actor (agent name/ID) must be present"
        assert ev["timestamp"], "timestamp must be present"
        assert "status" in detail, "detail must include outcome/status"

    def test_agent_started_written_to_events_table(self, fresh_db):
        """AC-3: First tool_use for a new session writes agent_started to events table."""
        client, srv = _get_test_client(fresh_db)
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            resp = client.post("/api/agent-event", json={
                "session_id": "new-sess-001",
                "event_type": "tool_use",
                "working_dir": "/home/user/dev/myproj",
                "status": "working",
                "name": "coder·myproj·feature/789·#newses",
                "tool_name": "Read",
            })
        assert resp.status_code == 200

        with fresh_db.get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE type='agent_started'"
            ).fetchall()
        assert rows, "agent_started event must be written on first tool_use for new session"

    def test_agent_started_not_duplicated_on_subsequent_tool_use(self, fresh_db):
        """Only one agent_started per session."""
        client, srv = _get_test_client(fresh_db)
        payload = {
            "session_id": "dup-sess-001",
            "event_type": "tool_use",
            "working_dir": "/home/user/dev/myproj",
            "status": "working",
            "name": "coder·myproj·feature/789·#dupsess",
            "tool_name": "Read",
        }
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            client.post("/api/agent-event", json=payload)
            client.post("/api/agent-event", json=payload)
            client.post("/api/agent-event", json=payload)

        with fresh_db.get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE type='agent_started' AND action_id='dup-sess-001'"
            ).fetchall()
        assert len(rows) == 1, f"Expected 1 agent_started event, got {len(rows)}"

    def test_agent_finished_source_is_agent(self, fresh_db):
        """AC-8: agent events use source='agent', compatible with events table CHECK constraint."""
        client, srv = _get_test_client(fresh_db)
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            client.post("/api/agent-event", json={
                "session_id": "src-test-sess",
                "event_type": "agent_stop",
                "working_dir": "/home/user/dev/myproj",
                "status": "done",
                "name": "coder·myproj·feature/123·#srctes",
            })

        with fresh_db.get_conn() as conn:
            rows = conn.execute(
                "SELECT source FROM events WHERE type='agent_finished'"
            ).fetchall()
        assert rows
        for row in rows:
            assert row["source"] == "agent"


# ── AC-4: Agent entry detail fields ───────────────────────────────────────────

class TestAgentEntryFields:
    """AC-4: detail includes agent name, action, and outcome."""

    def test_agent_finished_detail_has_status(self, fresh_db):
        client, srv = _get_test_client(fresh_db)
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            client.post("/api/agent-event", json={
                "session_id": "field-test-sess",
                "event_type": "agent_stop",
                "working_dir": "/home/user/dev/myproj",
                "status": "done",
                "name": "coder·myproj·feature/999·#fieldts",
            })

        with fresh_db.get_conn() as conn:
            rows = conn.execute(
                "SELECT detail FROM events WHERE type='agent_finished'"
            ).fetchall()
        assert rows
        detail = json.loads(rows[0]["detail"])
        assert "status" in detail, "outcome (status) must be in detail"

    def test_agent_started_detail_has_role(self, fresh_db):
        client, srv = _get_test_client(fresh_db)
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            client.post("/api/agent-event", json={
                "session_id": "role-test-sess",
                "event_type": "tool_use",
                "working_dir": "/home/user/dev/myproj",
                "status": "working",
                "name": "tester·myproj·feature/123·#roletes",
                "tool_name": "Bash",
            })

        with fresh_db.get_conn() as conn:
            rows = conn.execute(
                "SELECT detail FROM events WHERE type='agent_started'"
            ).fetchall()
        assert rows
        detail = json.loads(rows[0]["detail"])
        assert "role" in detail, "agent role must be in detail for AC-4"


# ── AC-5: Events ordered newest-first ─────────────────────────────────────────

class TestEventOrdering:
    """AC-5: Events are ordered newest-first from the endpoint."""

    def test_events_returned_newest_first(self, fresh_db):
        """GET /api/projects/{slug}/events returns events in descending timestamp order."""
        with fresh_db.get_conn() as conn:
            for ts, etype in [
                ("2026-01-01T10:00:00", "sprint_started"),
                ("2026-01-02T10:00:00", "sprint_finished"),
                ("2026-01-03T10:00:00", "agent_finished"),
            ]:
                conn.execute(
                    "INSERT INTO events (project, timestamp, source, actor, type, target, detail)"
                    " VALUES (?, ?, 'agent', 'system', ?, 'sprint-1', '{}')",
                    ("owner/myproj", ts, etype),
                )
            conn.commit()

        client, srv = _get_test_client(fresh_db)
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            resp = client.get("/api/projects/myproj/events")

        assert resp.status_code == 200
        events = resp.json()
        timestamps = [e["timestamp"] for e in events]
        assert timestamps == sorted(timestamps, reverse=True), (
            "Events must be returned newest-first"
        )


# ── AC-6: Events persist ───────────────────────────────────────────────────────

class TestEventPersistence:
    """AC-6: Events persist across page refreshes (DB-backed)."""

    def test_sprint_events_survive_db_reconnect(self, fresh_db):
        """Events written to DB are readable by a fresh connection."""
        fresh_db.record_event(
            project="owner/myproj",
            source="agent",
            actor="system",
            type="sprint_started",
            target="sprint-1",
            detail={"ticket_count": 3, "levels": 1},
            action_id="run-abc",
        )

        # New connection same DB
        import sqlite3
        with sqlite3.connect(fresh_db.DB_PATH) as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE type='sprint_started'"
            ).fetchall()
        assert rows, "sprint_started event must persist in DB"


# ── AC-7: Metrics fields ───────────────────────────────────────────────────────

class TestMetricsFields:
    """AC-7: Metrics-relevant fields (duration, status, agent name) present."""

    def test_sprint_finished_has_duration_in_detail(self, event_capture):
        _run_sprint()
        ev = next(c for c in event_capture if c["type"] == "sprint_finished")
        assert isinstance(ev["detail"].get("duration"), (int, float)), (
            "duration must be a number (seconds)"
        )

    def test_ticket_agent_finished_has_agent_and_duration(self, event_capture):
        _run_sprint()
        fin_events = [c for c in event_capture if c["type"] == "ticket_agent_finished"]
        assert fin_events
        for ev in fin_events:
            assert "agent" in ev["detail"], "agent name must be in ticket_agent_finished.detail"
            assert "duration" in ev["detail"], "duration must be in ticket_agent_finished.detail"

    def test_agent_finished_detail_has_status_for_metrics(self, fresh_db):
        """agent_finished detail includes status for aggregation."""
        client, srv = _get_test_client(fresh_db)
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            client.post("/api/agent-event", json={
                "session_id": "metrics-sess",
                "event_type": "agent_stop",
                "working_dir": "/home/user/dev/myproj",
                "status": "done",
                "name": "coder·myproj·feature/123·#metric",
            })

        with fresh_db.get_conn() as conn:
            rows = conn.execute(
                "SELECT detail FROM events WHERE type='agent_finished'"
            ).fetchall()
        assert rows
        detail = json.loads(rows[0]["detail"])
        assert "status" in detail


# ── AC-8: Existing Activity tab entries not broken ────────────────────────────

class TestExistingEntriesNotBroken:
    """AC-8: No existing Activity tab entries or UI are broken."""

    def test_existing_events_still_queryable(self, fresh_db):
        """Pre-existing events in the events table still return correctly."""
        with fresh_db.get_conn() as conn:
            conn.execute(
                "INSERT INTO events (project, timestamp, source, actor, type, target, detail)"
                " VALUES ('owner/myproj', '2026-01-01T09:00:00', 'github', 'bot',"
                " 'label_added', '#100', '{\"label\": \"uat\"}')"
            )
            conn.commit()

        client, srv = _get_test_client(fresh_db)
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            resp = client.get("/api/projects/myproj/events")

        assert resp.status_code == 200
        events = resp.json()
        github_events = [e for e in events if e["source"] == "github"]
        assert github_events, "Pre-existing GitHub events must still appear"

    def test_get_project_events_endpoint_still_returns_200(self, fresh_db):
        """Endpoint returns 200 with correct structure for a project with events."""
        with fresh_db.get_conn() as conn:
            conn.execute(
                "INSERT INTO events (project, timestamp, source, actor, type, target, detail)"
                " VALUES ('owner/myproj', '2026-06-09T10:00:00', 'agent', 'system',"
                " 'sprint_started', 'sprint-52', ?)",
                (json.dumps({"ticket_count": 5, "levels": 2}),),
            )
            conn.commit()

        client, srv = _get_test_client(fresh_db)
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            resp = client.get("/api/projects/myproj/events")

        assert resp.status_code == 200
        events = resp.json()
        assert len(events) >= 1
        ev = events[0]
        for field in ("timestamp", "source", "actor", "type", "target", "detail"):
            assert field in ev, f"Missing field: {field}"

    def test_source_filter_agent_still_works(self, fresh_db):
        """Existing source=agent filter must still work after our changes."""
        with fresh_db.get_conn() as conn:
            conn.execute(
                "INSERT INTO events (project, timestamp, source, actor, type, target, detail)"
                " VALUES ('owner/myproj', '2026-06-09T10:00:00', 'agent', 'system',"
                " 'sprint_started', 'sprint-1', '{}')"
            )
            conn.execute(
                "INSERT INTO events (project, timestamp, source, actor, type, target, detail)"
                " VALUES ('owner/myproj', '2026-06-09T09:00:00', 'github', 'bot',"
                " 'label_added', '#1', '{}')"
            )
            conn.commit()

        client, srv = _get_test_client(fresh_db)
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            resp = client.get("/api/projects/myproj/events?source=agent")

        assert resp.status_code == 200
        events = resp.json()
        assert all(e["source"] == "agent" for e in events)


# ── AC-2+AC-7: sprint_cancelled must include duration ────────────────────────

class TestSprintCancelledDuration:
    """sprint_cancelled events must carry duration so AC-2 (failure events show duration)
    and AC-7 (metrics fields present) are satisfied."""

    def test_sprint_cancelled_detail_has_duration(self, event_capture, monkeypatch):
        """run_sprint() emits sprint_cancelled with duration when SystemExit raised."""
        coder_calls = [0]

        def coder_that_cancels(*args, **kwargs):
            coder_calls[0] += 1
            if coder_calls[0] > 1:
                raise SystemExit(130)
            return (True, None)

        monkeypatch.setattr(sm, "_dispatch_coder", coder_that_cancels)
        with pytest.raises(SystemExit):
            _run_sprint(label="sprint-dur-test", repo_name="owner/myrepo")

        cancelled = [c for c in event_capture if c["type"] == "sprint_cancelled"]
        assert cancelled, "sprint_cancelled event must be emitted on SystemExit"
        for ev in cancelled:
            assert "duration" in ev["detail"], (
                "sprint_cancelled detail must include 'duration' for AC-2 (failure events "
                "show duration) and AC-7 (metrics fields). "
                f"Got detail: {ev['detail']}"
            )
            assert isinstance(ev["detail"]["duration"], (int, float)), (
                "duration must be a number (elapsed seconds)"
            )


# ── DB_PATH subprocess fix: server must pass absolute DB_PATH ─────────────────

class TestSubprocessDbPathAbsolute:
    """The server must resolve DB_PATH to an absolute path before passing it to the
    sprint_manager subprocess.  Without this, sprint_manager writes to a DB relative
    to the coder clone CWD (different from the UAT/prd server's DB), so events
    never appear in the Activity tab.

    Implementation: server.py must expose _build_sprint_subprocess_env() which
    resolves the DB_PATH value from os.environ to an absolute path.
    """

    def test_build_sprint_subprocess_env_exists_in_server(self):
        """server.py must expose _build_sprint_subprocess_env() helper."""
        for mod in list(sys.modules.keys()):
            if mod in ("server", "db"):
                del sys.modules[mod]
        import server as srv
        assert hasattr(srv, "_build_sprint_subprocess_env"), (
            "server.py must define _build_sprint_subprocess_env() — "
            "it builds the subprocess env for sprint_manager with an absolute DB_PATH"
        )

    def test_build_sprint_subprocess_env_resolves_db_path_to_absolute(self, monkeypatch):
        """_build_sprint_subprocess_env() returns absolute DB_PATH even when env has relative."""
        for mod in list(sys.modules.keys()):
            if mod in ("server", "db"):
                del sys.modules[mod]
        monkeypatch.setenv("DB_PATH", "./commander.db")
        import server as srv
        env = srv._build_sprint_subprocess_env()
        assert "DB_PATH" in env
        assert os.path.isabs(env["DB_PATH"]), (
            f"DB_PATH in subprocess env must be absolute, got: {env['DB_PATH']!r}. "
            "Relative DB_PATH causes sprint_manager to write to a different DB than the server."
        )

    def test_build_sprint_subprocess_env_keeps_absolute_db_path_unchanged(self, monkeypatch):
        """If DB_PATH is already absolute, _build_sprint_subprocess_env() keeps it."""
        for mod in list(sys.modules.keys()):
            if mod in ("server", "db"):
                del sys.modules[mod]
        abs_path = "/tmp/some/absolute/commander.db"
        monkeypatch.setenv("DB_PATH", abs_path)
        import server as srv
        env = srv._build_sprint_subprocess_env()
        assert env["DB_PATH"] == abs_path

    def test_build_sprint_subprocess_env_strips_anthropic_api_key(self, monkeypatch):
        """ANTHROPIC_API_KEY must be stripped from the subprocess env (existing behavior)."""
        for mod in list(sys.modules.keys()):
            if mod in ("server", "db"):
                del sys.modules[mod]
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret-key")
        monkeypatch.setenv("DB_PATH", "/tmp/test.db")
        import server as srv
        env = srv._build_sprint_subprocess_env()
        assert "ANTHROPIC_API_KEY" not in env, (
            "ANTHROPIC_API_KEY must be stripped from subprocess env"
        )
