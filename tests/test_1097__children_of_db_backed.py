"""Tests for issue #1097: Consolidate child-sprint resolvers into children_of().

AC coverage:
  AC1 — children_of(parent_label) exists and queries sprints table via parent linkage
  AC2 — falls back to disk glob for rows predating parent tracking
  AC3 — _child_sprint_labels_from_plans removed
  AC4 — _child_sprint_labels_for_parent removed
  AC5 — lineage display is separate (children_of not used for lineage rendering)
  AC7 — given one parent + two children, children_of returns same set at every former call site
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))

import db as _db_module  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path):
    """Isolated SQLite DB for sprint tests."""
    db_file = tmp_path / "test_1097.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


def _import_server():
    import server as srv
    return srv


# ── AC1: children_of queries sprints table via parent linkage ─────────────────

class TestChildrenOfDbBacked:
    """children_of(parent_label) returns child labels stored in the sprints table."""

    def test_children_of_returns_db_children(self, fresh_db):
        """Given one parent + two children in DB, children_of returns both children."""
        fresh_db.record_sprint_start("sprint-10", project="owner/repo", parent_label=None)
        fresh_db.record_sprint_start("sprint-10.1", project="owner/repo", parent_label="sprint-10")
        fresh_db.record_sprint_start("sprint-10.2", project="owner/repo", parent_label="sprint-10")

        srv = _import_server()
        result = srv.children_of("sprint-10")

        assert set(result) == {"sprint-10.1", "sprint-10.2"}, (
            f"children_of must return both children; got {result!r}"
        )

    def test_children_of_returns_sorted_by_sub_index(self, fresh_db):
        """children_of returns children sorted by sub-index (sprint-N.1 before sprint-N.2)."""
        fresh_db.record_sprint_start("sprint-20", project="owner/repo")
        fresh_db.record_sprint_start("sprint-20.2", project="owner/repo", parent_label="sprint-20")
        fresh_db.record_sprint_start("sprint-20.1", project="owner/repo", parent_label="sprint-20")

        srv = _import_server()
        result = srv.children_of("sprint-20")

        assert result == ["sprint-20.1", "sprint-20.2"], (
            f"children_of must sort by sub-index; got {result!r}"
        )

    def test_children_of_returns_empty_for_unknown_parent(self, fresh_db):
        """children_of returns [] when no DB rows reference the given parent."""
        srv = _import_server()
        result = srv.children_of("sprint-99")
        assert result == [], f"expected [], got {result!r}"


# ── AC2: fallback to disk glob for pre-tracking rows ─────────────────────────

class TestChildrenOfDiskFallback:
    """children_of falls back to plan.json disk glob when DB has no matching rows."""

    def test_fallback_to_disk_glob_when_db_empty(self, tmp_path, fresh_db):
        """No DB rows for parent → disk glob returns children from plan.json files."""
        project_root = tmp_path / "proj"
        sprints_dir = project_root / ".commander" / "sprints"
        sprints_dir.mkdir(parents=True)
        (sprints_dir / "sprint-30.1-plan.json").write_text(
            json.dumps({"label": "sprint-30.1", "parent": "sprint-30"}),
            encoding="utf-8",
        )
        (sprints_dir / "sprint-30.2-plan.json").write_text(
            json.dumps({"label": "sprint-30.2", "parent": "sprint-30"}),
            encoding="utf-8",
        )

        srv = _import_server()
        result = srv.children_of("sprint-30", project_root=project_root)

        assert set(result) == {"sprint-30.1", "sprint-30.2"}, (
            f"fallback must find children via disk glob; got {result!r}"
        )

    def test_fallback_returns_empty_when_no_plan_files(self, tmp_path, fresh_db):
        """Disk fallback returns [] when no matching plan.json files exist."""
        project_root = tmp_path / "proj"
        sprints_dir = project_root / ".commander" / "sprints"
        sprints_dir.mkdir(parents=True)

        srv = _import_server()
        result = srv.children_of("sprint-40", project_root=project_root)

        assert result == [], f"expected [], got {result!r}"

    def test_db_takes_priority_over_disk(self, tmp_path, fresh_db):
        """When DB has children, disk glob is NOT consulted (DB is primary)."""
        # DB has sprint-50.1
        fresh_db.record_sprint_start("sprint-50.1", project="owner/repo", parent_label="sprint-50")

        # Disk has sprint-50.2 only
        project_root = tmp_path / "proj"
        sprints_dir = project_root / ".commander" / "sprints"
        sprints_dir.mkdir(parents=True)
        (sprints_dir / "sprint-50.2-plan.json").write_text(
            json.dumps({"label": "sprint-50.2", "parent": "sprint-50"}),
            encoding="utf-8",
        )

        srv = _import_server()
        result = srv.children_of("sprint-50", project_root=project_root)

        # DB result only (sprint-50.1); disk result (sprint-50.2) ignored
        assert result == ["sprint-50.1"], (
            f"DB must take priority over disk; got {result!r}"
        )


# ── AC3 + AC4: old functions removed ─────────────────────────────────────────

class TestOldFunctionsRemoved:
    """Neither _child_sprint_labels_from_plans nor _child_sprint_labels_for_parent exist."""

    def test_child_sprint_labels_from_plans_not_in_server(self):
        """`_child_sprint_labels_from_plans` must be removed from server.py."""
        src = (DASHBOARD_DIR / "server.py").read_text(encoding="utf-8")
        assert "def _child_sprint_labels_from_plans" not in src, (
            "_child_sprint_labels_from_plans still defined in server.py"
        )

    def test_child_sprint_labels_for_parent_not_in_server(self):
        """`_child_sprint_labels_for_parent` must be removed from server.py."""
        src = (DASHBOARD_DIR / "server.py").read_text(encoding="utf-8")
        assert "def _child_sprint_labels_for_parent" not in src, (
            "_child_sprint_labels_for_parent still defined in server.py"
        )

    def test_old_names_not_called_anywhere(self):
        """No call site (other than this test file) references the old function names."""
        import subprocess
        result = subprocess.run(
            ["grep", "-rn", "--include=*.py",
             "_child_sprint_labels_from_plans\\|_child_sprint_labels_for_parent",
             str(REPO_ROOT / "apps"),
             str(REPO_ROOT / "scripts"),
             str(REPO_ROOT / "services"),
             ],
            capture_output=True,
            text=True,
        )
        hits = [line for line in result.stdout.splitlines()
                if "def _child_sprint_labels_from_plans" not in line
                and "def _child_sprint_labels_for_parent" not in line]
        assert not hits, (
            "Old resolver names still referenced in non-test code:\n" + "\n".join(hits)
        )


# ── AC7: same child set at every former call site ─────────────────────────────

class TestChildrenOfConsistency:
    """children_of returns the same child set at both outcome-derive and bulk-complete."""

    def test_derive_outcome_lifecycle_uses_children_of(self, fresh_db, tmp_path):
        """_derive_outcome_lifecycle resolves children via children_of, not old functions."""
        fresh_db.record_sprint_start("sprint-60", project="owner/repo")
        fresh_db.record_sprint_start("sprint-60.1", project="owner/repo", parent_label="sprint-60")
        fresh_db.record_sprint_start("sprint-60.2", project="owner/repo", parent_label="sprint-60")

        project_root = tmp_path / "proj"
        project_root.mkdir()

        srv = _import_server()

        captured = []

        real_children_of = srv.children_of

        def _spy(parent_label, project_root=None):
            result = real_children_of(parent_label, project_root=project_root)
            captured.append((parent_label, result))
            return result

        with patch("server.children_of", side_effect=_spy), \
             patch("server._child_sprint_settled", return_value=True), \
             patch("server._sprint_work_tickets_all_uat", return_value=True):
            srv._derive_outcome_lifecycle(
                sprint_label="sprint-60",
                project_root=project_root,
                project="owner/repo",
                plan_state="completed",
                pane_state="completed",
                failed_count=0,
            )

        assert any(parent == "sprint-60" for parent, _ in captured), (
            "_derive_outcome_lifecycle must call children_of('sprint-60'); "
            f"captured calls: {captured!r}"
        )
        # Verify the child set at the call site matches DB
        child_sets = [set(children) for parent, children in captured if parent == "sprint-60"]
        assert any(s == {"sprint-60.1", "sprint-60.2"} for s in child_sets), (
            f"children_of must return both children at _derive_outcome_lifecycle; got {child_sets!r}"
        )

    def test_bulk_complete_collect_issues_uses_children_of(self, fresh_db, tmp_path):
        """_bulk_complete_collect_issues resolves children via children_of."""
        fresh_db.record_sprint_start("sprint-70", project="owner/repo")
        fresh_db.record_sprint_start("sprint-70.1", project="owner/repo", parent_label="sprint-70")
        fresh_db.record_sprint_start("sprint-70.2", project="owner/repo", parent_label="sprint-70")

        project_root = tmp_path / "proj"
        project_root.mkdir()

        srv = _import_server()

        captured = []
        real_children_of = srv.children_of

        def _spy(parent_label, project_root=None):
            result = real_children_of(parent_label, project_root=project_root)
            captured.append((parent_label, result))
            return result

        with patch("server.children_of", side_effect=_spy), \
             patch("server._get_sprint_issues", return_value=[]), \
             patch("server._open_summary_issues_for_labels", return_value=[]):
            srv._bulk_complete_collect_issues("owner/repo", project_root, "sprint-70")

        assert any(parent == "sprint-70" for parent, _ in captured), (
            "_bulk_complete_collect_issues must call children_of('sprint-70'); "
            f"captured calls: {captured!r}"
        )
        child_sets = [set(children) for parent, children in captured if parent == "sprint-70"]
        assert any(s == {"sprint-70.1", "sprint-70.2"} for s in child_sets), (
            f"children_of must return both children at bulk-complete; got {child_sets!r}"
        )
