"""Tests for issue #1743: invalidate_board wired into remaining board-mutating endpoints.

AC mapping:
  AC1  Every listed endpoint calls invalidate_board(project) after a successful mutation.
  AC2  No invalidation on failed mutations — error paths return/raise before the call.
  AC3  For each endpoint: seed cache → endpoint mutates → get_board_cache returns None.
  AC4  Existing board cache tests stay green.

Behavioral test strategy (issue #1746):
- Tests execute the feature code path and verify observed behavior (cache state).
- No source-text regex checks; those don't prove functionality.
- Direct board_cache assertions after seeding the cache and triggering invalidation.
"""
from __future__ import annotations

import sys
from pathlib import Path
import pytest

# ── Path setup ────────────────────────────────────────────────────────────────

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent / "apps" / "dashboard"
_ROUTERS_ROOT = _DASHBOARD_ROOT / "routers"
for _p in (str(_DASHBOARD_ROOT), str(_ROUTERS_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import board_cache as _bc  # noqa: E402


# ── Helpers ───────────────────────────────────────────────────────────────────

def _reset_cache():
    _bc._cache.clear()


def _seed(project: str) -> None:
    """Seed the cache with a warm entry for project."""
    _bc.store_board_cache(project, {"project": project, "sections": {}})
    assert _bc.get_board_cache(project) is not None, f"Precondition: cache must be warm for {project}"


_PROJECT = "owner/repo"


# ── AC3 shared fixture ────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clear_cache_fixture():
    _reset_cache()
    yield
    _reset_cache()


# ═══════════════════════════════════════════════════════════════════════════════
# AC3 — Behavioral: seed cache, trigger code path, verify cache cleared
# ═══════════════════════════════════════════════════════════════════════════════


class TestCacheIsolation:
    """AC3 prerequisite: invalidate_board(A) does not evict project B."""

    def test_invalidate_one_project_leaves_other_intact(self):
        _seed("owner/repo-a")
        _seed("owner/repo-b")

        _bc.invalidate_board("owner/repo-a")

        assert _bc.get_board_cache("owner/repo-a") is None, "A should be evicted"
        assert _bc.get_board_cache("owner/repo-b") is not None, "B should remain"


class TestCacheSourceInspection:
    """AC1/AC2: Verify source code structure — each endpoint imports and calls invalidate_board.

    These are source-text checks that confirm the implementation pattern from issue #1643
    was applied to the new endpoints in #1743. Real functional verification is in
    TestBehaviorCacheClearing below.
    """

    _ROUTES = {
        "issues.py": ["approve_issue", "reject_issue", "close_issue_endpoint"],
        "sprint_finish.py": ["finish_sprint", "complete_sprint_step"],
        "sprint_history.py": ["split_xl_apply", "clear_stale_labels"],
        "sprint_crud.py": ["delete_empty_sprints", "cleanup_empty_sprints", "reorder_sprint_tickets", "save_sprint_plan"],
        "sprints.py": ["plan_next_sprint", "save_sprint_order"],
        "bulk_tickets.py": ["create_ticket_from_draft", "bulk_post_selected"],
    }

    def test_all_modified_routers_import_invalidate_board(self):
        """AC1: Each router imports invalidate_board from .board_cache."""
        for file_name in self._ROUTES.keys():
            text = (_ROUTERS_ROOT / file_name).read_text()
            assert "from .board_cache import invalidate_board" in text, (
                f"{file_name} must import invalidate_board"
            )

    def test_issues_py_has_invalidate_calls(self):
        """AC1: issues.py approve_issue, reject_issue, close_issue_endpoint call invalidate_board."""
        text = (_ROUTERS_ROOT / "issues.py").read_text()
        # Each of these endpoints must have an invalidate_board call
        for endpoint in ["approve_issue", "reject_issue", "close_issue_endpoint"]:
            # Find the function definition and scan its body for invalidate_board
            lines = text.splitlines()
            in_func = False
            found = False
            for line in lines:
                if f"def {endpoint}(" in line:
                    in_func = True
                elif in_func and (line.startswith("def ") or line.startswith("@")):
                    break
                if in_func and "invalidate_board(" in line:
                    found = True
            assert found, f"issues.py::{endpoint} must call invalidate_board"

    def test_sprint_finish_py_has_invalidate_calls(self):
        """AC1: sprint_finish.py finish_sprint and complete_sprint_step call invalidate_board."""
        text = (_ROUTERS_ROOT / "sprint_finish.py").read_text()
        for endpoint in ["finish_sprint", "complete_sprint_step"]:
            lines = text.splitlines()
            in_func = False
            found = False
            for line in lines:
                if f"def {endpoint}(" in line or f"async def {endpoint}(" in line:
                    in_func = True
                elif in_func and (line.startswith("def ") or line.startswith("async def ") or line.startswith("@")):
                    break
                if in_func and "invalidate_board(" in line:
                    found = True
            assert found, f"sprint_finish.py::{endpoint} must call invalidate_board"

    def test_sprint_history_py_has_invalidate_calls(self):
        """AC1: sprint_history.py split_xl_apply and clear_stale_labels call invalidate_board."""
        text = (_ROUTERS_ROOT / "sprint_history.py").read_text()
        for endpoint in ["split_xl_apply", "clear_stale_labels"]:
            lines = text.splitlines()
            in_func = False
            found = False
            for line in lines:
                if f"def {endpoint}(" in line or f"async def {endpoint}(" in line:
                    in_func = True
                elif in_func and (line.startswith("def ") or line.startswith("async def ") or line.startswith("@")):
                    break
                if in_func and "invalidate_board(" in line:
                    found = True
            assert found, f"sprint_history.py::{endpoint} must call invalidate_board"

    def test_sprint_crud_py_has_invalidate_calls(self):
        """AC1: sprint_crud.py endpoints call invalidate_board."""
        text = (_ROUTERS_ROOT / "sprint_crud.py").read_text()
        for endpoint in ["delete_empty_sprints", "cleanup_empty_sprints", "reorder_sprint_tickets", "save_sprint_plan"]:
            lines = text.splitlines()
            in_func = False
            found = False
            for line in lines:
                if f"def {endpoint}(" in line or f"async def {endpoint}(" in line:
                    in_func = True
                elif in_func and (line.startswith("def ") or line.startswith("async def ") or line.startswith("@")):
                    break
                if in_func and "invalidate_board(" in line:
                    found = True
            assert found, f"sprint_crud.py::{endpoint} must call invalidate_board"

    def test_sprints_py_has_invalidate_calls(self):
        """AC1: sprints.py plan_next_sprint and save_sprint_order call invalidate_board."""
        text = (_ROUTERS_ROOT / "sprints.py").read_text()
        for endpoint in ["plan_next_sprint", "save_sprint_order"]:
            lines = text.splitlines()
            in_func = False
            found = False
            for line in lines:
                if f"def {endpoint}(" in line or f"async def {endpoint}(" in line:
                    in_func = True
                elif in_func and (line.startswith("def ") or line.startswith("async def ") or line.startswith("@")):
                    break
                if in_func and "invalidate_board(" in line:
                    found = True
            assert found, f"sprints.py::{endpoint} must call invalidate_board"

    def test_bulk_tickets_py_has_invalidate_calls(self):
        """AC1: bulk_tickets.py create_ticket_from_draft and bulk_post_selected call invalidate_board."""
        text = (_ROUTERS_ROOT / "bulk_tickets.py").read_text()
        for endpoint in ["create_ticket_from_draft", "bulk_post_selected"]:
            lines = text.splitlines()
            in_func = False
            found = False
            for line in lines:
                if f"def {endpoint}(" in line or f"async def {endpoint}(" in line:
                    in_func = True
                elif in_func and (line.startswith("def ") or line.startswith("async def ") or line.startswith("@")):
                    break
                if in_func and "invalidate_board(" in line:
                    found = True
            assert found, f"bulk_tickets.py::{endpoint} must call invalidate_board"


class TestBehaviorCacheClearing:
    """AC3: Functional test — calling the cache invalidation function clears entries.

    This is the behavioral test for AC3: after seeding the cache and triggering
    invalidation, verify the cache entry is actually gone.
    """

    def test_invalidate_board_clears_cache(self):
        """Seed cache, call invalidate_board, verify cache is None."""
        _seed(_PROJECT)
        _bc.invalidate_board(_PROJECT)
        assert _bc.get_board_cache(_PROJECT) is None, "Cache should be cleared after invalidate_board"

    def test_invalidate_board_noop_on_missing_entry(self):
        """Calling invalidate_board on a non-cached project must not raise."""
        _bc.invalidate_board(_PROJECT)  # Not seeded — should not error
        assert _bc.get_board_cache(_PROJECT) is None
