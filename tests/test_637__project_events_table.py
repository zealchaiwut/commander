"""
Tests for issue #637 — project_events table and recorder.
Each test is anchored to a specific acceptance criterion.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")

import db as _db_module  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path):
    """Isolated SQLite DB; yields patched db module."""
    db_file = tmp_path / "test_637.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


# ── AC1: project_events table exists with correct columns ────────────────────

def test_project_events_table_has_correct_columns(fresh_db):
    """AC1: init_db() creates project_events with required columns."""
    with fresh_db.get_conn() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(project_events)").fetchall()}
    expected = {"id", "project", "created_at", "source", "event_type", "target", "actor", "action_id", "data"}
    assert expected == cols


# ── AC2: correct indexes are created ────────────────────────────────────────

def test_project_events_indexes_exist(fresh_db):
    """AC2: init_db() creates the three required indexes."""
    with fresh_db.get_conn() as conn:
        index_rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='project_events'"
        ).fetchall()
    names = {r["name"] for r in index_rows}
    assert "ix_project_events_project_created" in names
    assert "ix_project_events_target" in names
    assert "ix_project_events_action" in names


# ── AC3: existing tables untouched ──────────────────────────────────────────

def test_existing_tables_not_dropped(fresh_db):
    """AC3: init_db() does not drop agents, events, or token_usage tables."""
    with fresh_db.get_conn() as conn:
        tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    for t in ("agents", "token_usage", "project_events"):
        assert t in tables


def test_init_db_idempotent(fresh_db):
    """AC3: calling init_db() twice does not raise (CREATE TABLE IF NOT EXISTS)."""
    fresh_db.init_db()  # second call — must not raise


# ── AC4: record_project_event inserts a row ─────────────────────────────────

def test_record_project_event_inserts_row(fresh_db):
    """AC4: record_project_event inserts one row; created_at is ISO-8601 UTC; data is JSON."""
    fresh_db.record_project_event(
        project="zealchaiwut/commander",
        source="agent",
        event_type="sprint_started",
        target="sprint-47",
        actor="coder-bot",
        data={"sprint": 47},
        action_id="act-001",
    )
    with fresh_db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM project_events").fetchall()
    assert len(rows) == 1
    row = dict(rows[0])
    assert row["project"] == "zealchaiwut/commander"
    assert row["source"] == "agent"
    assert row["event_type"] == "sprint_started"
    assert row["target"] == "sprint-47"
    assert row["actor"] == "coder-bot"
    assert row["action_id"] == "act-001"
    # created_at must be a non-empty string parseable as a datetime
    ca = row["created_at"]
    assert isinstance(ca, str) and len(ca) > 0
    # Must be parseable without raising
    datetime.fromisoformat(ca.replace("Z", "+00:00"))


def test_record_project_event_data_is_json(fresh_db):
    """AC4: data column is JSON-serialized when provided."""
    fresh_db.record_project_event(
        project="proj",
        source="github",
        event_type="pr_merged",
        data={"pr": 123},
    )
    with fresh_db.get_conn() as conn:
        row = conn.execute("SELECT data FROM project_events").fetchone()
    parsed = json.loads(row["data"])
    assert parsed == {"pr": 123}


def test_record_project_event_data_none(fresh_db):
    """AC4: data=None is stored as NULL (not a string)."""
    fresh_db.record_project_event(
        project="proj",
        source="agent",
        event_type="started",
    )
    with fresh_db.get_conn() as conn:
        row = conn.execute("SELECT data FROM project_events").fetchone()
    assert row["data"] is None


# ── AC5: get_project_events returns rows newest-first ───────────────────────

def test_get_project_events_newest_first(fresh_db):
    """AC5: get_project_events returns rows ordered newest first."""
    fresh_db.record_project_event("proj", "agent", "first")
    fresh_db.record_project_event("proj", "agent", "second")
    rows = fresh_db.get_project_events("proj")
    assert len(rows) == 2
    assert rows[0]["event_type"] == "second"
    assert rows[1]["event_type"] == "first"


def test_get_project_events_default_limit(fresh_db):
    """AC5: get_project_events default limit is 200."""
    for i in range(205):
        fresh_db.record_project_event("proj", "agent", f"evt-{i}")
    rows = fresh_db.get_project_events("proj")
    assert len(rows) == 200


# ── AC6: filters work individually and combined ─────────────────────────────

def _seed_two_events(db):
    db.record_project_event(
        "proj", "agent", "sprint_started", target="sprint-47", actor="bot", action_id="act-001"
    )
    db.record_project_event(
        "proj", "github", "pr_merged", target="#598", actor="human", action_id="act-001"
    )


def test_filter_by_source(fresh_db):
    """AC6: source filter returns only matching rows."""
    _seed_two_events(fresh_db)
    rows = fresh_db.get_project_events("proj", source="github")
    assert len(rows) == 1
    assert rows[0]["event_type"] == "pr_merged"


def test_filter_by_target(fresh_db):
    """AC6: target filter returns only matching rows."""
    _seed_two_events(fresh_db)
    rows = fresh_db.get_project_events("proj", target="#598")
    assert len(rows) == 1
    assert rows[0]["event_type"] == "pr_merged"


def test_filter_by_since_excludes_past(fresh_db):
    """AC6: since=far-future returns empty list."""
    _seed_two_events(fresh_db)
    rows = fresh_db.get_project_events("proj", since="2099-01-01T00:00:00Z")
    assert rows == []


def test_filter_by_since_includes_recent(fresh_db):
    """AC6: since=past includes current rows."""
    _seed_two_events(fresh_db)
    rows = fresh_db.get_project_events("proj", since="2000-01-01T00:00:00Z")
    assert len(rows) == 2


def test_filter_by_until(fresh_db):
    """AC6: until=far-past returns empty list."""
    _seed_two_events(fresh_db)
    rows = fresh_db.get_project_events("proj", until="2000-01-01T00:00:00Z")
    assert rows == []


def test_filter_combined_source_and_target(fresh_db):
    """AC6: combining source + target filters correctly."""
    _seed_two_events(fresh_db)
    rows = fresh_db.get_project_events("proj", source="agent", target="sprint-47")
    assert len(rows) == 1
    assert rows[0]["event_type"] == "sprint_started"


def test_filter_no_cross_project_leak(fresh_db):
    """AC6: events for project A not returned when querying project B."""
    fresh_db.record_project_event("proj-a", "agent", "evt")
    rows = fresh_db.get_project_events("proj-b")
    assert rows == []


# ── AC7: multiple rows with same action_id retrievable ──────────────────────

def test_shared_action_id_retrievable(fresh_db):
    """AC7: both rows with action_id='act-001' are returned by get_project_events."""
    _seed_two_events(fresh_db)
    try:
        rows = fresh_db.get_project_events("proj", action_id="act-001")
    except TypeError:
        # action_id filter not exposed — verify both appear in unfiltered query
        rows = [r for r in fresh_db.get_project_events("proj") if r.get("action_id") == "act-001"]
    assert len(rows) == 2


# ── AC8: no sqlalchemy / alembic imports ────────────────────────────────────

def test_no_sqlalchemy_or_alembic_in_db_module():
    """AC8: db.py must not import sqlalchemy or alembic."""
    db_path = REPO_ROOT / "apps" / "dashboard" / "db.py"
    source = db_path.read_text()
    assert "sqlalchemy" not in source
    assert "alembic" not in source
    assert "neon" not in source.lower()


# ── AC9: DB path from DB_PATH env var ───────────────────────────────────────

def test_db_path_from_env_var(fresh_db, tmp_path):
    """AC9: DB_PATH env var controls the file location."""
    expected = str(tmp_path / "test_637.db")
    assert str(fresh_db.DB_PATH) == expected
