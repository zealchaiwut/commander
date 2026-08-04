"""Regression test for issue #2195: the bulk-complete-preview ROUTER re-added
the exact 400 that issue #1758 removed.

#1758 fixed `_bulk_complete_collect_issues` to tolerate a base sprint with
zero DB children (a clean, single-attempt sprint — not an error state), but
its own test (test_1758__bulk_complete_zero_children.py) only calls that
helper directly. A later change added a second, redundant `len(all_labels)
<= 1` guard one call site up, in the router function itself
(`get_sprint_bulk_complete_preview`), silently reintroducing the 400 for any
caller that goes through the actual HTTP endpoint. This test exercises the
router function itself so a regression there is caught.
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

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest-2195.db")
os.environ.setdefault("COMMANDER_DISABLE_NEON", "1")

import db as _db_module  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path):
    db_file = tmp_path / "test_2195.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


def _insert_sprint(db_module, label, project="owner/repo", state="ready_to_merge"):
    with db_module.get_conn() as conn:
        db_module._create_sprint_lifecycle_tables(conn)
        conn.execute(
            "INSERT OR REPLACE INTO sprints (label, project, state) VALUES (?, ?, ?)",
            (label, project, state),
        )
        conn.commit()


class TestBulkCompletePreviewRouterZeroChildren:
    """A clean, never-reworked base sprint must reach preview via the router,
    not just via the underlying helper (issue #1758's original bug, reintroduced
    by issue #2195).
    """

    def test_zero_children_does_not_400_through_router(self, fresh_db, tmp_path):
        _insert_sprint(fresh_db, "sprint-58", project="owner/repo")
        project_root = tmp_path / "proj"
        project_root.mkdir()

        import server as srv
        from routers import sprint_finish

        with patch.object(srv, "children_of", return_value=[]), \
             patch.object(srv, "_get_sprint_issues", return_value=[]), \
             patch.object(srv, "_open_summary_issues_for_labels", return_value=[]), \
             patch.object(srv, "_project_root_path", return_value=project_root), \
             patch.object(srv, "_bulk_complete_unsettled_children", return_value=[]), \
             patch.object(srv, "_bulk_complete_merge_steps", return_value=[]), \
             patch.object(srv, "_gh_branch_exists", return_value=False), \
             patch.object(srv, "_has_merged_pr", return_value=False):
            # Must not raise — this is exactly the #1758 regression scenario.
            result = sprint_finish.get_sprint_bulk_complete_preview(
                "owner", "repo", "sprint-58"
            )

        assert result["base_label"] == "sprint-58"
        member_labels = [m["label"] for m in result["members"]]
        assert member_labels == ["sprint-58"], (
            f"Expected the zero-child base sprint to preview as a single "
            f"member, got {member_labels!r}"
        )

    def test_children_present_still_works(self, fresh_db, tmp_path):
        """Sanity check: the router still previews correctly when children exist."""
        _insert_sprint(fresh_db, "sprint-70", project="owner/repo")
        _insert_sprint(fresh_db, "sprint-70.1", project="owner/repo")
        project_root = tmp_path / "proj"
        project_root.mkdir()

        import server as srv
        from routers import sprint_finish

        with patch.object(srv, "children_of", return_value=["sprint-70.1"]), \
             patch.object(srv, "_get_sprint_issues", return_value=[]), \
             patch.object(srv, "_open_summary_issues_for_labels", return_value=[]), \
             patch.object(srv, "_project_root_path", return_value=project_root), \
             patch.object(srv, "_bulk_complete_unsettled_children", return_value=[]), \
             patch.object(srv, "_bulk_complete_merge_steps", return_value=[]), \
             patch.object(srv, "_gh_branch_exists", return_value=False), \
             patch.object(srv, "_has_merged_pr", return_value=False), \
             patch.object(srv, "_sprint_merge_parent_label", return_value="sprint-70"):
            result = sprint_finish.get_sprint_bulk_complete_preview(
                "owner", "repo", "sprint-70"
            )

        member_labels = sorted(m["label"] for m in result["members"])
        assert member_labels == ["sprint-70", "sprint-70.1"]
