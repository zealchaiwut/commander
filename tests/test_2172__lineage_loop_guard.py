"""Behavioral tests for issue #2172 — cycle/depth guard on complete-step lineage walk.

AC1: When the sprint lineage contains a cycle (self-referential parent pointers),
     the complete-step endpoint must return 409 instead of spinning forever.

AC2: When the sprint lineage exceeds the maximum allowed depth without converging
     to a base sprint, the complete-step endpoint must return 409 instead of
     hanging indefinitely.

AC3: Normal deep lineages that do converge within the depth limit continue to
     work correctly (no regression).
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
):
    """POST complete-step with GitHub/disk boundaries mocked.

    branch_exists_fn(repo, branch) -> bool
    parent_fn(root, lbl, **kw) -> str  (immediate parent label; must accept **kw)
    """
    import server as srv
    from fastapi.testclient import TestClient

    if merge_calls is None:
        merge_calls = []

    def fake_merge(repo, head, base, title, delete_branch=True, **kw):
        merge_calls.append((head, base))
        return (True, "merged", 42)

    patches = [
        *_dual("_project_root_path", return_value=REPO_ROOT),
        *_dual("_is_sprint_running", return_value=False),
        *_dual("_gh_branch_exists", side_effect=branch_exists_fn),
        *_dual("_branch_has_unmerged_commits", return_value=True),
        *_dual("_gh_merge_branch_via_pr", side_effect=fake_merge),
        *_dual("_sprint_merge_parent_label", side_effect=parent_fn),
        *_dual("_open_summary_issues_for_labels", return_value=[]),
        *_dual("_bulk_complete_collect_issues", return_value=(["sprint-119"], [])),
        *_dual("children_of", return_value=[]),
        *_dual("_plan_json_set_state", return_value=None),
        *_dual("_sprint_db_mark_merged_completed", return_value=True),
        *_dual("_has_rework_tickets", return_value=False),
        patch.object(srv.github_client, "close_issue", return_value=None),
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


# ── AC1: cycle guard ──────────────────────────────────────────────────────────

class TestAC1CycleGuard:
    """complete-step must 409 when the sprint lineage contains a cycle."""

    def test_returns_409_on_two_node_cycle(self):
        """A → B → A cycle in parent pointers must not hang; must 409."""
        # sprint-119.3 (head, exists)
        # sprint-119.2 (parent of .3, branch deleted)
        # sprint-119.1 (parent of .2, branch deleted)
        # sprint-119.2 is again parent of sprint-119.1 → cycle
        def branch_exists(repo, branch):
            return branch == "sprint/sprint-119.3"

        cycle_map = {
            "sprint-119.3": "sprint-119.2",
            "sprint-119.2": "sprint-119.1",
            "sprint-119.1": "sprint-119.2",  # cycle: points back to .2
        }

        def parent(root, lbl, **kw):
            return cycle_map.get(lbl, "sprint-119")

        resp = _complete_step("sprint-119.3", branch_exists, parent)

        assert resp.status_code == 409, resp.text
        detail = resp.json().get("detail", "")
        assert detail, "409 response must include a detail message"

    def test_returns_409_on_self_referential_node(self):
        """A label pointing to itself as parent must 409 immediately."""
        def branch_exists(repo, branch):
            return branch == "sprint/sprint-119.2"

        def parent(root, lbl, **kw):
            # sprint-119.2 → sprint-119.1 → sprint-119.1 (self-loop)
            if lbl == "sprint-119.2":
                return "sprint-119.1"
            return lbl  # self-loop: sprint-119.1 is its own parent

        resp = _complete_step("sprint-119.2", branch_exists, parent)

        assert resp.status_code == 409, resp.text
        detail = resp.json().get("detail", "")
        assert detail, "409 response must include a detail message"


# ── AC2: depth guard ──────────────────────────────────────────────────────────

class TestAC2DepthGuard:
    """complete-step must 409 when lineage exceeds max allowed depth."""

    def test_returns_409_on_excessively_deep_lineage(self):
        """A 60-level deep lineage (all child labels, no surviving branches)
        must 409 rather than traverse indefinitely."""
        # Build a chain: sprint-119.60 → sprint-119.59 → ... → sprint-119.1
        # All branches deleted; sprint-119.1's parent is also a child label
        # (sprint-119.0 doesn't match ^sprint-\d+\.\d+ so it would break out —
        # to keep it a child-only chain, use sprint-119.1.1 style? No — let's
        # just make sprint-119.1's parent another child label sprint-119.0a
        # which doesn't match is_child and would terminate normally)
        #
        # Actually, to keep all nodes as child labels, use sub-numbered labels:
        # sprint-119.60 → sprint-119.59 → ... → sprint-119.1 → base sprint-119
        # But if sprint-119 branch doesn't exist either, the loop continues to
        # the _is_child_sprint_label("sprint-119") → False → terminate path.
        # So the only way to force depth overflow is a cycle or all-deleted chain
        # longer than MAX_DEPTH with a well-formed base at the end.
        #
        # We test depth by creating a long chain with all branches deleted and
        # verifying we get 409 before exhausting the chain (if the depth guard
        # fires before reaching the base).
        DEPTH = 60  # larger than any reasonable MAX_DEPTH

        def branch_exists(repo, branch):
            # only the head exists; all ancestors' branches deleted
            return branch == f"sprint/sprint-119.{DEPTH}"

        def parent(root, lbl, **kw):
            # Build a linear chain: sprint-119.N → sprint-119.(N-1)
            if lbl == f"sprint-119.{DEPTH}":
                return f"sprint-119.{DEPTH - 1}"
            try:
                n = int(lbl.split(".")[-1])
                if n > 1:
                    return f"sprint-119.{n - 1}"
            except (ValueError, IndexError):
                pass
            return "sprint-119"  # eventually reaches base

        resp = _complete_step(f"sprint-119.{DEPTH}", branch_exists, parent)

        assert resp.status_code == 409, resp.text
        detail = resp.json().get("detail", "")
        assert detail, "409 response must include a detail message"


# ── AC3: valid deep lineage still works ───────────────────────────────────────

class TestAC3ValidLineageNoRegression:
    """Normal lineages that converge within the depth limit must still work."""

    def test_retargets_to_surviving_grandparent_within_depth_limit(self):
        """A 3-level lineage where grandparent branch exists must succeed (200)."""
        alive = {"sprint/sprint-119.3", "sprint/sprint-119"}

        def branch_exists(repo, branch):
            return branch in alive

        parent_map = {
            "sprint-119.3": "sprint-119.2",
            "sprint-119.2": "sprint-119.1",
            "sprint-119.1": "sprint-119",
        }

        def parent(root, lbl, **kw):
            return parent_map.get(lbl, "sprint-119")

        calls: list = []
        resp = _complete_step("sprint-119.3", branch_exists, parent, merge_calls=calls)

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["merged"] is True
        assert ("sprint/sprint-119.3", "sprint/sprint-119") in calls, calls

    def test_falls_back_to_develop_when_all_ancestors_deleted_within_depth_limit(self):
        """When all ancestor branches are deleted but lineage terminates at a base
        sprint, merge falls back to develop (not 409)."""
        alive = {"sprint/sprint-119.2"}  # only the head

        def branch_exists(repo, branch):
            return branch in alive

        parent_map = {
            "sprint-119.2": "sprint-119.1",
            "sprint-119.1": "sprint-119",
        }

        def parent(root, lbl, **kw):
            return parent_map.get(lbl, "sprint-119")

        calls: list = []
        resp = _complete_step("sprint-119.2", branch_exists, parent, merge_calls=calls)

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["merged"] is True
        assert ("sprint/sprint-119.2", "develop") in calls, calls
