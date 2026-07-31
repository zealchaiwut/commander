"""Bulk complete: allow zero-rework sprints (issue #1758).

Problem: _bulk_complete_collect_issues hard-raised a 400 "No child sprints
found under {base_label}" whenever children_of(base_label) returned an empty
list. A sprint that passed all tickets on the first attempt (no rerun/rework
ever needed) legitimately has zero children — that's the normal clean
outcome, not an error. Observed on crux sprint-58: single DB row,
state=ready_to_merge, parent_label=None, zero children — bulk-complete-preview
refused to even preview it.

AC1: _bulk_complete_collect_issues no longer raises when children_of returns
     [] — it proceeds with all_labels == [base_label].
AC2: Existing behavior for a base label with real children is unchanged.
AC3: The base-label-shape guard (must match sprint-\\d+) is preserved.
AC4: Regression test — a base sprint with zero DB children reaches
     bulk-complete-preview successfully (no 400), computed from [base_label].
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
for _p in (str(DASHBOARD_DIR), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")
os.environ.setdefault("COMMANDER_DISABLE_NEON", "1")

import db as _db_module  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path):
    db_file = tmp_path / "test_1758.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


def _insert_sprint(db_module, label, project="owner/repo", parent_label=None,
                    state="ready_to_merge"):
    with db_module.get_conn() as conn:
        db_module._create_sprint_lifecycle_tables(conn)
        conn.execute(
            "INSERT OR REPLACE INTO sprints (label, project, state, parent_label)"
            " VALUES (?, ?, ?, ?)",
            (label, project, state, parent_label),
        )
        conn.commit()


def _import_server():
    import server as srv
    return srv


class TestZeroChildrenAllowed:
    """AC1 / AC4: a clean, never-reworked base sprint no longer 400s."""

    def test_no_children_does_not_raise(self, fresh_db, tmp_path):
        _insert_sprint(fresh_db, "sprint-58", project="owner/repo")
        project_root = tmp_path / "proj"
        project_root.mkdir()
        srv = _import_server()

        with patch("server.children_of", return_value=[]), \
             patch("server._get_sprint_issues", return_value=[]), \
             patch("server._open_summary_issues_for_labels", return_value=[]):
            all_labels, _ = srv._bulk_complete_collect_issues(
                "owner/repo", project_root, "sprint-58"
            )

        assert all_labels == ["sprint-58"]

    def test_no_children_still_collects_base_label_tickets(self, fresh_db, tmp_path):
        _insert_sprint(fresh_db, "sprint-58", project="owner/repo")
        project_root = tmp_path / "proj"
        project_root.mkdir()
        srv = _import_server()

        with patch("startup.children_of", return_value=[]), \
             patch("startup._get_sprint_issues", return_value=[{"number": 42}]), \
             patch("startup._open_summary_issues_for_labels", return_value=[]):
            all_labels, issues = srv._bulk_complete_collect_issues(
                "owner/repo", project_root, "sprint-58"
            )

        assert all_labels == ["sprint-58"]
        assert issues == [{"number": 42}]


class TestChildrenStillWork:
    """AC2: existing behavior with real children is unchanged."""

    def test_with_children_all_labels_included(self, fresh_db, tmp_path):
        _insert_sprint(fresh_db, "sprint-70", project="owner/repo")
        _insert_sprint(fresh_db, "sprint-70.1", project="owner/repo", parent_label="sprint-70")
        project_root = tmp_path / "proj"
        project_root.mkdir()
        srv = _import_server()

        with patch("server.children_of", return_value=["sprint-70.1"]), \
             patch("server._get_sprint_issues", return_value=[]), \
             patch("server._open_summary_issues_for_labels", return_value=[]):
            all_labels, _ = srv._bulk_complete_collect_issues(
                "owner/repo", project_root, "sprint-70"
            )

        assert all_labels == ["sprint-70", "sprint-70.1"]


class TestBaseLabelShapeGuardPreserved:
    """AC3: bulk complete still rejects a non-base (sub-numbered) label."""

    def test_sub_numbered_label_still_rejected(self, tmp_path):
        srv = _import_server()
        project_root = tmp_path / "proj"
        project_root.mkdir()
        with pytest.raises(HTTPException) as exc:
            srv._bulk_complete_collect_issues("owner/repo", project_root, "sprint-58.1")
        assert exc.value.status_code == 400
