"""Tests for issue #920 AC1 — backend field in agent_run records.

AC1: Every coder agent_run record and activity event includes a `backend`
field ("cline" or "claude-code").
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SM_DIR = REPO_ROOT / "services" / "sprint_manager"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))
sys.path.insert(0, str(SM_DIR))

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))

import db as _db_module  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path):
    """Isolated SQLite DB."""
    db_file = tmp_path / "test_920.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


def test_agent_runs_table_has_backend_column(fresh_db):
    """AC1: agent_runs table must have a 'backend' column."""
    with fresh_db.get_conn() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(agent_runs)").fetchall()}
    assert "backend" in cols, "agent_runs missing 'backend' column"


def test_record_agent_start_stores_backend_cline(fresh_db):
    """AC1: record_agent_start stores backend='cline' when provided."""
    fresh_db.record_agent_start(920, "sprint-71", "coder", backend="cline")
    rows = fresh_db.agent_runs_for_issue(920, "sprint-71")
    assert rows, "expected one agent_runs row"
    assert rows[0]["backend"] == "cline"


def test_record_agent_start_stores_backend_claude_code(fresh_db):
    """AC1: record_agent_start stores backend='claude-code' when provided."""
    fresh_db.record_agent_start(920, "sprint-71", "coder", backend="claude-code")
    rows = fresh_db.agent_runs_for_issue(920, "sprint-71")
    assert rows
    assert rows[0]["backend"] == "claude-code"


def test_record_agent_start_backend_nullable(fresh_db):
    """AC1: backend is nullable — existing call sites without the arg still work."""
    fresh_db.record_agent_start(920, "sprint-71", "tester")
    rows = fresh_db.agent_runs_for_issue(920, "sprint-71")
    assert rows
    assert rows[0]["backend"] is None


def test_sprint_manager_passes_backend_to_db_agent_start():
    """AC1: sprint_manager._db_agent_start_sm accepts and passes the backend field."""
    src = (SM_DIR / "sprint_manager.py").read_text()
    # The helper must forward a backend kwarg to db.record_agent_start
    assert "backend=" in src, "_db_agent_start_sm must forward backend= kwarg"
    assert "record_agent_start(" in src


def test_activity_service_enriches_backend_from_agent_runs():
    """AC1: activity_service enriches agent_finished events with backend from agent_runs."""
    src = (DASHBOARD_DIR / "routers" / "activity_service.py").read_text()
    assert "backend" in src, "activity_service must enrich events with backend field"
