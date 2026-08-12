"""Tests for issue #1481: scope ASSERT_ABSENT lookup to (label, project) in repair script."""
import pytest
import sqlite3
import tempfile
from pathlib import Path

# The repair script is at scripts/repair_sprint_collisions.py
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.archive import repair_sprint_collisions as rsc


@pytest.fixture
def temp_db():
    """Create a temporary SQLite database with the sprints table (composite key schema)."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Create sprints table with composite key (label, project)
    conn.execute("""
        CREATE TABLE sprints (
            label TEXT NOT NULL,
            project TEXT NOT NULL,
            state TEXT DEFAULT 'planned',
            created_at TEXT,
            parent_label TEXT,
            PRIMARY KEY (label, project)
        )
    """)
    conn.commit()

    yield conn

    conn.close()
    Path(db_path).unlink()


def test_assert_absent_detects_ghost_when_project_row_exists(temp_db):
    """AC: ASSERT_ABSENT lookup uses (label, project) scoping and detects a ghost when the target project's row is running."""
    # Setup: Insert two rows with same label but different projects
    # perf-coach row is running (ghost state - this is what we're detecting)
    temp_db.execute(
        "INSERT INTO sprints (label, project, state, created_at) VALUES (?, ?, ?, ?)",
        ("sprint-66", "zealchaiwut/perf-coach", "running", "2026-01-01T00:00:00"),
    )
    # commander row is valid (different project)
    temp_db.execute(
        "INSERT INTO sprints (label, project, state, created_at) VALUES (?, ?, ?, ?)",
        ("sprint-66", "zealchaiwut/commander", "finished", "2026-01-01T00:00:00"),
    )
    temp_db.commit()

    # Test: the scoped lookup for perf-coach should find the running row
    row = rsc._get_sprint_row_scoped(temp_db, "sprint-66", "zealchaiwut/perf-coach")

    assert row is not None
    assert row["state"] == "running"
    assert row["project"] == "zealchaiwut/perf-coach"


def test_assert_absent_passes_when_only_other_project_row_exists(temp_db):
    """AC: ASSERT_ABSENT lookup correctly reports passing (no ghost) when only a different project's row exists for that label."""
    # Setup: Insert only commander's row for sprint-66 (perf-coach has no row)
    temp_db.execute(
        "INSERT INTO sprints (label, project, state, created_at) VALUES (?, ?, ?, ?)",
        ("sprint-66", "zealchaiwut/commander", "finished", "2026-01-01T00:00:00"),
    )
    temp_db.commit()

    # Test: scoped lookup for perf-coach should find nothing
    row = rsc._get_sprint_row_scoped(temp_db, "sprint-66", "zealchaiwut/perf-coach")

    assert row is None


def test_assert_absent_uses_scoped_query(temp_db):
    """AC: line 288 (line 305 in current code) uses project-scoped query, not unscoped."""
    # Setup: multiple rows with same label
    temp_db.execute(
        "INSERT INTO sprints (label, project, state, created_at) VALUES (?, ?, ?, ?)",
        ("sprint-test", "zealchaiwut/project-a", "running", "2026-01-01T00:00:00"),
    )
    temp_db.execute(
        "INSERT INTO sprints (label, project, state, created_at) VALUES (?, ?, ?, ?)",
        ("sprint-test", "zealchaiwut/project-b", "finished", "2026-01-01T00:00:00"),
    )
    temp_db.commit()

    # With scoped lookup (correct):
    # - should find project-a's running row when scoped to project-a
    row_a = rsc._get_sprint_row_scoped(temp_db, "sprint-test", "zealchaiwut/project-a")
    assert row_a is not None
    assert row_a["project"] == "zealchaiwut/project-a"
    assert row_a["state"] == "running"

    # - should find project-b's finished row when scoped to project-b
    row_b = rsc._get_sprint_row_scoped(temp_db, "sprint-test", "zealchaiwut/project-b")
    assert row_b is not None
    assert row_b["project"] == "zealchaiwut/project-b"
    assert row_b["state"] == "finished"

    # With unscoped lookup (wrong):
    # - returns an arbitrary row (implementation detail — just verify it's not reliable)
    unscoped = rsc._get_sprint_row_unscoped(temp_db, "sprint-test")
    assert unscoped is not None  # Returns *some* row
    # But we can't rely on which one without checking — scoped is better


def test_no_other_assert_absent_uses_unscoped(temp_db):
    """AC: No other ASSERT_ABSENT call sites in repair_sprint_collisions.py use the unscoped lookup."""
    # Verify the fix is isolated to one location
    # The only call to _get_sprint_row_unscoped is:
    #   1. Inside the _commander_row_exists helper (fallback for single-key schema) — INTENTIONAL, not ASSERT_ABSENT
    #   2. Nowhere else in apply() for ASSERT_ABSENT

    # Read the source to verify this
    source_path = Path(__file__).parent.parent / "scripts" / "archive" / "repair_sprint_collisions.py"
    source = source_path.read_text()

    # Count calls to _get_sprint_row_unscoped
    unscoped_count = source.count("_get_sprint_row_unscoped")

    # Should be exactly 2:
    #   1. function definition (line 187)
    #   2. one call inside _commander_row_exists (line 224)
    # NOT called in the ASSERT_ABSENT section (which uses _get_sprint_row_scoped at line 305)
    assert unscoped_count == 2

    # Verify ASSERT_ABSENT section uses _get_sprint_row_scoped
    lines = source.split("\n")
    in_assert_absent = False
    found_scoped_in_assert = False
    for i, line in enumerate(lines, 1):
        if 'if entry["action"] != "ASSERT_ABSENT":' in line:
            in_assert_absent = True
        elif 'if entry["action"] != "RECREATE":' in line:
            in_assert_absent = False

        if in_assert_absent and "_get_sprint_row_scoped" in line:
            found_scoped_in_assert = True
            break

    assert found_scoped_in_assert, "ASSERT_ABSENT section does not use _get_sprint_row_scoped"


def test_single_project_setup_no_regression(temp_db):
    """AC: The fix does not break existing repair script behavior for single-project setups where label uniqueness is already guaranteed."""
    # Setup: single project with sprint-100
    temp_db.execute(
        "INSERT INTO sprints (label, project, state, created_at) VALUES (?, ?, ?, ?)",
        ("sprint-100", "zealchaiwut/commander", "finished", "2026-01-01T00:00:00"),
    )
    temp_db.commit()

    # Test: ASSERT_ABSENT for a different project should still work (find nothing)
    row = rsc._get_sprint_row_scoped(temp_db, "sprint-100", "zealchaiwut/perf-coach")
    assert row is None

    # Test: ASSERT_ABSENT for the actual project should find it
    row = rsc._get_sprint_row_scoped(temp_db, "sprint-100", "zealchaiwut/commander")
    assert row is not None
    assert row["state"] == "finished"
