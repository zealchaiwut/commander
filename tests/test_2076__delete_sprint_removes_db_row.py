"""Tests for issue #2076: delete-sprint endpoint must remove the live sprints table row.

AC1 — After DELETE /api/sprints/{label}, the sprints table row for that label
       is removed (db.get_sprint returns None), regardless of whether the sprint
       ever had GitHub-labeled tickets.
AC2 — Behavioral test: creates an empty draft sprint row, calls delete_sprint,
       asserts db.get_sprint(label, project=...) returns None afterward.
AC3 — Existing behavior for populated sprints (unlabeling tickets, deleting the
       GitHub label, removing state/goal files) is unchanged.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))

import db as _db  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path):
    db_file = tmp_path / "test_2076.db"
    original = _db.DB_PATH
    _db.DB_PATH = db_file
    _db.init_db()
    yield _db
    _db.DB_PATH = original


def _insert_draft_sprint(label: str, project: str) -> None:
    with _db.get_conn() as conn:
        _db._create_sprint_lifecycle_tables(conn)
        conn.execute(
            "INSERT INTO sprints (label, project, state) VALUES (?, ?, 'draft')",
            (label, project),
        )
        conn.commit()


def _make_fake_server(tmp_path):
    fake = MagicMock()
    fake._SPRINT_LABEL_RE = re.compile(r"sprint-\d+(?:\.\d+)?")
    fake._is_sprint_running.return_value = False
    fake._project_root_path.return_value = tmp_path
    fake._commander_dir.return_value = tmp_path
    fake._get_sprint_issues.return_value = []
    return fake


# ── AC1 / AC2: empty draft sprint row is removed after delete ────────────────

def test_ac1_delete_removes_empty_draft_row(fresh_db, tmp_path):
    """AC1+AC2: delete_sprint removes the sprints table row for an empty draft sprint.

    Repro from the bug report: sprint created as draft, label never created on
    GitHub, 0 tickets.  After DELETE the row must be gone.
    """
    label = "sprint-1001"
    project = "zealchaiwut/commander"

    _insert_draft_sprint(label, project)
    assert _db.get_sprint(label, project=project) is not None, "Precondition: row must exist before delete"

    fake_server = _make_fake_server(tmp_path)
    mock_gc = MagicMock()
    # Simulate label-not-found 404 on GitHub (the original bug repro)
    mock_gc.delete_label.side_effect = subprocess.CalledProcessError(1, "gh", stderr="")

    from routers import sprint_crud
    with (
        patch.object(sprint_crud, "_server", return_value=fake_server),
        patch.object(sprint_crud, "github_client", mock_gc),
        patch.object(sprint_crud, "invalidate_board", MagicMock()),
    ):
        result = sprint_crud.delete_sprint(sprint_label=label, project=project)

    assert result["deleted_label"] == label
    assert result["unlabelled_count"] == 0

    # Core assertion from AC1/AC2
    assert _db.get_sprint(label, project=project) is None, (
        "Sprint row still present after delete_sprint() — "
        "db.transition_sprint_state('deleted') was not called (issue #2076)"
    )


# ── AC3: populated sprint — existing unlabeling behavior is unchanged ─────────

def test_ac3_populated_sprint_delete_unlabels_and_removes_row(fresh_db, tmp_path):
    """AC3: Populated sprint delete still unlabels tickets and deletes the GitHub label."""
    label = "sprint-9999"
    project = "zealchaiwut/commander"

    _insert_draft_sprint(label, project)

    fake_server = _make_fake_server(tmp_path)
    fake_server._get_sprint_issues.return_value = [
        {"number": 100},
        {"number": 101},
    ]
    mock_gc = MagicMock()
    mock_gc.update_labels.return_value = None
    mock_gc.delete_label.return_value = None

    from routers import sprint_crud
    with (
        patch.object(sprint_crud, "_server", return_value=fake_server),
        patch.object(sprint_crud, "github_client", mock_gc),
        patch.object(sprint_crud, "invalidate_board", MagicMock()),
    ):
        result = sprint_crud.delete_sprint(sprint_label=label, project=project)

    # Existing behavior: tickets unlabeled, label deleted
    assert result["unlabelled_count"] == 2
    assert mock_gc.update_labels.call_count == 2
    assert mock_gc.delete_label.call_count == 1

    # New behavior: row also gone from live sprints table
    assert _db.get_sprint(label, project=project) is None, (
        "Sprint row must be removed for populated sprint too (issue #2076)"
    )
