"""Tests for issue #1857 — Logs error visibility, AC4.

AC4: /api/projects/{slug}/events supports the `since=` param; behavioral test
     asserts only newer events return. The test exercises the actual service
     code path with a real SQLite DB — not a source-regex check.
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
ROUTERS_DIR = DASHBOARD_DIR / "routers"

if str(ROUTERS_DIR) not in sys.path:
    sys.path.insert(0, str(ROUTERS_DIR))
if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))

import db as _db_module  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path):
    db_file = tmp_path / "test_1857.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


def _reload_activity_service(fresh_db):
    """Reload activity_service against the fresh DB."""
    for mod in list(sys.modules.keys()):
        if "activity_service" in mod:
            del sys.modules[mod]
    import db as _sync_db
    _sync_db.DB_PATH = fresh_db.DB_PATH
    import importlib
    act_svc = importlib.import_module("activity_service")
    return act_svc


def _insert_event(db_mod, project, timestamp, event_type="agent_finished", status="done"):
    detail = json.dumps({"status": status})
    with db_mod.get_conn() as conn:
        conn.execute(
            "INSERT INTO events (project, timestamp, source, actor, type, target, action_id, detail)"
            " VALUES (?, ?, 'agent', 'coder', ?, '', NULL, ?)",
            (project, timestamp, event_type, detail),
        )


_FAKE_PROJECT = [{"repo": "owner/myrepo", "name": "My Repo", "slug": "myrepo"}]


# ── AC4: since= param returns only newer events ───────────────────────────────

def test_ac4_since_excludes_older_events(fresh_db):
    """Events before since= timestamp must not appear in the response."""
    svc = _reload_activity_service(fresh_db)
    _insert_event(fresh_db, "owner/myrepo", "2026-01-01T10:00:00", "agent_finished", "done")
    _insert_event(fresh_db, "owner/myrepo", "2026-01-01T12:00:00", "ticket_failed", "error")

    with patch.object(svc, "projects_module") as mock_pm:
        mock_pm.load_projects.return_value = _FAKE_PROJECT
        result = svc.get_project_events("myrepo", since="2026-01-01T11:00:00")

    timestamps = [e["timestamp"] for e in result]
    assert "2026-01-01T10:00:00" not in timestamps, "Event before since= must be excluded"
    assert any(t >= "2026-01-01T11:00:00" for t in timestamps), "Event after since= must be included"


def test_ac4_since_includes_events_at_boundary(fresh_db):
    """Events at exactly the since= timestamp boundary must be included."""
    svc = _reload_activity_service(fresh_db)
    _insert_event(fresh_db, "owner/myrepo", "2026-02-01T09:00:00", "ticket_failed", "error")
    _insert_event(fresh_db, "owner/myrepo", "2026-02-01T10:00:00", "agent_finished", "done")

    with patch.object(svc, "projects_module") as mock_pm:
        mock_pm.load_projects.return_value = _FAKE_PROJECT
        result = svc.get_project_events("myrepo", since="2026-02-01T10:00:00")

    timestamps = [e["timestamp"] for e in result]
    assert "2026-02-01T09:00:00" not in timestamps
    assert "2026-02-01T10:00:00" in timestamps


def test_ac4_no_since_returns_all_events(fresh_db):
    """Without since=, all events for the project are returned (up to limit)."""
    svc = _reload_activity_service(fresh_db)
    _insert_event(fresh_db, "owner/myrepo", "2026-03-01T08:00:00", "agent_finished", "done")
    _insert_event(fresh_db, "owner/myrepo", "2026-03-01T09:00:00", "agent_finished", "error")
    _insert_event(fresh_db, "owner/myrepo", "2026-03-01T10:00:00", "ticket_failed", "error")

    with patch.object(svc, "projects_module") as mock_pm:
        mock_pm.load_projects.return_value = _FAKE_PROJECT
        result = svc.get_project_events("myrepo")

    timestamps = {e["timestamp"] for e in result}
    assert "2026-03-01T08:00:00" in timestamps
    assert "2026-03-01T09:00:00" in timestamps
    assert "2026-03-01T10:00:00" in timestamps


def test_ac4_since_filters_across_multiple_events(fresh_db):
    """since= correctly excludes all events older than the cutoff across many rows."""
    svc = _reload_activity_service(fresh_db)
    older_ts = [
        "2026-04-01T01:00:00",
        "2026-04-01T02:00:00",
        "2026-04-01T03:00:00",
    ]
    newer_ts = [
        "2026-04-01T05:00:00",
        "2026-04-01T06:00:00",
    ]
    for ts in older_ts:
        _insert_event(fresh_db, "owner/myrepo", ts, "agent_finished", "done")
    for ts in newer_ts:
        _insert_event(fresh_db, "owner/myrepo", ts, "ticket_failed", "error")

    with patch.object(svc, "projects_module") as mock_pm:
        mock_pm.load_projects.return_value = _FAKE_PROJECT
        result = svc.get_project_events("myrepo", since="2026-04-01T04:00:00")

    result_ts = {e["timestamp"] for e in result}
    for ts in older_ts:
        assert ts not in result_ts, f"Older event {ts} must not appear with since= cutoff"
    for ts in newer_ts:
        assert ts in result_ts, f"Newer event {ts} must appear with since= cutoff"


def test_ac4_since_returns_newest_first(fresh_db):
    """Events with since= are returned newest-first (descending timestamp)."""
    svc = _reload_activity_service(fresh_db)
    _insert_event(fresh_db, "owner/myrepo", "2026-05-01T10:00:00", "ticket_failed", "error")
    _insert_event(fresh_db, "owner/myrepo", "2026-05-01T11:00:00", "agent_finished", "error")
    _insert_event(fresh_db, "owner/myrepo", "2026-05-01T12:00:00", "ticket_failed", "error")

    with patch.object(svc, "projects_module") as mock_pm:
        mock_pm.load_projects.return_value = _FAKE_PROJECT
        result = svc.get_project_events("myrepo", since="2026-05-01T09:00:00")

    timestamps = [e["timestamp"] for e in result]
    assert timestamps == sorted(timestamps, reverse=True), "Results must be newest-first"
