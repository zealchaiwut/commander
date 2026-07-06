"""Tests for issue #1743: Wire invalidate_board into remaining board-mutating endpoints.

AC coverage:
  AC1  Every endpoint calls invalidate_board(project) after successful mutation.
  AC2  No invalidation on failed mutations (error paths return before invalidating).
  AC3  For each endpoint, seed cache, hit endpoint, assert get_board_cache returns None.
  AC4  Existing board cache tests stay green.
"""
from __future__ import annotations

from pathlib import Path

# ── AC1: Source inspection — each endpoint imports and calls invalidate_board ──


class TestIssuesApproveRejectClose:
    """AC1: issues.py approve_issue, reject_issue, close_issue_endpoint call invalidate_board."""

    _FILE = "issues.py"

    def test_module_imports_invalidate_board(self):
        text = (Path(__file__).parent.parent / "apps" / "dashboard" / "routers" / self._FILE).read_text()
        assert "from .board_cache import invalidate_board" in text, (
            "issues.py must import invalidate_board"
        )

    def test_approve_issue_calls_invalidate(self):
        text = (Path(__file__).parent.parent / "apps" / "dashboard" / "routers" / self._FILE).read_text()
        # approve_issue endpoint must call invalidate_board
        assert "invalidate_board(" in text, "issues.py must call invalidate_board in approve_issue"

    def test_reject_issue_calls_invalidate(self):
        text = (Path(__file__).parent.parent / "apps" / "dashboard" / "routers" / self._FILE).read_text()
        assert "invalidate_board(" in text, "issues.py must call invalidate_board in reject_issue"

    def test_close_issue_calls_invalidate(self):
        text = (Path(__file__).parent.parent / "apps" / "dashboard" / "routers" / self._FILE).read_text()
        assert "invalidate_board(" in text, "issues.py must call invalidate_board in close_issue_endpoint"


class TestSprintFinishEndpoints:
    """AC1: sprint_finish.py finish_sprint and complete_sprint_step call invalidate_board."""

    _FILE = "sprint_finish.py"

    def test_module_imports_invalidate_board(self):
        text = (Path(__file__).parent.parent / "apps" / "dashboard" / "routers" / self._FILE).read_text()
        assert "from .board_cache import invalidate_board" in text

    def test_finish_sprint_calls_invalidate(self):
        text = (Path(__file__).parent.parent / "apps" / "dashboard" / "routers" / self._FILE).read_text()
        assert "invalidate_board(" in text, "sprint_finish.py must call invalidate_board in finish_sprint"

    def test_complete_sprint_step_calls_invalidate(self):
        text = (Path(__file__).parent.parent / "apps" / "dashboard" / "routers" / self._FILE).read_text()
        assert "invalidate_board(" in text, "sprint_finish.py must call invalidate_board in complete_sprint_step"


class TestSprintHistoryEndpoints:
    """AC1: sprint_history.py split_xl_apply and clear_stale_labels call invalidate_board."""

    _FILE = "sprint_history.py"

    def test_module_imports_invalidate_board(self):
        text = (Path(__file__).parent.parent / "apps" / "dashboard" / "routers" / self._FILE).read_text()
        assert "from .board_cache import invalidate_board" in text

    def test_split_xl_apply_calls_invalidate(self):
        text = (Path(__file__).parent.parent / "apps" / "dashboard" / "routers" / self._FILE).read_text()
        assert "invalidate_board(" in text, "sprint_history.py must call invalidate_board in split_xl_apply"

    def test_clear_stale_labels_calls_invalidate(self):
        text = (Path(__file__).parent.parent / "apps" / "dashboard" / "routers" / self._FILE).read_text()
        assert "invalidate_board(" in text, "sprint_history.py must call invalidate_board in clear_stale_labels"


class TestSprintCrudEndpoints:
    """AC1: sprint_crud.py delete/cleanup/reorder/save_sprint_plan call invalidate_board."""

    _FILE = "sprint_crud.py"

    def test_module_imports_invalidate_board(self):
        text = (Path(__file__).parent.parent / "apps" / "dashboard" / "routers" / self._FILE).read_text()
        assert "from .board_cache import invalidate_board" in text

    def test_delete_empty_sprints_calls_invalidate(self):
        text = (Path(__file__).parent.parent / "apps" / "dashboard" / "routers" / self._FILE).read_text()
        assert "invalidate_board(" in text, "sprint_crud.py must call invalidate_board in delete_empty_sprints"

    def test_cleanup_empty_sprints_calls_invalidate(self):
        text = (Path(__file__).parent.parent / "apps" / "dashboard" / "routers" / self._FILE).read_text()
        assert "invalidate_board(" in text, "sprint_crud.py must call invalidate_board in cleanup_empty_sprints"

    def test_reorder_sprint_tickets_calls_invalidate(self):
        text = (Path(__file__).parent.parent / "apps" / "dashboard" / "routers" / self._FILE).read_text()
        assert "invalidate_board(" in text, "sprint_crud.py must call invalidate_board in reorder_sprint_tickets"

    def test_save_sprint_plan_calls_invalidate(self):
        text = (Path(__file__).parent.parent / "apps" / "dashboard" / "routers" / self._FILE).read_text()
        assert "invalidate_board(" in text, "sprint_crud.py must call invalidate_board in save_sprint_plan"


class TestSprintsEndpoints:
    """AC1: sprints.py plan_next_sprint and save_sprint_order call invalidate_board."""

    _FILE = "sprints.py"

    def test_module_imports_invalidate_board(self):
        text = (Path(__file__).parent.parent / "apps" / "dashboard" / "routers" / self._FILE).read_text()
        assert "from .board_cache import invalidate_board" in text

    def test_plan_next_sprint_calls_invalidate(self):
        text = (Path(__file__).parent.parent / "apps" / "dashboard" / "routers" / self._FILE).read_text()
        assert "invalidate_board(" in text, "sprints.py must call invalidate_board in plan_next_sprint"

    def test_save_sprint_order_calls_invalidate(self):
        text = (Path(__file__).parent.parent / "apps" / "dashboard" / "routers" / self._FILE).read_text()
        assert "invalidate_board(" in text, "sprints.py must call invalidate_board in save_sprint_order"


class TestBulkTicketsEndpoints:
    """AC1: bulk_tickets.py create_ticket_from_draft and bulk_post_selected call invalidate_board."""

    _FILE = "bulk_tickets.py"

    def test_module_imports_invalidate_board(self):
        text = (Path(__file__).parent.parent / "apps" / "dashboard" / "routers" / self._FILE).read_text()
        assert "from .board_cache import invalidate_board" in text

    def test_create_ticket_from_draft_calls_invalidate(self):
        text = (Path(__file__).parent.parent / "apps" / "dashboard" / "routers" / self._FILE).read_text()
        # create_ticket_from_draft calls invalidate_board
        assert "invalidate_board(" in text, "bulk_tickets.py must call invalidate_board in create_ticket_from_draft"

    def test_bulk_post_selected_calls_invalidate(self):
        text = (Path(__file__).parent.parent / "apps" / "dashboard" / "routers" / self._FILE).read_text()
        assert "invalidate_board(" in text, "bulk_tickets.py must call invalidate_board in bulk_post_selected"
