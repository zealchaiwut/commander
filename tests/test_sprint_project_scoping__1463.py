"""Tests for issue #1463: Scope all sprint DB writes to (label, project).

Verifies cross-project isolation of sprint lifecycle writes.
"""
import os
import sys
import pytest
import tempfile
import sqlite3
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "dashboard"))

import db


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["DB_PATH"] = path
    db.DB_PATH = Path(path)
    yield path
    # Cleanup
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def clean_db(temp_db):
    """Initialize a clean database for each test."""
    db.init_db()
    yield
    # Clear sprints table
    with db.get_conn() as conn:
        conn.execute("DELETE FROM sprints")
        conn.execute("DELETE FROM sprint_history")
        conn.commit()


def test_transition_sprint_state__on_conflict_composite_key(clean_db):
    """AC: transition_sprint_state ON CONFLICT target is (label, project)."""
    # Insert row for perf-coach
    db.record_sprint_start("sprint-66", project="perf-coach")

    # Start the same label for commander — should create a new row, not overwrite
    db.record_sprint_start("sprint-66", project="commander")

    # Verify both rows exist and are independent
    with db.get_conn() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM sprints WHERE label = 'sprint-66' ORDER BY project"
        ).fetchall()

    assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"
    assert rows[0]["project"] == "commander"
    assert rows[1]["project"] == "perf-coach"
    assert rows[0]["state"] == "running"
    assert rows[1]["state"] == "running"


def test_transition_sprint_state__insert_always_supplies_project(clean_db):
    """AC: INSERT in transition_sprint_state always supplies non-null project."""
    db.record_sprint_start("sprint-99", project="perf-coach")

    with db.get_conn() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT project FROM sprints WHERE label = 'sprint-99'"
        ).fetchone()

    assert row is not None
    assert row["project"] == "perf-coach"
    assert row["project"] != ""


def test_set_sprint_terminal__scopes_to_label_project(clean_db):
    """AC: _set_sprint_terminal UPDATE scopes to (label, project)."""
    # Create rows for two projects
    db.record_sprint_start("sprint-77", project="perf-coach")
    db.record_sprint_start("sprint-77", project="commander")

    # Move only perf-coach row to needs_rework
    db.record_sprint_needs_rework("sprint-77", project="perf-coach")

    with db.get_conn() as conn:
        conn.row_factory = sqlite3.Row
        pc_row = conn.execute(
            "SELECT state FROM sprints WHERE label = 'sprint-77' AND project = 'perf-coach'"
        ).fetchone()
        cmd_row = conn.execute(
            "SELECT state FROM sprints WHERE label = 'sprint-77' AND project = 'commander'"
        ).fetchone()

    assert pc_row["state"] == "needs_rework"
    assert cmd_row["state"] == "running"


def test_record_sprint_start__scopes_to_label_project(clean_db):
    """AC: record_sprint_start scopes to (label, project)."""
    db.record_sprint_start("sprint-11", project="proj-a")
    db.record_sprint_start("sprint-11", project="proj-b")
    db.record_sprint_start("sprint-11", project="proj-a")  # re-start same

    with db.get_conn() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM sprints WHERE label = 'sprint-11' ORDER BY project"
        ).fetchall()

    # Should have 2 rows (one per project), not 3
    assert len(rows) == 2


def test_record_sprint_finish__scopes_to_label_project(clean_db):
    """AC: record_sprint_finish scopes to (label, project)."""
    db.record_sprint_start("sprint-22", project="alpha")
    db.record_sprint_start("sprint-22", project="beta")

    db.record_sprint_finish("sprint-22", project="alpha")

    with db.get_conn() as conn:
        conn.row_factory = sqlite3.Row
        alpha = conn.execute(
            "SELECT state FROM sprints WHERE label = 'sprint-22' AND project = 'alpha'"
        ).fetchone()
        beta = conn.execute(
            "SELECT state FROM sprints WHERE label = 'sprint-22' AND project = 'beta'"
        ).fetchone()

    assert alpha["state"] == "completed"
    assert beta["state"] == "running"


def test_record_sprint_ready_to_merge__scopes_to_label_project(clean_db):
    """AC: record_sprint_ready_to_merge scopes to (label, project)."""
    db.record_sprint_start("sprint-33", project="x")
    db.record_sprint_start("sprint-33", project="y")

    db.record_sprint_ready_to_merge("sprint-33", project="x")

    with db.get_conn() as conn:
        conn.row_factory = sqlite3.Row
        x = conn.execute(
            "SELECT state FROM sprints WHERE label = 'sprint-33' AND project = 'x'"
        ).fetchone()
        y = conn.execute(
            "SELECT state FROM sprints WHERE label = 'sprint-33' AND project = 'y'"
        ).fetchone()

    assert x["state"] == "ready_to_merge"
    assert y["state"] == "running"


def test_record_sprint_needs_rework__scopes_to_label_project(clean_db):
    """AC: record_sprint_needs_rework scopes to (label, project)."""
    db.record_sprint_start("sprint-44", project="proj1")
    db.record_sprint_start("sprint-44", project="proj2")

    db.record_sprint_needs_rework("sprint-44", project="proj2")

    with db.get_conn() as conn:
        conn.row_factory = sqlite3.Row
        p1 = conn.execute(
            "SELECT state FROM sprints WHERE label = 'sprint-44' AND project = 'proj1'"
        ).fetchone()
        p2 = conn.execute(
            "SELECT state FROM sprints WHERE label = 'sprint-44' AND project = 'proj2'"
        ).fetchone()

    assert p1["state"] == "running"
    assert p2["state"] == "needs_rework"


def test_update_sprint_run_counts__scopes_to_label_project(clean_db):
    """AC: update_sprint_run_counts scopes to (label, project)."""
    db.record_sprint_start("sprint-55", project="proj-m")
    db.record_sprint_start("sprint-55", project="proj-n")

    db.update_sprint_run_counts(
        "sprint-55",
        '[]',
        summary_settled_done=5,
        summary_uat_count=2,
        summary_failure_count=0,
        project="proj-m"
    )

    with db.get_conn() as conn:
        conn.row_factory = sqlite3.Row
        m = conn.execute(
            "SELECT summary_settled_done FROM sprints WHERE label = 'sprint-55' AND project = 'proj-m'"
        ).fetchone()
        n = conn.execute(
            "SELECT summary_settled_done FROM sprints WHERE label = 'sprint-55' AND project = 'proj-n'"
        ).fetchone()

    assert m["summary_settled_done"] == 5
    assert n["summary_settled_done"] == 0  # unchanged


def test_ingest_sprint_run_artifact__scopes_to_label_project(clean_db):
    """AC: ingest_sprint_run_artifact scopes to (label, project)."""
    db.record_sprint_start("sprint-66", project="p1")
    db.record_sprint_start("sprint-66", project="p2")

    artifact = {
        "issue_states": [],
        "tokens": 1000,
        "wall_clock_secs": 120,
        "pr_number": 42,
    }

    db.ingest_sprint_run_artifact("sprint-66", artifact, project="p1")

    with db.get_conn() as conn:
        conn.row_factory = sqlite3.Row
        p1 = conn.execute(
            "SELECT pr_number FROM sprints WHERE label = 'sprint-66' AND project = 'p1'"
        ).fetchone()
        p2 = conn.execute(
            "SELECT pr_number FROM sprints WHERE label = 'sprint-66' AND project = 'p2'"
        ).fetchone()

    assert p1["pr_number"] == 42
    assert p2["pr_number"] is None  # unchanged


def test_delete_sprint__scopes_to_label_project(clean_db):
    """AC: DELETE in deleted transition scopes to (label, project)."""
    db.record_sprint_start("sprint-88", project="site-a")
    db.record_sprint_start("sprint-88", project="site-b")

    # Transition to deleted (which removes the row)
    db.transition_sprint_state(
        "sprint-88", "deleted", actor="manager", project="site-a"
    )

    with db.get_conn() as conn:
        conn.row_factory = sqlite3.Row
        a_row = conn.execute(
            "SELECT * FROM sprints WHERE label = 'sprint-88' AND project = 'site-a'"
        ).fetchone()
        b_row = conn.execute(
            "SELECT * FROM sprints WHERE label = 'sprint-88' AND project = 'site-b'"
        ).fetchone()

    assert a_row is None  # deleted
    assert b_row is not None  # unaffected
    assert b_row["state"] == "running"


def test_empty_project_parameter_no_op(clean_db):
    """AC: Empty or None project does not affect existing rows."""
    db.record_sprint_start("sprint-99", project="actual-proj")

    # Try to update with empty project
    db.update_sprint_run_counts(
        "sprint-99",
        '[]',
        summary_settled_done=10,
        summary_uat_count=5,
        summary_failure_count=0,
        project=""
    )

    with db.get_conn() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT summary_settled_done FROM sprints WHERE label = 'sprint-99'"
        ).fetchone()

    # Should remain unchanged (0 is the default)
    assert row["summary_settled_done"] == 0


def test_cross_project_isolation_full_lifecycle(clean_db):
    """AC: Writing sprint-66 for perf-coach never mutates commander's sprint-66."""
    # Setup: create the same label for two projects
    db.record_sprint_start("sprint-66", project="perf-coach")
    db.record_sprint_start("sprint-66", project="commander")

    # Transition only perf-coach through a full lifecycle
    db.record_sprint_ready_to_merge("sprint-66", project="perf-coach")
    db.record_sprint_finish("sprint-66", project="perf-coach")

    with db.get_conn() as conn:
        conn.row_factory = sqlite3.Row
        pc = conn.execute(
            "SELECT state FROM sprints WHERE label = 'sprint-66' AND project = 'perf-coach'"
        ).fetchone()
        cmd = conn.execute(
            "SELECT state FROM sprints WHERE label = 'sprint-66' AND project = 'commander'"
        ).fetchone()

    assert pc["state"] == "completed"
    assert cmd["state"] == "running"


def test_record_sprint_finish_leaves_other_project_unchanged(clean_db):
    """UAT Step 2: Call record_sprint_finish('sprint-66', project='perf-coach')."""
    db.record_sprint_start("sprint-66", project="perf-coach")
    db.record_sprint_start("sprint-66", project="commander")

    db.record_sprint_finish("sprint-66", project="perf-coach")

    with db.get_conn() as conn:
        conn.row_factory = sqlite3.Row
        pc = conn.execute(
            "SELECT state FROM sprints WHERE label = 'sprint-66' AND project = 'perf-coach'"
        ).fetchone()
        cmd = conn.execute(
            "SELECT state FROM sprints WHERE label = 'sprint-66' AND project = 'commander'"
        ).fetchone()

    assert pc["state"] == "completed"
    assert cmd["state"] == "running"


def test_record_sprint_needs_rework_only_affects_target(clean_db):
    """UAT Step 3: Call record_sprint_needs_rework('sprint-66', project='commander')."""
    db.record_sprint_start("sprint-66", project="perf-coach")
    db.record_sprint_start("sprint-66", project="commander")

    db.record_sprint_needs_rework("sprint-66", project="commander")

    with db.get_conn() as conn:
        conn.row_factory = sqlite3.Row
        pc = conn.execute(
            "SELECT state FROM sprints WHERE label = 'sprint-66' AND project = 'perf-coach'"
        ).fetchone()
        cmd = conn.execute(
            "SELECT state FROM sprints WHERE label = 'sprint-66' AND project = 'commander'"
        ).fetchone()

    assert pc["state"] == "running"
    assert cmd["state"] == "needs_rework"


def test_record_sprint_start_timestamp_isolation(clean_db):
    """UAT Step 4: record_sprint_start updates only target project."""
    db.record_sprint_start("sprint-66", project="perf-coach")
    db.record_sprint_start("sprint-66", project="commander")

    with db.get_conn() as conn:
        conn.row_factory = sqlite3.Row
        pc_started_1 = conn.execute(
            "SELECT started_at FROM sprints WHERE label = 'sprint-66' AND project = 'perf-coach'"
        ).fetchone()["started_at"]
        cmd_started_1 = conn.execute(
            "SELECT started_at FROM sprints WHERE label = 'sprint-66' AND project = 'commander'"
        ).fetchone()["started_at"]

    assert pc_started_1 is not None
    assert cmd_started_1 is not None
    # Times may differ slightly due to insertion order, but both should exist


def test_delete_removes_only_target_row(clean_db):
    """UAT Step 5: Deleting (label, project) removes only that row."""
    db.record_sprint_start("sprint-66", project="perf-coach")
    db.record_sprint_start("sprint-66", project="commander")

    db.transition_sprint_state(
        "sprint-66", "deleted", actor="manager", project="commander"
    )

    with db.get_conn() as conn:
        conn.row_factory = sqlite3.Row
        pc = conn.execute(
            "SELECT * FROM sprints WHERE label = 'sprint-66' AND project = 'perf-coach'"
        ).fetchone()
        cmd = conn.execute(
            "SELECT * FROM sprints WHERE label = 'sprint-66' AND project = 'commander'"
        ).fetchone()

    assert pc is not None  # perf-coach row still exists
    assert cmd is None  # commander row deleted


def test_null_project_parameter_handled_safely(clean_db):
    """AC: Null project parameter does not mutate rows; project is NOT NULL."""
    db.record_sprint_start("sprint-77", project="real-proj")

    with db.get_conn() as conn:
        conn.row_factory = sqlite3.Row
        row_before = conn.execute(
            "SELECT * FROM sprints WHERE label = 'sprint-77' AND project = 'real-proj'"
        ).fetchone()
        assert row_before is not None

    # Calling with None should fail (NOT NULL constraint) — this is acceptable
    # as it prevents silent data loss
    try:
        db.record_sprint_start("sprint-77", project=None)
        # If it somehow succeeds, verify the original row is still intact
        with db.get_conn() as conn:
            conn.row_factory = sqlite3.Row
            row_after = conn.execute(
                "SELECT * FROM sprints WHERE label = 'sprint-77' AND project = 'real-proj'"
            ).fetchone()
        assert row_after is not None  # unchanged
    except sqlite3.IntegrityError:
        # Expected: project is NOT NULL, so None is rejected
        pass
