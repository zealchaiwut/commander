"""Tests for issue #631: Add GET /api/projects/{slug}/events endpoint.

AC-1: Returns 200 with JSON array sorted newest-first
AC-2: Each event has timestamp, source, actor, type, target, action_id, detail
AC-3: source filter returns only matching events
AC-4: target filter returns only exact-match events
AC-5: since/until restrict results to date range (inclusive)
AC-6: limit caps result count; defaults to 100
AC-7: target=#598 returns only that issue's events
AC-8: target=sprint-47 returns only sprint-scoped events
AC-9: Invalid source returns 400
AC-10: Unknown slug returns 404
AC-11: Empty result set returns [], not an error
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))


# ── Fixtures ──────────────────────────────────────────────────────────────────

import db as _db_module  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path):
    db_file = tmp_path / "test_631.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


def _insert_event(db_mod, project, timestamp, source, actor, etype, target,
                  action_id=None, detail=None):
    with db_mod.get_conn() as conn:
        conn.execute(
            "INSERT INTO events (project, timestamp, source, actor, type, target, action_id, detail)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (project, timestamp, source, actor, etype, target, action_id,
             json.dumps(detail or {})),
        )
        conn.commit()


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


# ── AC-1 + AC-2: 200 with correct fields, sorted newest-first ────────────────

class TestBasicResponse:
    def test_returns_200_array(self, fresh_db):
        _insert_event(fresh_db, "myproj", "2026-01-02T10:00:00", "agent",
                      "coder", "ticket_started", "#100")
        client, srv = _get_test_client(fresh_db)
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            resp = client.get("/api/projects/myproj/events")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_events_have_required_fields(self, fresh_db):
        _insert_event(fresh_db, "myproj", "2026-01-02T10:00:00", "agent",
                      "coder", "ticket_started", "#100",
                      action_id="abc-123", detail={"sprint": "sprint-50"})
        client, srv = _get_test_client(fresh_db)
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            resp = client.get("/api/projects/myproj/events")
        assert resp.status_code == 200
        event = resp.json()[0]
        for field in ("timestamp", "source", "actor", "type", "target", "action_id", "detail"):
            assert field in event, f"Missing field: {field}"

    def test_sorted_newest_first(self, fresh_db):
        _insert_event(fresh_db, "myproj", "2026-01-01T08:00:00", "agent",
                      "coder", "ticket_started", "#1")
        _insert_event(fresh_db, "myproj", "2026-01-03T08:00:00", "agent",
                      "coder", "ticket_started", "#2")
        _insert_event(fresh_db, "myproj", "2026-01-02T08:00:00", "agent",
                      "coder", "ticket_started", "#3")
        client, srv = _get_test_client(fresh_db)
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            resp = client.get("/api/projects/myproj/events")
        assert resp.status_code == 200
        timestamps = [e["timestamp"] for e in resp.json()]
        assert timestamps == sorted(timestamps, reverse=True)


# ── AC-3: source filter ───────────────────────────────────────────────────────

class TestSourceFilter:
    def test_source_agent_filters(self, fresh_db):
        _insert_event(fresh_db, "myproj", "2026-01-01T08:00:00", "agent",
                      "coder", "ticket_started", "#1")
        _insert_event(fresh_db, "myproj", "2026-01-02T08:00:00", "dashboard",
                      "user", "ticket_moved", "#2")
        _insert_event(fresh_db, "myproj", "2026-01-03T08:00:00", "github",
                      "bot", "issue_opened", "#3")
        client, srv = _get_test_client(fresh_db)
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            resp = client.get("/api/projects/myproj/events?source=agent")
        assert resp.status_code == 200
        data = resp.json()
        assert all(e["source"] == "agent" for e in data)
        assert len(data) == 1

    def test_source_dashboard_filters(self, fresh_db):
        _insert_event(fresh_db, "myproj", "2026-01-01T08:00:00", "agent",
                      "coder", "ticket_started", "#1")
        _insert_event(fresh_db, "myproj", "2026-01-02T08:00:00", "dashboard",
                      "user", "ticket_moved", "#2")
        client, srv = _get_test_client(fresh_db)
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            resp = client.get("/api/projects/myproj/events?source=dashboard")
        assert resp.status_code == 200
        assert all(e["source"] == "dashboard" for e in resp.json())

    def test_source_github_filters(self, fresh_db):
        _insert_event(fresh_db, "myproj", "2026-01-01T08:00:00", "github",
                      "bot", "issue_opened", "#1")
        _insert_event(fresh_db, "myproj", "2026-01-02T08:00:00", "agent",
                      "coder", "ticket_started", "#2")
        client, srv = _get_test_client(fresh_db)
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            resp = client.get("/api/projects/myproj/events?source=github")
        assert resp.status_code == 200
        data = resp.json()
        assert all(e["source"] == "github" for e in data)
        assert len(data) == 1


# ── AC-4 + AC-7 + AC-8: target filter ────────────────────────────────────────

class TestTargetFilter:
    def test_target_issue_exact_match(self, fresh_db):
        _insert_event(fresh_db, "myproj", "2026-01-01T08:00:00", "agent",
                      "coder", "ticket_started", "#598")
        _insert_event(fresh_db, "myproj", "2026-01-02T08:00:00", "agent",
                      "coder", "ticket_started", "#599")
        client, srv = _get_test_client(fresh_db)
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            resp = client.get("/api/projects/myproj/events?target=%23598")
        assert resp.status_code == 200
        data = resp.json()
        assert all(e["target"] == "#598" for e in data)
        assert len(data) == 1

    def test_target_issue_no_leak(self, fresh_db):
        """AC-7: #598 returns only #598 events, not other issues."""
        for n in ("#597", "#598", "#599", "#600"):
            _insert_event(fresh_db, "myproj", "2026-01-01T08:00:00", "agent",
                          "coder", "ticket_started", n)
        client, srv = _get_test_client(fresh_db)
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            resp = client.get("/api/projects/myproj/events?target=%23598")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["target"] == "#598"

    def test_target_sprint_filter(self, fresh_db):
        """AC-8: sprint-47 returns only sprint-47 events."""
        _insert_event(fresh_db, "myproj", "2026-01-01T08:00:00", "agent",
                      "coder", "sprint_started", "sprint-47")
        _insert_event(fresh_db, "myproj", "2026-01-02T08:00:00", "agent",
                      "coder", "sprint_started", "sprint-48")
        client, srv = _get_test_client(fresh_db)
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            resp = client.get("/api/projects/myproj/events?target=sprint-47")
        assert resp.status_code == 200
        data = resp.json()
        assert all(e["target"] == "sprint-47" for e in data)
        assert len(data) == 1


# ── AC-5: since/until date range ──────────────────────────────────────────────

class TestDateRange:
    def test_since_filter(self, fresh_db):
        _insert_event(fresh_db, "myproj", "2025-12-31T23:59:59", "agent",
                      "coder", "ticket_started", "#1")
        _insert_event(fresh_db, "myproj", "2026-01-01T00:00:00", "agent",
                      "coder", "ticket_started", "#2")
        _insert_event(fresh_db, "myproj", "2026-06-01T00:00:00", "agent",
                      "coder", "ticket_started", "#3")
        client, srv = _get_test_client(fresh_db)
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            resp = client.get("/api/projects/myproj/events?since=2026-01-01")
        assert resp.status_code == 200
        data = resp.json()
        assert all(e["timestamp"] >= "2026-01-01" for e in data)
        assert len(data) == 2

    def test_until_filter(self, fresh_db):
        _insert_event(fresh_db, "myproj", "2026-01-15T00:00:00", "agent",
                      "coder", "ticket_started", "#1")
        _insert_event(fresh_db, "myproj", "2026-03-31T23:59:59", "agent",
                      "coder", "ticket_started", "#2")
        _insert_event(fresh_db, "myproj", "2026-04-01T00:00:00", "agent",
                      "coder", "ticket_started", "#3")
        client, srv = _get_test_client(fresh_db)
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            resp = client.get("/api/projects/myproj/events?until=2026-03-31")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert all(e["timestamp"] <= "2026-03-31T23:59:59" for e in data)

    def test_since_until_range_inclusive(self, fresh_db):
        """Both boundaries are inclusive (Jan 1 – Mar 31)."""
        _insert_event(fresh_db, "myproj", "2026-01-01T00:00:00", "agent",
                      "coder", "ticket_started", "#1")
        _insert_event(fresh_db, "myproj", "2026-02-15T12:00:00", "agent",
                      "coder", "ticket_started", "#2")
        _insert_event(fresh_db, "myproj", "2026-03-31T23:59:59", "agent",
                      "coder", "ticket_started", "#3")
        _insert_event(fresh_db, "myproj", "2025-12-31T00:00:00", "agent",
                      "coder", "ticket_started", "#4")
        _insert_event(fresh_db, "myproj", "2026-04-01T00:00:00", "agent",
                      "coder", "ticket_started", "#5")
        client, srv = _get_test_client(fresh_db)
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            resp = client.get(
                "/api/projects/myproj/events?since=2026-01-01&until=2026-03-31"
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3


# ── AC-6: limit ───────────────────────────────────────────────────────────────

class TestLimit:
    def test_limit_caps_results(self, fresh_db):
        for i in range(10):
            _insert_event(fresh_db, "myproj", f"2026-01-{i+1:02d}T00:00:00", "agent",
                          "coder", "tick", f"#{i}")
        client, srv = _get_test_client(fresh_db)
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            resp = client.get("/api/projects/myproj/events?limit=5")
        assert resp.status_code == 200
        assert len(resp.json()) == 5

    def test_default_limit_100(self, fresh_db):
        for i in range(110):
            _insert_event(fresh_db, "myproj", f"2026-01-01T{i % 24:02d}:{i % 60:02d}:00",
                          "agent", "coder", "tick", f"#{i}")
        client, srv = _get_test_client(fresh_db)
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            resp = client.get("/api/projects/myproj/events")
        assert resp.status_code == 200
        assert len(resp.json()) <= 100


# ── AC-9: invalid source → 400 ───────────────────────────────────────────────

class TestInvalidSource:
    def test_invalid_source_returns_400(self, fresh_db):
        client, srv = _get_test_client(fresh_db)
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            resp = client.get("/api/projects/myproj/events?source=invalid_value")
        assert resp.status_code == 400

    def test_400_has_descriptive_message(self, fresh_db):
        client, srv = _get_test_client(fresh_db)
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            resp = client.get("/api/projects/myproj/events?source=bad")
        assert resp.status_code == 400
        body = resp.json()
        assert "detail" in body
        assert len(body["detail"]) > 0

    def test_valid_sources_accepted(self, fresh_db):
        client, srv = _get_test_client(fresh_db)
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            for src in ("agent", "dashboard", "github"):
                resp = client.get(f"/api/projects/myproj/events?source={src}")
                assert resp.status_code == 200, f"source={src} should be 200"


# ── AC-10: unknown slug → 404 ────────────────────────────────────────────────

class TestUnknownSlug:
    def test_unknown_slug_returns_404(self, fresh_db):
        client, srv = _get_test_client(fresh_db)
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            resp = client.get("/api/projects/does-not-exist/events")
        assert resp.status_code == 404

    def test_404_message_includes_slug(self, fresh_db):
        client, srv = _get_test_client(fresh_db)
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            resp = client.get("/api/projects/ghost-project/events")
        assert resp.status_code == 404
        body = resp.json()
        assert "ghost-project" in body.get("detail", "")

    def test_known_slug_by_full_repo_name(self, fresh_db):
        """owner/myproj works as the slug too."""
        client, srv = _get_test_client(fresh_db)
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            resp = client.get("/api/projects/myproj/events")
        assert resp.status_code == 200


# ── AC-11: empty result → [] ─────────────────────────────────────────────────

class TestEmptyResult:
    def test_no_events_returns_empty_array(self, fresh_db):
        client, srv = _get_test_client(fresh_db)
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            resp = client.get("/api/projects/myproj/events")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_no_matching_events_returns_empty_array(self, fresh_db):
        _insert_event(fresh_db, "myproj", "2026-01-01T00:00:00", "agent",
                      "coder", "tick", "#1")
        client, srv = _get_test_client(fresh_db)
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            resp = client.get("/api/projects/myproj/events?source=github")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_empty_result_is_list_not_error(self, fresh_db):
        client, srv = _get_test_client(fresh_db)
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            resp = client.get("/api/projects/myproj/events?target=sprint-999")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


# ── Detail JSON round-trip ────────────────────────────────────────────────────

class TestDetailField:
    def test_detail_returned_as_object(self, fresh_db):
        detail = {"sprint": "sprint-50", "count": 3}
        _insert_event(fresh_db, "myproj", "2026-01-01T00:00:00", "agent",
                      "coder", "tick", "#1", detail=detail)
        client, srv = _get_test_client(fresh_db)
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            resp = client.get("/api/projects/myproj/events")
        assert resp.status_code == 200
        returned_detail = resp.json()[0]["detail"]
        assert isinstance(returned_detail, dict)
        assert returned_detail["sprint"] == "sprint-50"
        assert returned_detail["count"] == 3

    def test_null_action_id_allowed(self, fresh_db):
        _insert_event(fresh_db, "myproj", "2026-01-01T00:00:00", "agent",
                      "coder", "tick", "#1", action_id=None)
        client, srv = _get_test_client(fresh_db)
        with patch.object(srv.projects_module, "load_projects", return_value=FAKE_PROJECT):
            resp = client.get("/api/projects/myproj/events")
        assert resp.status_code == 200
        assert resp.json()[0]["action_id"] is None
