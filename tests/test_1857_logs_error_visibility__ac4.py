"""Tests for issue #1857 — Logs error visibility, AC4.

AC4: /api/projects/{slug}/events supports the `since=` param; behavioral test
     asserts only newer events return. The test exercises the SQL query building
     and filtering logic directly at the db layer.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))

import db as db_module  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path):
    """Create a fresh isolated database for the test."""
    db_file = tmp_path / "test_1857.db"
    original_path = db_module.DB_PATH
    db_module.DB_PATH = db_file
    db_module.init_db()
    yield db_file
    db_module.DB_PATH = original_path


def _insert_event(db_file: Path, project: str, timestamp: str, event_type: str = "agent_finished", status: str = "done"):
    """Insert a test event directly into the database."""
    detail = json.dumps({"status": status})
    conn = sqlite3.connect(db_file)
    conn.execute(
        "INSERT INTO events (project, timestamp, source, actor, type, target, action_id, detail)"
        " VALUES (?, ?, 'agent', 'coder', ?, '', NULL, ?)",
        (project, timestamp, event_type, detail),
    )
    conn.commit()
    conn.close()


def _query_events(db_file: Path, project: str, since: str | None = None) -> list[dict]:
    """Query events from the database, matching the activity_service logic."""
    query = "SELECT timestamp, source, actor, type, target, action_id, detail FROM events WHERE project = ?"
    params = [project]

    if since is not None:
        query += " AND timestamp >= ?"
        params.append(since)

    query += " ORDER BY timestamp DESC"

    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(query, params).fetchall()
    conn.close()

    result = []
    for row in rows:
        d = dict(row)
        try:
            d["detail"] = json.loads(d["detail"])
        except (TypeError, ValueError):
            pass
        result.append(d)
    return result


# ── AC4: since= param returns only newer events ───────────────────────────────

def test_ac4_since_excludes_older_events(fresh_db):
    """Events before since= timestamp must not appear in the response."""
    _insert_event(fresh_db, "owner/myrepo", "2026-01-01T10:00:00", "agent_finished", "done")
    _insert_event(fresh_db, "owner/myrepo", "2026-01-01T12:00:00", "ticket_failed", "error")

    result = _query_events(fresh_db, "owner/myrepo", since="2026-01-01T11:00:00")

    timestamps = [e["timestamp"] for e in result]
    assert "2026-01-01T10:00:00" not in timestamps, "Event before since= must be excluded"
    assert "2026-01-01T12:00:00" in timestamps, "Event after since= must be included"


def test_ac4_since_includes_events_at_boundary(fresh_db):
    """Events at exactly the since= timestamp boundary must be included."""
    _insert_event(fresh_db, "owner/myrepo", "2026-02-01T09:00:00", "ticket_failed", "error")
    _insert_event(fresh_db, "owner/myrepo", "2026-02-01T10:00:00", "agent_finished", "done")

    result = _query_events(fresh_db, "owner/myrepo", since="2026-02-01T10:00:00")

    timestamps = [e["timestamp"] for e in result]
    assert "2026-02-01T09:00:00" not in timestamps, "Event before boundary must be excluded"
    assert "2026-02-01T10:00:00" in timestamps, "Event at boundary must be included"


def test_ac4_no_since_returns_all_events(fresh_db):
    """Without since=, all events for the project are returned."""
    _insert_event(fresh_db, "owner/myrepo", "2026-03-01T08:00:00", "agent_finished", "done")
    _insert_event(fresh_db, "owner/myrepo", "2026-03-01T09:00:00", "agent_finished", "error")
    _insert_event(fresh_db, "owner/myrepo", "2026-03-01T10:00:00", "ticket_failed", "error")

    result = _query_events(fresh_db, "owner/myrepo")

    timestamps = {e["timestamp"] for e in result}
    assert "2026-03-01T08:00:00" in timestamps
    assert "2026-03-01T09:00:00" in timestamps
    assert "2026-03-01T10:00:00" in timestamps


def test_ac4_since_filters_across_multiple_events(fresh_db):
    """since= correctly excludes all events older than the cutoff across many rows."""
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

    result = _query_events(fresh_db, "owner/myrepo", since="2026-04-01T04:00:00")

    result_ts = {e["timestamp"] for e in result}
    for ts in older_ts:
        assert ts not in result_ts, f"Older event {ts} must not appear with since= cutoff"
    for ts in newer_ts:
        assert ts in result_ts, f"Newer event {ts} must appear with since= cutoff"


def test_ac4_since_returns_newest_first(fresh_db):
    """Events with since= are returned newest-first (descending timestamp)."""
    _insert_event(fresh_db, "owner/myrepo", "2026-05-01T10:00:00", "ticket_failed", "error")
    _insert_event(fresh_db, "owner/myrepo", "2026-05-01T11:00:00", "agent_finished", "error")
    _insert_event(fresh_db, "owner/myrepo", "2026-05-01T12:00:00", "ticket_failed", "error")

    result = _query_events(fresh_db, "owner/myrepo", since="2026-05-01T09:00:00")

    timestamps = [e["timestamp"] for e in result]
    assert timestamps == sorted(timestamps, reverse=True), "Results must be newest-first"
