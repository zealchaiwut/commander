"""Tests for issue #2247 — AC3: agent_runs_for_sprint returns manual runs for a sprint.

AC3: sprint_finish.py's agent_runs_for_sprint returns rows for a sprint assembled
     from manual (NULL sprint_label) runs when those issues carry the sprint label.
AC4: Sprints that do have a label still return their rows as before.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

sys.path.insert(0, str(DASHBOARD_DIR))
sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest-2247-ac3.db")
os.environ.setdefault("COMMANDER_DISABLE_NEON", "1")

import db as _db  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path):
    db_file = tmp_path / "test_2247_ac3.db"
    original = _db.DB_PATH
    _db.DB_PATH = db_file
    _db.init_db()
    yield _db
    _db.DB_PATH = original


def _insert_run(conn, issue_number, agent, sprint_label, project, session_id=None):
    conn.execute(
        "INSERT INTO agent_runs (issue_number, sprint_label, agent, started_at, project, session_id) "
        "VALUES (?, ?, ?, datetime('now'), ?, ?)",
        (issue_number, sprint_label, agent, project, session_id),
    )
    conn.commit()


def _insert_issue_with_label(conn, repo, issue_number, sprint_label):
    """Insert a mirrored issue with the given sprint label."""
    labels = json.dumps([sprint_label])
    conn.execute(
        "INSERT OR REPLACE INTO issues (repo, issue_number, title, state, labels) "
        "VALUES (?, ?, 'Test Issue', 'open', ?)",
        (repo, issue_number, labels),
    )
    conn.commit()


# ── AC3: manual runs included when issue has sprint label in issues table ─────

def test_ac3_manual_runs_included_for_sprint_issues(fresh_db):
    """AC3: agent_runs_for_sprint includes NULL-sprint rows for issues labelled with the sprint."""
    project = "owner/test-repo"
    sprint = "sprint-42"

    with _db.get_conn() as conn:
        _db._create_agent_runs_table(conn)
        _db._create_issues_table(conn)

        # Issue 600 carries sprint-42 label in the issues mirror
        _insert_issue_with_label(conn, project, 600, sprint)

        # Manual run for issue 600 (sprint_label IS NULL)
        _insert_run(conn, 600, "coder", None, project, session_id="manual-sess-1")

        # Sprint-labeled run for a different issue (not manual)
        _insert_issue_with_label(conn, project, 601, sprint)
        _insert_run(conn, 601, "coder", sprint, project, session_id="sprint-sess-1")

    rows = _db.agent_runs_for_sprint(sprint, project=project)
    issue_numbers = {r["issue_number"] for r in rows}

    assert 600 in issue_numbers, (
        "Manual run for issue 600 must be included when that issue carries the sprint label"
    )
    assert 601 in issue_numbers, "Sprint-labeled run for issue 601 must still be included"


def test_ac3_manual_run_from_different_project_excluded(fresh_db):
    """AC3: manual runs from a different project are not included even if issue shares sprint label."""
    project_a = "owner/repo-a"
    project_b = "owner/repo-b"
    sprint = "sprint-50"

    with _db.get_conn() as conn:
        _db._create_agent_runs_table(conn)
        _db._create_issues_table(conn)

        _insert_issue_with_label(conn, project_a, 700, sprint)
        _insert_run(conn, 700, "coder", None, project_b, session_id="cross-project")

    rows = _db.agent_runs_for_sprint(sprint, project=project_a)
    session_ids = {r.get("session_id") for r in rows}
    assert "cross-project" not in session_ids, (
        "Manual run from a different project must not appear in another project's sprint"
    )


def test_ac3_no_issues_mirror_rows_yields_no_manual_runs(fresh_db):
    """AC3: when the issues table has no entries for the sprint, manual runs are not returned."""
    project = "owner/no-issues-repo"
    sprint = "sprint-77"

    with _db.get_conn() as conn:
        _db._create_agent_runs_table(conn)
        _db._create_issues_table(conn)
        # Manual run exists but no issue-mirror row links it to the sprint
        _insert_run(conn, 800, "coder", None, project, session_id="orphan-manual")

    rows = _db.agent_runs_for_sprint(sprint, project=project)
    session_ids = {r.get("session_id") for r in rows}
    assert "orphan-manual" not in session_ids, (
        "Manual run with no matching issue-mirror entry must not appear"
    )


# ── AC4: existing labeled-sprint rows still returned as before ────────────────

def test_ac4_labeled_sprint_rows_returned_unchanged(fresh_db):
    """AC4: agent_runs_for_sprint still returns sprint-labeled rows exactly as before."""
    project = "owner/stable-repo"
    sprint = "sprint-1"

    with _db.get_conn() as conn:
        _db._create_agent_runs_table(conn)
        _db._create_issues_table(conn)
        _insert_run(conn, 900, "coder", sprint, project, session_id="labeled-1")
        _insert_run(conn, 900, "tester", sprint, project, session_id="labeled-2")

    rows = _db.agent_runs_for_sprint(sprint, project=project)
    session_ids = {r["session_id"] for r in rows}
    assert "labeled-1" in session_ids
    assert "labeled-2" in session_ids


def test_ac4_no_project_arg_unchanged(fresh_db):
    """AC4: agent_runs_for_sprint with no project arg still returns labeled rows (backward compat)."""
    sprint = "sprint-2"

    with _db.get_conn() as conn:
        _db._create_agent_runs_table(conn)
        _db._create_issues_table(conn)
        _insert_run(conn, 901, "coder", sprint, "", session_id="no-project-row")

    rows = _db.agent_runs_for_sprint(sprint)
    session_ids = {r.get("session_id") for r in rows}
    assert "no-project-row" in session_ids
