"""Tests for issue #1494: Narrow bare except in audit_sprint_collisions._collect_label_projects

This tests the fix that replaced bare `except Exception: pass` with narrower
`except sqlite3.OperationalError: pass` to allow genuine DB errors to surface
while still gracefully handling missing tables.
"""
import sqlite3
import tempfile
from pathlib import Path

import pytest

# Import the function under test
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.audit_sprint_collisions import _collect_label_projects


@pytest.fixture
def temp_db():
    """Create a temporary SQLite database for testing."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name

    yield db_path

    # Cleanup
    if Path(db_path).exists():
        Path(db_path).unlink()


@pytest.fixture
def conn_with_sprints(temp_db):
    """Create a database with only the sprints table."""
    conn = sqlite3.connect(temp_db)
    conn.execute(
        "CREATE TABLE sprints (label TEXT, project TEXT)"
    )
    conn.execute("INSERT INTO sprints VALUES ('sprint-1', 'project-a')")
    conn.execute("INSERT INTO sprints VALUES ('sprint-2', 'project-b')")
    conn.commit()

    yield conn

    conn.close()


@pytest.fixture
def conn_with_all_tables(temp_db):
    """Create a database with all three tables (sprints, sprint_history, agent_runs)."""
    conn = sqlite3.connect(temp_db)

    # Create all three tables
    conn.execute(
        "CREATE TABLE sprints (label TEXT, project TEXT)"
    )
    conn.execute(
        "CREATE TABLE sprint_history (label TEXT, project TEXT)"
    )
    conn.execute(
        "CREATE TABLE agent_runs (sprint_label TEXT, project TEXT)"
    )

    # Insert test data
    conn.execute("INSERT INTO sprints VALUES ('sprint-1', 'project-a')")
    conn.execute("INSERT INTO sprint_history VALUES ('sprint-1', 'project-b')")
    conn.execute("INSERT INTO agent_runs VALUES ('sprint-1', 'project-c')")

    conn.commit()
    yield conn

    conn.close()


def test_audit_collisions__missing_tables(conn_with_sprints):
    """Script handles missing sprint_history and agent_runs tables gracefully."""
    # When sprint_history and agent_runs don't exist, the function should still
    # process the sprints table and return its data without raising an exception
    label_projects, survivors = _collect_label_projects(conn_with_sprints)

    assert 'sprint-1' in label_projects
    assert 'sprint-2' in label_projects
    assert label_projects['sprint-1'] == {'project-a'}
    assert label_projects['sprint-2'] == {'project-b'}
    assert survivors['sprint-1'] == 'project-a'
    assert survivors['sprint-2'] == 'project-b'


def test_audit_collisions__all_tables_present(conn_with_all_tables):
    """Script collects labels from all three tables when they exist."""
    label_projects, survivors = _collect_label_projects(conn_with_all_tables)

    # sprint-1 should have collected projects from all three tables
    assert 'sprint-1' in label_projects
    assert label_projects['sprint-1'] == {'project-a', 'project-b', 'project-c'}

    # survivor should be the one from the sprints table
    assert survivors['sprint-1'] == 'project-a'


def test_audit_collisions__empty_tables(temp_db):
    """Script handles empty tables correctly (no data, but tables exist)."""
    conn = sqlite3.connect(temp_db)

    # Create tables but don't insert data
    conn.execute("CREATE TABLE sprints (label TEXT, project TEXT)")
    conn.execute("CREATE TABLE sprint_history (label TEXT, project TEXT)")
    conn.execute("CREATE TABLE agent_runs (sprint_label TEXT, project TEXT)")
    conn.commit()

    label_projects, survivors = _collect_label_projects(conn)

    # Should return empty dictionaries, not crash
    assert label_projects == {}
    assert survivors == {}

    conn.close()


def test_audit_collisions__null_values(temp_db):
    """Script correctly filters out NULL and empty string values."""
    conn = sqlite3.connect(temp_db)

    conn.execute("CREATE TABLE sprints (label TEXT, project TEXT)")
    conn.execute("INSERT INTO sprints VALUES ('sprint-1', 'project-a')")
    conn.execute("INSERT INTO sprints VALUES ('sprint-2', NULL)")  # NULL project
    conn.execute("INSERT INTO sprints VALUES (NULL, 'project-b')")  # NULL label
    conn.execute("INSERT INTO sprints VALUES ('', 'project-c')")    # empty label
    conn.commit()

    label_projects, survivors = _collect_label_projects(conn)

    # Only the first row should be included
    assert label_projects == {'sprint-1': {'project-a'}}
    assert survivors == {'sprint-1': 'project-a'}

    conn.close()
