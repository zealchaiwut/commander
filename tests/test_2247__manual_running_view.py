"""Tests for issue #2247 — AC2: Running view renders live manual sessions.

AC2: The Running view renders live manual sessions via GET /api/manual/live.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

sys.path.insert(0, str(DASHBOARD_DIR))
sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest-2247-ac2.db")
os.environ.setdefault("COMMANDER_DISABLE_NEON", "1")

import db as _db  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path):
    db_file = tmp_path / "test_2247_ac2.db"
    original = _db.DB_PATH
    _db.DB_PATH = db_file
    _db.init_db()
    yield _db
    _db.DB_PATH = original


@pytest.fixture
def client(fresh_db):
    """TestClient wired to a minimal app that mounts the sprint_live router."""
    from fastapi import FastAPI
    import routers.sprint_live as sprint_live_router

    app = FastAPI()
    app.include_router(sprint_live_router.router)
    return TestClient(app, raise_server_exceptions=True)


def _insert_run(conn, issue_number, agent, sprint_label, project, session_id=None, finished_at=None):
    conn.execute(
        "INSERT INTO agent_runs "
        "(issue_number, sprint_label, agent, started_at, project, session_id, finished_at) "
        "VALUES (?, ?, ?, datetime('now'), ?, ?, ?)",
        (issue_number, sprint_label, agent, project, session_id, finished_at),
    )
    conn.commit()


# ── AC2: /api/manual/live returns active manual sessions ─────────────────────

def test_ac2_manual_live_returns_active_null_sprint_sessions(client, fresh_db):
    """AC2: GET /api/manual/live returns sessions where sprint_label IS NULL and finished_at IS NULL."""
    with _db.get_conn() as conn:
        _db._create_agent_runs_table(conn)
        _insert_run(conn, 500, "coder", None, "owner/repo", session_id="sess-live-1")
        # This one is finished — should NOT appear
        _insert_run(conn, 500, "coder", None, "owner/repo", session_id="sess-live-done",
                    finished_at="2026-01-01T00:00:00")
        # Sprint-labeled run — should NOT appear
        _insert_run(conn, 500, "coder", "sprint-5", "owner/repo", session_id="sess-sprint")

    resp = client.get("/api/manual/live")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "sessions" in data

    session_ids = {s["session_id"] for s in data["sessions"]}
    assert "sess-live-1" in session_ids, "Active manual session must appear in /api/manual/live"
    assert "sess-live-done" not in session_ids, "Finished session must not appear"
    assert "sess-sprint" not in session_ids, "Sprint session must not appear in manual view"


def test_ac2_manual_live_empty_when_no_active_sessions(client, fresh_db):
    """AC2: GET /api/manual/live returns empty sessions list when none are active."""
    resp = client.get("/api/manual/live")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["sessions"] == [], f"Expected empty list, got {data['sessions']}"


def test_ac2_manual_live_project_filter(client, fresh_db):
    """AC2: GET /api/manual/live?project= filters to the given project."""
    with _db.get_conn() as conn:
        _db._create_agent_runs_table(conn)
        _insert_run(conn, 501, "coder", None, "owner/repo-a", session_id="sess-a")
        _insert_run(conn, 502, "coder", None, "owner/repo-b", session_id="sess-b")

    resp = client.get("/api/manual/live", params={"project": "owner/repo-a"})
    assert resp.status_code == 200
    session_ids = {s["session_id"] for s in resp.json()["sessions"]}
    assert "sess-a" in session_ids
    assert "sess-b" not in session_ids
