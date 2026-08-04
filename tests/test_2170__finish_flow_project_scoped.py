"""Tests for issue #2170 — Pass project= to _sprint_merge_parent_label from finish flow.

Cross-project bleed fix: _sprint_merge_parent_label callers in sprint_finish.py and
startup._finish_merge_steps must pass project=repo so db.get_sprint scopes to the
correct repo when sprint labels collide across projects (issue #2064).

AC1: get_sprint_bulk_complete_preview passes project=repo to every
     _sprint_merge_parent_label call in the member-status loop.
AC2: complete_sprint_step passes project=repo to _sprint_merge_parent_label at the
     child-parent lookup (line 789) and grandparent walk (line 810).
AC3: startup._finish_merge_steps passes project=repo to _sprint_merge_parent_label
     at the early-check lookup (line 5159) that was missed in issue #2048.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _finish_client() -> TestClient:
    from routers.sprint_finish import router as finish_router
    app = FastAPI()
    app.include_router(finish_router)
    return TestClient(app, raise_server_exceptions=False)


def _base_srv() -> MagicMock:
    srv = MagicMock()
    srv._SPRINT_LABEL_RE = re.compile(r"^sprint-\d+(\.\d+)?$")
    srv._sprint_label_base.side_effect = lambda lbl: lbl.split(".")[0] if "." in lbl else lbl
    srv._is_child_sprint_label.side_effect = lambda lbl: "." in lbl
    srv._project_root_path.return_value = Path("/fake/project")
    srv._sprint_branch_name.side_effect = lambda lbl: f"sprint/{lbl}"
    srv._sprint_merge_parent_label.return_value = "sprint-5"
    return srv


def _make_bulk_preview_mock() -> MagicMock:
    """Minimal mock for get_sprint_bulk_complete_preview to reach the member-status loop."""
    srv = _base_srv()
    srv._bulk_complete_collect_issues.return_value = (
        ["sprint-5", "sprint-5.1"],
        [{"number": 1, "labels": [{"name": "sprint-5.1"}], "title": "T1"}],
    )
    srv._bulk_complete_unsettled_children.return_value = []
    srv._bulk_complete_merge_steps.return_value = []
    srv._bulk_complete_ticket_rows.return_value = {}
    srv.db.get_sprint.return_value = None
    srv.db.canonical_lifecycle.return_value = "completed"
    srv._gh_branch_exists.return_value = False
    srv._has_merged_pr.return_value = True
    srv.db.agent_runs_for_sprint.return_value = []
    srv._sprint_label_sub_index.side_effect = (
        lambda lbl: int(lbl.rsplit(".", 1)[-1]) if "." in lbl else 0
    )
    return srv


def _make_complete_step_mock() -> MagicMock:
    """Minimal mock for complete_sprint_step to reach the parent-label lookup."""
    srv = _base_srv()
    srv._is_sprint_running.return_value = False
    srv._has_rework_tickets.return_value = False
    srv._gh_branch_exists.return_value = True  # parent branch exists → skip grandparent walk
    srv._branch_has_unmerged_commits.return_value = False  # already merged → skip merge
    srv._open_summary_issues_for_labels.return_value = []
    srv._sprint_db_mark_merged_completed.return_value = True
    srv._plan_json_set_state.return_value = None
    return srv


# ── AC1: bulk-complete-preview member loop ────────────────────────────────────

class TestBulkCompletePreviewProjectScoped:
    """AC1: get_sprint_bulk_complete_preview must pass project=repo to _sprint_merge_parent_label.

    Without project=, db.get_sprint falls back to a global WHERE label=? query which
    returns another project's row when sprint labels collide across repos (issue #2064).
    """

    def test_member_loop_passes_project_kwarg(self):
        """Every non-base member call must include project='owner/myrepo' in kwargs."""
        client = _finish_client()
        mock_srv = _make_bulk_preview_mock()

        with (
            patch("routers.sprint_finish._server", return_value=mock_srv),
            patch("routers.sprint_finish.invalidate_board"),
        ):
            r = client.get(
                "/api/projects/owner/myrepo/sprints/sprint-5/bulk-complete-preview"
            )

        assert r.status_code == 200, f"Unexpected {r.status_code}: {r.text}"
        assert mock_srv._sprint_merge_parent_label.called, (
            "_sprint_merge_parent_label was never called — test setup error or regression"
        )
        for i, c in enumerate(mock_srv._sprint_merge_parent_label.call_args_list):
            assert c.kwargs.get("project") == "owner/myrepo", (
                f"Call #{i} to _sprint_merge_parent_label missing project='owner/myrepo': {c}. "
                "Without project=, the DB lookup is unscoped and can return another "
                "project's parent when sprint labels collide."
            )

    def test_cross_project_collision_isolated_in_member_loop(self):
        """Bulk-complete member loop resolves parent for the given repo, not a colliding one."""
        client = _finish_client()
        mock_srv = _make_bulk_preview_mock()

        # Simulate cross-project collision: the unscoped call would return "sprint-99"
        # (another project's immediate_parent), but the scoped call returns "sprint-5".
        def _scoped_smp(proj_root, lbl, *, project=None):
            if project == "owner/myrepo":
                return "sprint-5"
            return "sprint-99"  # wrong project's row

        mock_srv._sprint_merge_parent_label.side_effect = _scoped_smp

        with (
            patch("routers.sprint_finish._server", return_value=mock_srv),
            patch("routers.sprint_finish.invalidate_board"),
        ):
            r = client.get(
                "/api/projects/owner/myrepo/sprints/sprint-5/bulk-complete-preview"
            )

        assert r.status_code == 200, f"Unexpected {r.status_code}: {r.text}"
        # All calls must have scoped project= so the correct parent is returned
        for i, c in enumerate(mock_srv._sprint_merge_parent_label.call_args_list):
            assert c.kwargs.get("project") == "owner/myrepo", (
                f"Call #{i} missing project='owner/myrepo': {c}"
            )


# ── AC2: complete-step parent lookups ────────────────────────────────────────

class TestCompleteStepProjectScoped:
    """AC2: complete_sprint_step must pass project=repo to _sprint_merge_parent_label.

    Covers line 789 (child-parent lookup) and line 810 (grandparent walk).
    Without project=, the wrong branch can be chosen as merge target.
    """

    def test_child_parent_lookup_passes_project(self):
        """Child-parent lookup at line 789 must include project=repo."""
        client = _finish_client()
        mock_srv = _make_complete_step_mock()

        with (
            patch("routers.sprint_finish._server", return_value=mock_srv),
            patch("routers.sprint_finish.invalidate_board"),
        ):
            r = client.post(
                "/api/projects/owner/myrepo/sprints/sprint-5.1/complete-step",
                json={"confirmed": True},
            )

        assert r.status_code == 200, f"Unexpected {r.status_code}: {r.text}"
        assert mock_srv._sprint_merge_parent_label.called, (
            "_sprint_merge_parent_label not called — test setup error or regression"
        )
        for i, c in enumerate(mock_srv._sprint_merge_parent_label.call_args_list):
            assert c.kwargs.get("project") == "owner/myrepo", (
                f"Call #{i} to _sprint_merge_parent_label missing project='owner/myrepo': {c}. "
                "Without project=, the wrong branch can be chosen as merge target."
            )

    def test_grandparent_walk_passes_project(self):
        """Grandparent walk at line 810 must also pass project=repo.

        When the immediate parent branch has been deleted, the endpoint walks up
        the lineage to find the next surviving ancestor. Each call in that walk
        must be scoped to the correct project.
        """
        client = _finish_client()
        mock_srv = _make_complete_step_mock()

        # Arrange: head exists, but immediate parent branch does not → trigger walk
        def _branch_exists(repo, branch):
            if "sprint-5.1" in branch:
                return True   # head exists
            if branch == "sprint/sprint-5.0":
                return False  # initial parent absent → walk starts
            return True       # grandparent (sprint-5) exists

        mock_srv._gh_branch_exists.side_effect = _branch_exists

        # sprint-5.1's parent is sprint-5.0 (nested child), so the walk must resolve
        # sprint-5.0's parent too.
        def _smp(proj_root, lbl, *, project=None):
            if lbl == "sprint-5.1":
                return "sprint-5.0"
            return "sprint-5"

        mock_srv._sprint_merge_parent_label.side_effect = _smp

        with (
            patch("routers.sprint_finish._server", return_value=mock_srv),
            patch("routers.sprint_finish.invalidate_board"),
        ):
            r = client.post(
                "/api/projects/owner/myrepo/sprints/sprint-5.1/complete-step",
                json={"confirmed": True},
            )

        # The grandparent walk itself may or may not raise depending on branch state;
        # what matters is that every _sprint_merge_parent_label call had project= set.
        for i, c in enumerate(mock_srv._sprint_merge_parent_label.call_args_list):
            assert c.kwargs.get("project") == "owner/myrepo", (
                f"Call #{i} (grandparent walk) to _sprint_merge_parent_label "
                f"missing project='owner/myrepo': {c}"
            )

    def test_child_guard_passes_project(self):
        """Child-guard loop at line 833 must pass project=repo to _sprint_merge_parent_label.

        When completing a base sprint, the endpoint checks each child for unmerged
        commits. The parent lookup inside that guard must be scoped to the correct project.
        """
        client = _finish_client()
        mock_srv = _make_complete_step_mock()

        # Arrange: base sprint with one live child that has no unmerged commits
        mock_srv._is_child_sprint_label.side_effect = lambda lbl: "." in lbl
        mock_srv.children_of.return_value = ["sprint-5.1"]
        mock_srv._gh_branch_exists.side_effect = lambda repo, branch: True
        mock_srv._is_sprint_running.return_value = False
        mock_srv._branch_has_unmerged_commits.return_value = False

        with (
            patch("routers.sprint_finish._server", return_value=mock_srv),
            patch("routers.sprint_finish.invalidate_board"),
        ):
            r = client.post(
                "/api/projects/owner/myrepo/sprints/sprint-5/complete-step",
                json={"confirmed": True},
            )

        assert r.status_code == 200, f"Unexpected {r.status_code}: {r.text}"
        assert mock_srv._sprint_merge_parent_label.called, (
            "_sprint_merge_parent_label not called for base sprint child guard"
        )
        for i, c in enumerate(mock_srv._sprint_merge_parent_label.call_args_list):
            assert c.kwargs.get("project") == "owner/myrepo", (
                f"Call #{i} in child guard missing project='owner/myrepo': {c}"
            )


# ── AC3: startup._finish_merge_steps early-check ─────────────────────────────

class TestFinishMergeStepsProjectScoped:
    """AC3: startup._finish_merge_steps must pass project=repo at line 5159.

    The early-check call at line 5159 (before the fallthrough at line 5169) was
    not patched in issue #2048. Without project=, a cross-project collision in
    db.get_sprint can return the wrong immediate_parent, causing _finish_merge_steps
    to build an incorrect merge-step chain.
    """

    def test_early_check_call_passes_project(self, tmp_path):
        """Line 5159 call must pass project=repo so db.get_sprint scopes correctly."""
        import startup

        project_root = tmp_path / "proj"
        (project_root / ".commander" / "sprints").mkdir(parents=True)
        repo = "owner/myrepo"
        label = "sprint-5.1"

        with (
            patch.object(startup, "_sprint_merge_parent_label") as mock_smp,
            patch.object(startup, "_bulk_complete_unsettled_children", return_value=[]),
            patch.object(startup, "_is_child_sprint_label", side_effect=lambda lbl: "." in lbl),
            patch.object(startup, "_sprint_label_base",
                         side_effect=lambda lbl: lbl.split(".")[0] if "." in lbl else lbl),
            patch.object(startup, "_sprint_branch_name",
                         side_effect=lambda lbl: f"sprint/{lbl}"),
            patch.object(startup, "_branch_has_unmerged_commits", return_value=False),
        ):
            # Return a nested-child parent so condition at line 5164 is False,
            # exercising both the early-check call (5159) and the later call (5169).
            mock_smp.return_value = "sprint-5.0"
            startup._finish_merge_steps(project_root, repo, label)

        assert mock_smp.called, (
            "_sprint_merge_parent_label not called in _finish_merge_steps — "
            "test setup error or function was refactored"
        )
        for i, c in enumerate(mock_smp.call_args_list):
            assert c.kwargs.get("project") == repo, (
                f"Call #{i} in _finish_merge_steps missing project={repo!r}: {c}. "
                "Without project=, db.get_sprint falls back to a global lookup and "
                "can return another project's immediate_parent when labels collide."
            )
