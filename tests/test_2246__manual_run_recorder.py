"""Tests for issue #2246 — manual-run recorder: write agent_runs rows from hooks.

Acceptance criteria:
  AC1: hooks write an agent_runs row on session start and close it on Stop
  AC2: rows keyed by issue + role + session_id; sprint_label NULL when no sprint
  AC3: project populated on every new row
  AC4: write is best-effort — DB failure never fails the event
  AC5: behavioral test — simulate hook payload with no sprint label; assert row
       written with expected fields
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

sys.path.insert(0, str(DASHBOARD_DIR))
sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")
os.environ.setdefault("COMMANDER_DISABLE_NEON", "1")

import db as _db  # noqa: E402
import routers.logs_service as svc  # noqa: E402


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def fresh_db(tmp_path):
    """Isolated SQLite DB; clears logs_service session state before/after."""
    db_file = tmp_path / "test_2246.db"
    original = _db.DB_PATH
    _db.DB_PATH = db_file
    _db.init_db()
    svc._seen_agent_sessions.clear()
    yield _db
    _db.DB_PATH = original
    svc._seen_agent_sessions.clear()


_TEST_PROJECT = "zealchaiwut/commander"
_TEST_PROJECTS_LIST = [{"repo": _TEST_PROJECT, "name": "commander"}]


def _make_event(
    session_id: str = "sess-001",
    event_type: str = "tool_use",
    status: str = "working",
    role: str = "coder",
    repo: str = "commander",
    branch: str = "feature/2246-test",
    issue: str = "2246",
):
    name = f"{role}·{repo}·{branch}·#abcdef·issue-{issue}"
    return svc.AgentEvent(
        session_id=session_id,
        event_type=event_type,
        status=status,
        name=name,
        working_dir="/tmp/test",
    )


def _agent_runs_for_session(session_id: str) -> list[dict]:
    with _db.get_conn() as conn:
        _db._create_agent_runs_table(conn)
        rows = conn.execute(
            "SELECT * FROM agent_runs WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── AC5: primary behavioral test ──────────────────────────────────────────────

def test_ac5_no_sprint_label_row_written_with_expected_fields(fresh_db):
    """AC5: simulate hook payload with no sprint label; assert row written correctly.

    This is the canonical behavioral test: feed a real tool_use event through
    receive_agent_event (the path a hook POST hits) and inspect the DB row.
    """
    event = _make_event(
        session_id="sess-ac5",
        event_type="tool_use",
        branch="feature/2246-recorder",  # non-sprint → sprint_label NULL
        issue="2246",
        role="coder",
    )
    with patch.object(svc.projects_module, "load_projects", return_value=_TEST_PROJECTS_LIST):
        svc.receive_agent_event(event)

    rows = _agent_runs_for_session("sess-ac5")
    assert len(rows) == 1, f"expected 1 agent_runs row, got {len(rows)}"
    row = rows[0]
    assert row["session_id"] == "sess-ac5"
    assert row["issue_number"] == 2246
    assert row["agent"] == "coder"
    assert row["project"] == _TEST_PROJECT
    assert row["sprint_label"] is None, (
        f"expected NULL sprint_label for non-sprint branch, got {row['sprint_label']!r}"
    )
    assert row["started_at"] is not None
    assert row["finished_at"] is None  # still open until agent_stop


# ── AC1: open on start, close on Stop ─────────────────────────────────────────

def test_ac1_first_tool_use_opens_agent_runs_row(fresh_db):
    """AC1: first tool_use creates an open agent_runs row."""
    event = _make_event(session_id="sess-ac1-open")
    with patch.object(svc.projects_module, "load_projects", return_value=_TEST_PROJECTS_LIST):
        svc.receive_agent_event(event)

    rows = _agent_runs_for_session("sess-ac1-open")
    assert len(rows) == 1
    assert rows[0]["finished_at"] is None


def test_ac1_subsequent_tool_use_does_not_double_insert(fresh_db):
    """AC1: repeated tool_use events for the same session create only one row."""
    with patch.object(svc.projects_module, "load_projects", return_value=_TEST_PROJECTS_LIST):
        svc.receive_agent_event(_make_event(session_id="sess-ac1-dedup", event_type="tool_use"))
        svc.receive_agent_event(_make_event(session_id="sess-ac1-dedup", event_type="tool_use"))

    rows = _agent_runs_for_session("sess-ac1-dedup")
    assert len(rows) == 1


def test_ac1_agent_stop_closes_row(fresh_db):
    """AC1: agent_stop event closes the open agent_runs row."""
    with patch.object(svc.projects_module, "load_projects", return_value=_TEST_PROJECTS_LIST):
        svc.receive_agent_event(_make_event(session_id="sess-ac1-stop", event_type="tool_use"))
        svc.receive_agent_event(
            _make_event(session_id="sess-ac1-stop", event_type="agent_stop", status="done")
        )

    rows = _agent_runs_for_session("sess-ac1-stop")
    assert len(rows) == 1
    assert rows[0]["finished_at"] is not None
    assert rows[0]["duration_seconds"] is not None


# ── AC2: keying and sprint_label ──────────────────────────────────────────────

def test_ac2_sprint_label_null_for_non_sprint_branch(fresh_db):
    """AC2: sprint_label is NULL when the session branch is not a sprint/ branch."""
    event = _make_event(session_id="sess-ac2-develop", branch="develop")
    with patch.object(svc.projects_module, "load_projects", return_value=_TEST_PROJECTS_LIST):
        svc.receive_agent_event(event)

    rows = _agent_runs_for_session("sess-ac2-develop")
    assert rows[0]["sprint_label"] is None


def test_ac2_sprint_label_populated_for_sprint_branch(fresh_db):
    """AC2: sprint_label is populated when the session branch is sprint/<label>."""
    event = _make_event(session_id="sess-ac2-sprint", branch="sprint/sprint-1023")
    with patch.object(svc.projects_module, "load_projects", return_value=_TEST_PROJECTS_LIST):
        svc.receive_agent_event(event)

    rows = _agent_runs_for_session("sess-ac2-sprint")
    assert rows[0]["sprint_label"] == "sprint-1023"


def test_ac2_row_keyed_by_session_id(fresh_db):
    """AC2: session_id column is stored and uniquely identifies the run."""
    event = _make_event(session_id="sess-ac2-key")
    with patch.object(svc.projects_module, "load_projects", return_value=_TEST_PROJECTS_LIST):
        svc.receive_agent_event(event)

    rows = _agent_runs_for_session("sess-ac2-key")
    assert rows[0]["session_id"] == "sess-ac2-key"
    assert rows[0]["issue_number"] == 2246
    assert rows[0]["agent"] == "coder"


# ── AC3: project populated ────────────────────────────────────────────────────

def test_ac3_project_column_populated(fresh_db):
    """AC3: project is populated on every new row."""
    event = _make_event(session_id="sess-ac3")
    with patch.object(svc.projects_module, "load_projects", return_value=_TEST_PROJECTS_LIST):
        svc.receive_agent_event(event)

    rows = _agent_runs_for_session("sess-ac3")
    assert rows[0]["project"] == _TEST_PROJECT


# ── AC4: best-effort ──────────────────────────────────────────────────────────

def test_ac4_record_manual_run_open_swallows_db_failure():
    """AC4: record_manual_run_open is best-effort — bad DB path raises nothing."""
    original = _db.DB_PATH
    _db.DB_PATH = Path("/nonexistent/directory/commander-test.db")
    try:
        _db.record_manual_run_open(
            "sess-ac4-fail", 999, "coder", "owner/repo", sprint_label=None
        )
    finally:
        _db.DB_PATH = original


def test_ac4_record_manual_run_close_swallows_db_failure():
    """AC4: record_manual_run_close is best-effort — bad DB path raises nothing."""
    original = _db.DB_PATH
    _db.DB_PATH = Path("/nonexistent/directory/commander-test.db")
    try:
        _db.record_manual_run_close("sess-ac4-close-fail")
    finally:
        _db.DB_PATH = original
