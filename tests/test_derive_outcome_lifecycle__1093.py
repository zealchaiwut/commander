"""Tests for issue #1093: Migrate _derive_outcome_lifecycle to DB-only state"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))

import db as _db_module  # noqa: E402
from server import _derive_outcome_lifecycle  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path):
    """Isolated test DB; yields patched db module."""
    db_file = tmp_path / "test_derive_outcome_1093.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


@pytest.fixture
def project_root(tmp_path):
    """Return a temp project root."""
    return tmp_path


def test_derive_outcome_lifecycle__parent_terminal_child_unsettled(fresh_db, project_root):
    """AC: Parent terminal + unsettled child → returns partial_finished"""
    with fresh_db.get_conn() as conn:
        conn.execute(
            "INSERT INTO sprints (label, state) VALUES (?, ?)",
            ("parent-sprint", "completed"),
        )
        conn.execute(
            "INSERT INTO sprints (label, state, parent_label) VALUES (?, ?, ?)",
            ("child-sprint-1", "running", "parent-sprint"),
        )
        conn.commit()

    result = _derive_outcome_lifecycle("parent-sprint", project_root, "test-project", "completed", "completed", 0)
    assert result == "partial_finished", "Expected partial_finished when parent is terminal and child is unsettled"


def test_derive_outcome_lifecycle__parent_terminal_all_children_settled(fresh_db, project_root):
    """AC: Parent terminal + all children settled → returns DB state (completed)"""
    with fresh_db.get_conn() as conn:
        conn.execute(
            "INSERT INTO sprints (label, state) VALUES (?, ?)",
            ("parent-sprint", "completed"),
        )
        conn.execute(
            "INSERT INTO sprints (label, state, parent_label) VALUES (?, ?, ?)",
            ("child-sprint-1", "completed", "parent-sprint"),
        )
        conn.execute(
            "INSERT INTO sprints (label, state, parent_label) VALUES (?, ?, ?)",
            ("child-sprint-2", "ready_to_merge", "parent-sprint"),
        )
        conn.commit()

    result = _derive_outcome_lifecycle("parent-sprint", project_root, "test-project", "completed", "completed", 0)
    assert result == "completed", "Expected DB state (completed) when all children are settled"


def test_derive_outcome_lifecycle__parent_nonterminal_child_unsettled(fresh_db, project_root):
    """AC: Parent non-terminal + unsettled child → returns DB state, no partial_finished"""
    with fresh_db.get_conn() as conn:
        conn.execute(
            "INSERT INTO sprints (label, state) VALUES (?, ?)",
            ("parent-sprint", "running"),
        )
        conn.execute(
            "INSERT INTO sprints (label, state, parent_label) VALUES (?, ?, ?)",
            ("child-sprint-1", "running", "parent-sprint"),
        )
        conn.commit()

    result = _derive_outcome_lifecycle("parent-sprint", project_root, "test-project", "running", "running", 0)
    assert result == "running", "Expected running (DB state) when parent is non-terminal, no child check"


def test_derive_outcome_lifecycle__no_children_terminal_parent(fresh_db, project_root):
    """AC: Terminal parent with no children → returns DB state"""
    with fresh_db.get_conn() as conn:
        conn.execute(
            "INSERT INTO sprints (label, state) VALUES (?, ?)",
            ("parent-sprint", "completed"),
        )
        conn.commit()

    result = _derive_outcome_lifecycle("parent-sprint", project_root, "test-project", "completed", "completed", 0)
    assert result == "completed", "Expected DB state when no children exist"


def test_derive_outcome_lifecycle__multiple_unsettled_children(fresh_db, project_root):
    """AC: Terminal parent with multiple unsettled children → returns partial_finished"""
    with fresh_db.get_conn() as conn:
        conn.execute(
            "INSERT INTO sprints (label, state) VALUES (?, ?)",
            ("parent-sprint", "completed"),
        )
        conn.execute(
            "INSERT INTO sprints (label, state, parent_label) VALUES (?, ?, ?)",
            ("child-1", "running", "parent-sprint"),
        )
        conn.execute(
            "INSERT INTO sprints (label, state, parent_label) VALUES (?, ?, ?)",
            ("child-2", "planning", "parent-sprint"),
        )
        conn.execute(
            "INSERT INTO sprints (label, state, parent_label) VALUES (?, ?, ?)",
            ("child-3", "completed", "parent-sprint"),
        )
        conn.commit()

    result = _derive_outcome_lifecycle("parent-sprint", project_root, "test-project", "completed", "completed", 0)
    assert result == "partial_finished", "Expected partial_finished when at least one child is unsettled"
