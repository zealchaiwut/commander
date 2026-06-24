"""Tests for issue #1494: Narrow bare except in _collect_label_projects.

Anchored to acceptance criteria:
  AC1 — Each except block catches sqlite3.OperationalError (table absent), not bare Exception
  AC2 — Non-OperationalError exceptions propagate out of _collect_label_projects
  AC3 — audit still completes when all three tables are absent
"""
from __future__ import annotations

import importlib
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"

for _p in (str(REPO_ROOT), str(SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture
def audit_module():
    import audit_sprint_collisions as mod
    importlib.reload(mod)
    return mod


# ── AC1: OperationalError is silently swallowed (table absent) ───────────────

def test_ac1_sprints_table_absent_is_handled(audit_module, tmp_path):
    """OperationalError from a missing sprints table is caught silently."""
    db_path = tmp_path / "empty.db"
    conn = sqlite3.connect(str(db_path))
    conn.close()
    # DB exists but has no tables — each query raises OperationalError ("no such table")
    conn2 = sqlite3.connect(str(db_path))
    try:
        label_projects, survivors = audit_module._collect_label_projects(conn2)
    finally:
        conn2.close()
    assert label_projects == {} or len(label_projects) == 0
    assert survivors == {} or len(survivors) == 0


def test_ac1_sprint_history_table_absent_is_handled(audit_module, tmp_path):
    """OperationalError from missing sprint_history is caught, sprints data still returned."""
    db_path = tmp_path / "partial.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE sprints (label TEXT, project TEXT)"
    )
    conn.execute("INSERT INTO sprints VALUES ('sprint-42', 'owner/proj-a')")
    conn.commit()
    conn.close()

    conn2 = sqlite3.connect(str(db_path))
    try:
        label_projects, survivors = audit_module._collect_label_projects(conn2)
    finally:
        conn2.close()

    assert "sprint-42" in label_projects
    assert "owner/proj-a" in label_projects["sprint-42"]
    assert survivors.get("sprint-42") == "owner/proj-a"


def test_ac1_agent_runs_table_absent_is_handled(audit_module, tmp_path):
    """OperationalError from missing agent_runs is caught, other table data still returned."""
    db_path = tmp_path / "partial2.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE sprints (label TEXT, project TEXT)")
    conn.execute("CREATE TABLE sprint_history (label TEXT, project TEXT)")
    conn.execute("INSERT INTO sprint_history VALUES ('sprint-10', 'owner/proj-b')")
    conn.commit()
    conn.close()

    conn2 = sqlite3.connect(str(db_path))
    try:
        label_projects, survivors = audit_module._collect_label_projects(conn2)
    finally:
        conn2.close()

    assert "sprint-10" in label_projects
    assert "owner/proj-b" in label_projects["sprint-10"]


# ── AC2: Non-OperationalError exceptions propagate ───────────────────────────

def test_ac2_non_operational_error_propagates(audit_module):
    """A DatabaseError (not OperationalError) must propagate, not be swallowed."""
    mock_conn = MagicMock()
    mock_conn.execute.side_effect = sqlite3.DatabaseError("simulated corrupt DB")

    with pytest.raises(sqlite3.DatabaseError, match="simulated corrupt DB"):
        audit_module._collect_label_projects(mock_conn)


def test_ac2_programming_error_propagates(audit_module):
    """A ProgrammingError must propagate out of _collect_label_projects."""
    mock_conn = MagicMock()
    mock_conn.execute.side_effect = sqlite3.ProgrammingError("simulated programming error")

    with pytest.raises(sqlite3.ProgrammingError, match="simulated programming error"):
        audit_module._collect_label_projects(mock_conn)


# ── AC3: Full audit completes on empty DB (all tables absent) ────────────────

def test_ac3_run_audit_completes_with_no_tables(audit_module, tmp_path):
    """run_audit completes successfully when DB has no tables at all."""
    db_path = tmp_path / "blank.db"
    sqlite3.connect(str(db_path)).close()  # create empty DB
    runtime_dir = tmp_path / "runtime"

    collisions = audit_module.run_audit(
        db_path=db_path,
        runtime_dir=runtime_dir,
    )
    assert isinstance(collisions, list)
    assert len(collisions) == 0
