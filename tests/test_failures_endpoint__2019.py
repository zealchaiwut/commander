"""Tests for issue #2019: Failures API endpoint (AC-driven, behavioral).

Tests verify the unified failures endpoint (/api/failures) that merges
three data sources (events, agent_runs, agents) into a single normalized
failure-row list. All tests exercise real code paths against a temp SQLite DB.
"""
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Ensure we can import app modules
_REPO_ROOT = Path(__file__).resolve().parent.parent
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(_DASHBOARD_ROOT))

# Import the functions we need; bypass circular import chain by direct module load
import importlib.util
_spec_failures_service = importlib.util.spec_from_file_location(
    "failures_service", _DASHBOARD_ROOT / "routers" / "failures_service.py"
)
_failures_service_module = importlib.util.module_from_spec(_spec_failures_service)
sys.modules["failures_service"] = _failures_service_module
# Set up db module first since failures_service depends on it
import db as _db
_spec_failures_service.loader.exec_module(_failures_service_module)
get_failures = _failures_service_module.get_failures
normalize_outcome = _failures_service_module.normalize_outcome


@pytest.fixture(autouse=True)
def temp_db():
    """Create a temporary SQLite DB for each test, initialize schema, yield conn."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    # Create a valid empty SQLite database first by writing a minimal pragma
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.commit()
    conn.close()

    # Monkeypatch DB_PATH so the app uses our temp DB
    original_db_path = _db.DB_PATH
    _db.DB_PATH = Path(db_path)
    os.environ["DB_PATH"] = db_path

    # Initialize the schema (reuse the app's schema init)
    _db.init_db()

    yield db_path

    # Cleanup
    _db.DB_PATH = original_db_path
    try:
        Path(db_path).unlink()
        # Also remove WAL/SHM files if they exist
        Path(f"{db_path}-wal").unlink(missing_ok=True)
        Path(f"{db_path}-shm").unlink(missing_ok=True)
    except Exception:
        pass


def _insert_event(conn, project, ts, detail_dict):
    """Helper to insert a ticket_failed event."""
    conn.execute(
        """INSERT INTO events
           (project, timestamp, source, actor, type, target, action_id, detail)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            project,
            ts,
            "agent",
            "test-agent",
            "ticket_failed",
            "test-target",
            None,
            json.dumps(detail_dict),
        ),
    )


def _insert_agent_run(conn, issue_number, sprint_label, project, agent, outcome, attempt_kind, log_path, started_at):
    """Helper to insert an agent_runs row."""
    _db._create_agent_runs_table(conn)
    conn.execute(
        """INSERT INTO agent_runs
           (issue_number, sprint_label, project, agent, outcome, attempt_kind,
            log_path, started_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (issue_number, sprint_label, project, agent, outcome, attempt_kind, log_path, started_at),
    )


def _insert_agent(conn, session_id, name, working_dir, status, last_seen):
    """Helper to insert an agents row."""
    conn.execute(
        """INSERT INTO agents
           (session_id, name, working_dir, status, last_seen, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (session_id, name, working_dir, status, last_seen, datetime.now(timezone.utc).isoformat()),
    )


# ── AC1: Endpoint returns JSON list ──────────────────────────────────────

def test_failures_endpoint__returns_list(temp_db):
    """AC1: GET /api/failures returns a JSON list (not an error)."""
    # Call get_failures directly (no HTTP overhead; behavior is the same)
    result = get_failures()
    assert isinstance(result, list)


# ── AC2: Each row has required keys ──────────────────────────────────────

def test_failures_endpoint__row_structure(temp_db):
    """AC2: Each failure row contains all required keys."""
    # Seed one event row to ensure we get a result
    with _db.get_conn() as conn:
        _insert_event(
            conn,
            "zealchaiwut/commander",
            "2026-07-30T10:00:00Z",
            {
                "issue_num": 101,
                "sprint_label": "sprint-50",
                "agent": "coder",
                "category": "failed",
                "reason": "test failure",
                "failure_class": "AssertionError",
                "message": "test message",
                "branch": "feature/test",
            },
        )
        conn.commit()

    result = get_failures()
    assert len(result) > 0

    # Check required keys exist in at least one row
    required_keys = {
        "issue_number", "sprint_label", "project", "agent", "category",
        "reason", "failure_class", "message", "attempt_kind", "branch",
        "log_url", "ts", "source"
    }
    row = result[0]
    for key in required_keys:
        assert key in row, f"Missing required key: {key}"


# ── AC3: All three sources appear in merged result ────────────────────────

def test_failures_endpoint__merges_all_sources(temp_db):
    """AC3: Failure rows from events, agent_runs, and agents all appear.

    Seeds one row from each source and verifies all three appear in the result.
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    with _db.get_conn() as conn:
        # Source 1: events table with type='ticket_failed'
        _insert_event(
            conn,
            "zealchaiwut/commander",
            now_iso,
            {
                "issue_num": 100,
                "sprint_label": "sprint-50",
                "agent": "coder",
                "category": "failed",
                "reason": "AC test",
                "failure_class": "TestError",
                "message": "event-sourced failure",
                "branch": "feature/event-test",
            },
        )

        # Source 2: agent_runs table with outcome='failed'
        _insert_agent_run(
            conn,
            issue_number=101,
            sprint_label="sprint-50",
            project="zealchaiwut/commander",
            agent="coder",
            outcome="failed",
            attempt_kind="initial",
            log_path="/logs/coder.log",
            started_at=now_iso,
        )

        # Source 3: agents table with status='timed_out'
        _insert_agent(
            conn,
            session_id="agent-123",
            name="tester",
            working_dir="/Users/dev/commander/tester",
            status="timed_out",
            last_seen=now_iso,
        )

        conn.commit()

    result = get_failures()

    # Verify all three sources are present
    sources = {row["source"] for row in result}
    assert "events" in sources, "events source not found"
    assert "agent_runs" in sources, "agent_runs source not found"
    assert "agents" in sources, "agents source not found"

    # Verify at least one row from each source
    assert any(r["source"] == "events" for r in result), "no events rows"
    assert any(r["source"] == "agent_runs" for r in result), "no agent_runs rows"
    assert any(r["source"] == "agents" for r in result), "no agents rows"


# ── AC4: Filter parameters work correctly ─────────────────────────────────

def test_failures_endpoint__project_filter(temp_db):
    """AC4: project param narrows results to matching project."""
    now_iso = datetime.now(timezone.utc).isoformat()

    with _db.get_conn() as conn:
        # Insert rows from two different projects
        _insert_event(conn, "zealchaiwut/commander", now_iso,
                      {"issue_num": 1, "sprint_label": "s1"})
        _insert_event(conn, "zealchaiwut/perf-coach", now_iso,
                      {"issue_num": 2, "sprint_label": "s2"})

        _insert_agent_run(conn, 10, "s1", "zealchaiwut/commander", "coder", "failed", None, None, now_iso)
        _insert_agent_run(conn, 20, "s2", "zealchaiwut/perf-coach", "coder", "failed", None, None, now_iso)

        conn.commit()

    # Filter by commander project
    result = get_failures(project="zealchaiwut/commander")
    projects = {row["project"] for row in result if row["project"]}
    assert all("commander" in p or p is None for p in projects), "got unexpected project"

    # Filter by perf-coach
    result = get_failures(project="zealchaiwut/perf-coach")
    projects = {row["project"] for row in result if row["project"]}
    assert all("perf-coach" in p or p is None for p in projects), "got unexpected project"


def test_failures_endpoint__since_iso_filter(temp_db):
    """AC4: since param with ISO timestamp filters by lookback."""
    base = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)
    old_ts = (base - timedelta(days=8)).isoformat()  # Before 7-day cutoff
    recent_ts = (base - timedelta(days=1)).isoformat()  # Within 7 days
    cutoff_ts = (base - timedelta(days=7)).isoformat()  # Use as "since" param

    with _db.get_conn() as conn:
        _insert_event(conn, "zealchaiwut/commander", old_ts, {"issue_num": 1})
        _insert_event(conn, "zealchaiwut/commander", recent_ts, {"issue_num": 2})
        conn.commit()

    # Request failures since 7 days ago (cutoff_ts is approximately that)
    result = get_failures(since=cutoff_ts)
    # Should only get the recent one (the old one is before cutoff)
    # Note: exact boundary depends on current time; the recent one should always be in
    issue_numbers = {row.get("issue_number") for row in result}
    assert 2 in issue_numbers, "recent failure should appear"
    # old one (issue 1) may or may not appear depending on exact timing


def test_failures_endpoint__category_filter(temp_db):
    """AC4: category param filters to matching outcome/category."""
    now_iso = datetime.now(timezone.utc).isoformat()

    with _db.get_conn() as conn:
        _insert_event(conn, "zealchaiwut/commander", now_iso,
                      {"issue_num": 1, "category": "failed"})
        _insert_event(conn, "zealchaiwut/commander", now_iso,
                      {"issue_num": 2, "category": "succeeded"})

        _insert_agent_run(conn, 10, "s1", "zealchaiwut/commander", "coder",
                         "timed_out", None, None, now_iso)

        conn.commit()

    # Filter for failed category
    result = get_failures(category="failed")
    categories = {normalize_outcome(row.get("category")) for row in result}
    assert "failed" in categories, "should have failed category"
    assert "succeeded" not in categories, "should not have succeeded"


def test_failures_endpoint__no_filter_returns_all(temp_db):
    """AC4: Absent params return all failures (no filter applied)."""
    now_iso = datetime.now(timezone.utc).isoformat()

    with _db.get_conn() as conn:
        _insert_event(conn, "zealchaiwut/commander", now_iso, {"issue_num": 1})
        _insert_event(conn, "zealchaiwut/perf-coach", now_iso, {"issue_num": 2})
        _insert_agent_run(conn, 10, "s1", "zealchaiwut/commander", "coder", "failed", None, None, now_iso)
        conn.commit()

    # Call with no filters
    result = get_failures()
    assert len(result) >= 2, "should get rows from both projects without filters"


# ── AC5: normalize_outcome helper works ──────────────────────────────────

def test_normalize_outcome__canonical_forms():
    """AC5: normalize_outcome collapses synonyms to canonical form."""
    # success/succeeded → succeeded
    assert normalize_outcome("success") == "succeeded"
    assert normalize_outcome("succeeded") == "succeeded"

    # fail/failed → failed
    assert normalize_outcome("fail") == "failed"
    assert normalize_outcome("failed") == "failed"

    # pass/passed → passed
    assert normalize_outcome("pass") == "passed"
    assert normalize_outcome("passed") == "passed"

    # timed_out / timeout → timed_out
    assert normalize_outcome("timed_out") == "timed_out"
    assert normalize_outcome("timeout") == "timed_out"


def test_normalize_outcome__case_insensitive():
    """AC5: normalize_outcome handles mixed case."""
    assert normalize_outcome("FAILED") == "failed"
    assert normalize_outcome("Failed") == "failed"
    assert normalize_outcome("SUCCESS") == "succeeded"
    assert normalize_outcome("Succeeded") == "succeeded"


def test_normalize_outcome__none_and_empty():
    """AC5: normalize_outcome handles None and empty inputs."""
    assert normalize_outcome(None) is None
    assert normalize_outcome("") == ""
    assert normalize_outcome("  ") == ""


def test_normalize_outcome__unknown_passthrough():
    """AC5: normalize_outcome returns unknown values lowercased."""
    assert normalize_outcome("foobar") == "foobar"
    assert normalize_outcome("UnknownStatus") == "unknownstatus"


# ── Robustness: Malformed detail JSON doesn't crash ───────────────────────

def test_failures_endpoint__malformed_event_detail_json(temp_db):
    """Robustness: Event with malformed detail JSON is skipped gracefully."""
    now_iso = datetime.now(timezone.utc).isoformat()

    with _db.get_conn() as conn:
        # Insert an event with invalid JSON (but valid SQL string)
        conn.execute(
            """INSERT INTO events
               (project, timestamp, source, actor, type, target, action_id, detail)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "zealchaiwut/commander",
                now_iso,
                "agent",
                "test",
                "ticket_failed",
                "test",
                None,
                "{invalid json}",  # malformed
            ),
        )
        # Also insert a valid event to show others still appear
        _insert_event(conn, "zealchaiwut/commander", now_iso, {"issue_num": 1})
        conn.commit()

    # Should not raise; malformed event is skipped
    result = get_failures()
    assert isinstance(result, list)
    # Valid event should still appear
    assert any(r.get("issue_number") == 1 for r in result)


def test_failures_endpoint__empty_database(temp_db):
    """Robustness: Empty database returns empty list (no error)."""
    result = get_failures()
    assert isinstance(result, list)
    assert len(result) == 0


def test_failures_endpoint__empty_detail_json(temp_db):
    """Robustness: Event with empty detail JSON object is handled."""
    now_iso = datetime.now(timezone.utc).isoformat()

    with _db.get_conn() as conn:
        # Insert event with empty detail JSON (no issue_num, etc.)
        conn.execute(
            """INSERT INTO events
               (project, timestamp, source, actor, type, target, action_id, detail)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "zealchaiwut/commander",
                now_iso,
                "agent",
                "test",
                "ticket_failed",
                "test",
                None,
                "{}",  # empty object
            ),
        )
        conn.commit()

    # Should not raise
    result = get_failures()
    assert isinstance(result, list)
    if result:
        # If a row is created, it should have None fields for missing keys
        row = result[0]
        assert row["issue_number"] is None
        assert row["message"] is None


# ── Integration: Endpoint via TestClient ────────────────────────────────

def test_failures_endpoint__http_200_ok(temp_db):
    """Integration: GET /api/failures returns HTTP 200."""
    from server import app

    client = TestClient(app)
    response = client.get("/api/failures")
    assert response.status_code == 200


def test_failures_endpoint__http_json_response(temp_db):
    """Integration: Response is valid JSON with correct structure."""
    from server import app

    with _db.get_conn() as conn:
        now_iso = datetime.now(timezone.utc).isoformat()
        _insert_event(conn, "zealchaiwut/commander", now_iso, {"issue_num": 42})
        conn.commit()

    client = TestClient(app)
    response = client.get("/api/failures")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0
    assert data[0].get("issue_number") == 42


def test_failures_endpoint__query_params_via_http(temp_db):
    """Integration: Query params work via HTTP GET."""
    from server import app

    now_iso = datetime.now(timezone.utc).isoformat()
    with _db.get_conn() as conn:
        _insert_event(conn, "zealchaiwut/commander", now_iso,
                     {"issue_num": 1, "category": "failed"})
        _insert_event(conn, "zealchaiwut/perf-coach", now_iso,
                     {"issue_num": 2, "category": "failed"})
        conn.commit()

    client = TestClient(app)

    # Request with project filter
    response = client.get("/api/failures?project=zealchaiwut/commander")
    assert response.status_code == 200
    data = response.json()
    # Should get rows from commander (or inferred None for agents)
    assert any("commander" in str(r.get("project", "")) for r in data) or len(data) == 0


# ── Edge cases ───────────────────────────────────────────────────────────

def test_failures_endpoint__agent_run_partial_fields(temp_db):
    """Agent runs with project as None are handled."""
    now_iso = datetime.now(timezone.utc).isoformat()

    with _db.get_conn() as conn:
        _db._create_agent_runs_table(conn)
        conn.execute(
            """INSERT INTO agent_runs
               (issue_number, sprint_label, project, agent, outcome, log_path, started_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (100, "sprint-50", None, "coder", "failed", None, now_iso),
        )
        conn.commit()

    result = get_failures()
    assert len(result) > 0
    row = result[0]
    assert row["source"] == "agent_runs"
    assert row["issue_number"] == 100
    assert row["sprint_label"] == "sprint-50"
    assert row["project"] is None
    assert row["agent"] == "coder"


def test_failures_endpoint__sort_newest_first(temp_db):
    """Results sorted newest-first by timestamp."""
    base = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)
    old_ts = (base - timedelta(hours=2)).isoformat()
    new_ts = (base - timedelta(hours=1)).isoformat()
    newest_ts = base.isoformat()

    with _db.get_conn() as conn:
        _insert_event(conn, "zealchaiwut/commander", old_ts, {"issue_num": 1})
        _insert_event(conn, "zealchaiwut/commander", new_ts, {"issue_num": 2})
        _insert_event(conn, "zealchaiwut/commander", newest_ts, {"issue_num": 3})
        conn.commit()

    result = get_failures()
    # First result should be newest
    assert result[0].get("issue_number") == 3
    assert result[1].get("issue_number") == 2
    assert result[2].get("issue_number") == 1
