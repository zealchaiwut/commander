"""Behavioral tests for issue #1934 — complete-step bugs in lineage edge cases.

AC1: complete-step on a child whose parent branch no longer exists must retarget
     the merge to the next surviving ancestor (or develop), never ok/no-op while
     unmerged commits exist.

AC2: Base complete-step must refuse (409) while a sibling/child sprint in the
     lineage is still running or has an unmerged branch with commits.

AC3: The base close sweep must not close tickets that are open on a not-yet-merged
     child label.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

_PROJ = "owner/commander"


def _dual(name, **kw):
    out = []
    for mod_name in ("server", "startup"):
        try:
            mod = importlib.import_module(mod_name)
        except Exception:
            continue
        if hasattr(mod, name):
            out.append(patch(f"{mod_name}.{name}", **kw))
    return out


def _complete_step(
    label: str,
    branch_exists_fn,
    parent_fn,
    merge_calls: list | None = None,
    unmerged_fn=None,
    children_fn=None,
    close_calls: list | None = None,
    sprint_issues: list | None = None,
    running_fn=None,
):
    """POST complete-step with GitHub/disk boundaries mocked.

    branch_exists_fn(repo, branch) → bool
    parent_fn(project_root, label) → str  (immediate parent label)
    unmerged_fn(repo, head, base) → bool  (default: True always)
    children_fn(parent_label, project_root, project) → list[str]  (default: [])
    running_fn(project_root, label) → bool  (default: False)
    """
    import server as srv
    from fastapi.testclient import TestClient

    if merge_calls is None:
        merge_calls = []
    if close_calls is None:
        close_calls = []

    def fake_merge(repo, head, base, title, delete_branch=True, **kw):
        merge_calls.append((head, base))
        return (True, "merged", 42)

    def default_unmerged(repo, head, base):
        return True

    def default_children(*a, **kw):
        return []

    def default_running(root, lbl):
        return False

    _unmerged = unmerged_fn if unmerged_fn is not None else default_unmerged
    _children = children_fn if children_fn is not None else default_children
    _running = running_fn if running_fn is not None else default_running

    _sprint_issues = sprint_issues if sprint_issues is not None else []

    patches = [
        *_dual("_project_root_path", return_value=REPO_ROOT),
        *_dual("_is_sprint_running", side_effect=_running),
        *_dual("_gh_branch_exists", side_effect=branch_exists_fn),
        *_dual("_branch_has_unmerged_commits", side_effect=_unmerged),
        *_dual("_gh_merge_branch_via_pr", side_effect=fake_merge),
        *_dual("_sprint_merge_parent_label", side_effect=parent_fn),
        *_dual("_open_summary_issues_for_labels", return_value=[]),
        *_dual("_bulk_complete_collect_issues", return_value=(["sprint-119"], _sprint_issues)),
        *_dual("children_of", side_effect=_children),
        *_dual("_plan_json_set_state", return_value=None),
        *_dual("_sprint_db_mark_merged_completed", return_value=True),
        *_dual("_has_rework_tickets", return_value=False),
        patch.object(srv.github_client, "close_issue",
                     side_effect=lambda n, repo_name, reason: close_calls.append(n)),
        patch.object(srv.github_client, "invalidate", return_value=None),
    ]
    for p in patches:
        p.start()
    try:
        client = TestClient(srv.app, raise_server_exceptions=False)
        return client.post(
            f"/api/projects/{_PROJ}/sprints/{label}/complete-step",
            json={"confirmed": True},
        )
    finally:
        for p in patches:
            p.stop()


# ── AC1: child whose parent branch is deleted ─────────────────────────────────

class TestAC1ChildWithDeletedParentBranch:
    """complete-step on a child whose parent branch no longer exists must
    retarget to the next surviving ancestor branch."""

    def test_retargets_to_surviving_grandparent_branch(self):
        """When parent sprint/sprint-119.1 is deleted but grandparent
        sprint/sprint-119 still exists, merge goes to sprint-119 branch."""
        alive = {"sprint/sprint-119.2", "sprint/sprint-119"}

        def branch_exists(repo, branch):
            return branch in alive

        def parent(root, lbl):
            return {"sprint-119.2": "sprint-119.1", "sprint-119.1": "sprint-119"}.get(lbl, "sprint-119")

        calls: list = []
        resp = _complete_step("sprint-119.2", branch_exists, parent, merge_calls=calls)

        assert resp.status_code == 200, resp.text
        data = resp.json()
        # Must merge into the surviving grandparent branch
        assert ("sprint/sprint-119.2", "sprint/sprint-119") in calls, calls
        assert data["merged_into"] == "sprint-119"
        assert data["merged"] is True

    def test_falls_back_to_develop_when_entire_lineage_chain_deleted(self):
        """When both parent (sprint-119.1) and grandparent base (sprint-119)
        branches are deleted, the child should merge directly into develop."""
        alive = {"sprint/sprint-119.2"}  # only the child branch itself

        def branch_exists(repo, branch):
            return branch in alive

        def parent(root, lbl):
            return {"sprint-119.2": "sprint-119.1", "sprint-119.1": "sprint-119"}.get(lbl, "sprint-119")

        calls: list = []
        resp = _complete_step("sprint-119.2", branch_exists, parent, merge_calls=calls)

        assert resp.status_code == 200, resp.text
        data = resp.json()
        # Must merge into develop (ultimate ancestor) not a deleted branch
        assert ("sprint/sprint-119.2", "develop") in calls, calls
        assert data["merged_into"] == "develop"
        assert data["merged"] is True

    def test_no_merge_when_commits_already_in_surviving_ancestor(self):
        """If head commits are already reachable from the surviving ancestor,
        no merge is attempted (true idempotent no-op)."""
        alive = {"sprint/sprint-119.2", "sprint/sprint-119"}

        def branch_exists(repo, branch):
            return branch in alive

        def parent(root, lbl):
            return {"sprint-119.2": "sprint-119.1", "sprint-119.1": "sprint-119"}.get(lbl, "sprint-119")

        def already_merged(repo, head, base):
            # No unmerged commits relative to the surviving ancestor
            return False

        calls: list = []
        resp = _complete_step(
            "sprint-119.2", branch_exists, parent,
            merge_calls=calls, unmerged_fn=already_merged,
        )

        assert resp.status_code == 200, resp.text
        assert calls == [], f"Expected no merge but got: {calls}"
        assert resp.json()["merged"] is False  # idempotent no-op


# ── AC2: base step refuses if children have unmerged commits or are running ───

class TestAC2BaseStepGuardsChildren:
    """Base complete-step must 409 when a child sprint is still running or has
    an unmerged branch with commits."""

    def test_refuses_when_child_sprint_is_running(self):
        """409 when a child sprint is still running."""
        def branch_exists(repo, branch):
            return True

        def parent(root, lbl):
            return "sprint-119"  # base sprint has no parent

        def children(parent_label, project_root=None, project=None):
            if parent_label == "sprint-119":
                return ["sprint-119.1"]
            return []

        def running(root, lbl):
            return lbl == "sprint-119.1"  # child is running

        resp = _complete_step(
            "sprint-119",  # base sprint
            branch_exists, parent,
            children_fn=children,
            running_fn=running,
        )

        assert resp.status_code == 409, resp.text
        detail = resp.json().get("detail", "")
        assert "sprint-119.1" in detail

    def test_refuses_when_child_has_unmerged_branch(self):
        """409 when a child sprint's branch still exists and has unmerged commits."""
        def branch_exists(repo, branch):
            # Both base and child branches exist
            return branch in ("sprint/sprint-119", "sprint/sprint-119.1")

        def parent(root, lbl):
            return {"sprint-119.1": "sprint-119"}.get(lbl, "develop")

        def children(parent_label, project_root=None, project=None):
            if parent_label == "sprint-119":
                return ["sprint-119.1"]
            return []

        def has_unmerged(repo, head, base):
            # Child branch has unmerged commits
            return head == "sprint/sprint-119.1"

        resp = _complete_step(
            "sprint-119",
            branch_exists, parent,
            children_fn=children,
            unmerged_fn=has_unmerged,
        )

        assert resp.status_code == 409, resp.text
        detail = resp.json().get("detail", "")
        assert "sprint-119.1" in detail

    def test_proceeds_when_child_branch_deleted_and_no_pending_commits(self):
        """Base step proceeds normally when child branch is already deleted
        (has been merged and pruned)."""
        def branch_exists(repo, branch):
            # Only the base branch exists; child was merged and branch deleted
            return branch == "sprint/sprint-119"

        def parent(root, lbl):
            return {"sprint-119.1": "sprint-119"}.get(lbl, "develop")

        def children(parent_label, project_root=None, project=None):
            if parent_label == "sprint-119":
                return ["sprint-119.1"]
            return []

        calls: list = []
        resp = _complete_step(
            "sprint-119",
            branch_exists, parent,
            children_fn=children,
            merge_calls=calls,
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["merged_into"] == "develop"


# ── AC3: base close sweep skips tickets on unmerged child labels ──────────────

class TestAC3BaseCloseSweep:
    """Base close sweep must not close tickets that are open on a not-yet-merged
    child sprint label."""

    def test_skips_closing_tickets_on_unmerged_child_label(self):
        """Issues carrying a not-yet-merged child sprint label must not be closed."""
        # Issue 100: has sprint-119 (base) label only → should be closed
        # Issue 101: has sprint-119.1 (child, branch still exists) → should NOT be closed
        sprint_issues = [
            {"number": 100, "labels": [{"name": "sprint-119"}]},
            {"number": 101, "labels": [{"name": "sprint-119.1"}]},
        ]

        def branch_exists(repo, branch):
            # child branch still exists (not yet merged)
            return branch in ("sprint/sprint-119", "sprint/sprint-119.1")

        def parent(root, lbl):
            return {"sprint-119.1": "sprint-119"}.get(lbl, "develop")

        def children(parent_label, project_root=None, project=None):
            if parent_label == "sprint-119":
                return ["sprint-119.1"]
            return []

        # Child has no unmerged commits to its parent (so AC2 doesn't block us),
        # but the branch still exists (so AC3 filter applies).
        def has_unmerged(repo, head, base):
            return False  # no pending commits for AC2 check

        closed: list = []
        resp = _complete_step(
            "sprint-119",
            branch_exists, parent,
            children_fn=children,
            unmerged_fn=has_unmerged,
            close_calls=closed,
            sprint_issues=sprint_issues,
        )

        assert resp.status_code == 200, resp.text
        assert 100 in closed, f"Issue 100 (base label only) should be closed; got {closed}"
        assert 101 not in closed, f"Issue 101 (unmerged child label) must NOT be closed; got {closed}"

    def test_closes_all_tickets_when_all_children_merged(self):
        """When all child branches are deleted (merged), all tickets are closed."""
        sprint_issues = [
            {"number": 100, "labels": [{"name": "sprint-119"}]},
            {"number": 101, "labels": [{"name": "sprint-119.1"}]},
        ]

        def branch_exists(repo, branch):
            # Child branch deleted (merged and pruned); only base exists
            return branch == "sprint/sprint-119"

        def parent(root, lbl):
            return {"sprint-119.1": "sprint-119"}.get(lbl, "develop")

        def children(parent_label, project_root=None, project=None):
            if parent_label == "sprint-119":
                return ["sprint-119.1"]
            return []

        def has_unmerged(repo, head, base):
            return False

        closed: list = []
        resp = _complete_step(
            "sprint-119",
            branch_exists, parent,
            children_fn=children,
            unmerged_fn=has_unmerged,
            close_calls=closed,
            sprint_issues=sprint_issues,
        )

        assert resp.status_code == 200, resp.text
        # Both tickets closed since child branch is gone (merged)
        assert sorted(closed) == [100, 101], f"Expected both closed; got {closed}"
