"""Tests for issue #274: Add Backlog pane to Sprint Management board.

Covers AC items 1-6 via static analysis of project.html HTML+JS and
github_client.py backend logic.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

DASHBOARD_DIR = Path(__file__).parent.parent
PROJECT_HTML  = DASHBOARD_DIR / "static" / "project.html"
GH_CLIENT_PY  = DASHBOARD_DIR / "github_client.py"


@pytest.fixture(scope="module")
def html() -> str:
    return PROJECT_HTML.read_text()


@pytest.fixture(scope="module")
def gh_client() -> str:
    return GH_CLIENT_PY.read_text()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _extract_function(source: str, fn_name: str, window: int = 6000) -> str:
    for prefix in (f"async function {fn_name}(", f"function {fn_name}("):
        idx = source.find(prefix)
        if idx != -1:
            return source[idx: idx + window]
    raise AssertionError(f"Function '{fn_name}' not found in source")


# ── AC1: Backlog pane renders as final block with dashed border and grey tint ──

class TestAC1BacklogPanePresent:
    def test_backlog_pane_element_exists(self, html):
        """project.html must contain the smgmt-backlog-pane element."""
        assert 'id="smgmt-backlog-pane"' in html

    def test_backlog_pane_has_dashed_border_css(self, html):
        """Backlog pane CSS must include a dashed border style."""
        assert "dashed" in html, (
            "project.html must have a dashed border CSS rule for the backlog pane"
        )

    def test_backlog_pane_has_grey_tint_css(self, html):
        """Backlog pane CSS must use var(--surface-2) or equivalent grey tint background."""
        # Extract .smgmt-backlog-pane CSS block
        idx = html.find(".smgmt-backlog-pane {")
        assert idx != -1, ".smgmt-backlog-pane CSS rule not found"
        block = html[idx: idx + 400]
        assert "surface-2" in block or "surface" in block, (
            ".smgmt-backlog-pane must set a grey tint background (e.g., var(--surface-2))"
        )

    def test_backlog_pane_is_after_sprint_list(self, html):
        """Backlog pane must appear after smgmt-sprint-list in the DOM."""
        sprint_list_idx = html.find('id="smgmt-sprint-list"')
        backlog_idx     = html.find('id="smgmt-backlog-pane"')
        assert sprint_list_idx != -1 and backlog_idx != -1
        assert backlog_idx > sprint_list_idx, (
            "Backlog pane must appear after smgmt-sprint-list in project.html"
        )

    def test_backlog_pane_has_drag_over_handler(self, html):
        """Backlog pane element must wire ondragover, ondragleave, and ondrop handlers."""
        idx = html.find('id="smgmt-backlog-pane"')
        assert idx != -1
        pane_block = html[idx: idx + 500]
        assert "ondragover=" in pane_block
        assert "ondragleave=" in pane_block
        assert "ondrop=" in pane_block

    def test_backlog_ticket_container_id_exists(self, html):
        """smgmt-backlog-tickets container div must exist."""
        assert 'id="smgmt-backlog-tickets"' in html


# ── AC2: Backlog pane lists unassigned / backlog-labelled tickets ─────────────

class TestAC2BacklogTickets:
    def test_render_fn_collects_unassigned(self, html):
        """_smgmtRender must collect issues with sprint==null into an unassigned array."""
        fn = _extract_function(html, "_smgmtRender")
        assert "unassigned" in fn, (
            "_smgmtRender must collect unassigned tickets (sprint == null)"
        )
        # The else branch pushes to unassigned
        assert "unassigned.push(iss)" in fn

    def test_render_fn_calls_render_backlog(self, html):
        """_smgmtRender must call _smgmtRenderBacklog with the unassigned tickets."""
        fn = _extract_function(html, "_smgmtRender")
        assert "_smgmtRenderBacklog(unassigned)" in fn, (
            "_smgmtRender must call _smgmtRenderBacklog(unassigned)"
        )

    def test_render_backlog_fn_exists(self, html):
        """_smgmtRenderBacklog function must be defined."""
        assert "_smgmtRenderBacklog" in html

    def test_render_backlog_updates_count_badge(self, html):
        """_smgmtRenderBacklog must update the smgmt-backlog-count badge element."""
        fn = _extract_function(html, "_smgmtRenderBacklog")
        assert "smgmt-backlog-count" in fn, (
            "_smgmtRenderBacklog must update the count badge"
        )

    def test_render_backlog_called_before_sprints_check(self, html):
        """_smgmtRenderBacklog is called before the sprints.length === 0 early-return.

        This ensures the backlog pane always shows, even when no sprints exist.
        """
        fn = _extract_function(html, "_smgmtRender", window=3000)
        backlog_idx = fn.find("_smgmtRenderBacklog")
        sprint_early_return_idx = fn.find("sprints.length === 0")
        assert backlog_idx != -1 and sprint_early_return_idx != -1
        assert backlog_idx < sprint_early_return_idx, (
            "_smgmtRenderBacklog must be called before the sprints.length === 0 check"
        )


# ── AC3: Drag from backlog to sprint applies sprint label, removes backlog ────

class TestAC3DragBacklogToSprint:
    def test_backlog_ticket_drag_start_fn_exists(self, html):
        """_smgmtBacklogTicketDragStart must be defined."""
        assert "_smgmtBacklogTicketDragStart" in html

    def test_backlog_ticket_drag_start_sets_from_sprint_null(self, html):
        """_smgmtBacklogTicketDragStart must set fromSprint to null (backlog origin)."""
        fn = _extract_function(html, "_smgmtBacklogTicketDragStart")
        assert "fromSprint: null" in fn, (
            "_smgmtBacklogTicketDragStart must set fromSprint: null to identify backlog origin"
        )

    def test_drop_on_sprint_fn_exists(self, html):
        """_smgmtDropOnSprint must be defined and call the sprint-label API."""
        fn = _extract_function(html, "_smgmtDropOnSprint")
        assert "/api/issues/" in fn or "sprint-label" in fn, (
            "_smgmtDropOnSprint must call the sprint-label assignment API"
        )

    def test_drop_on_sprint_uses_sprint_label_endpoint(self, html):
        """_smgmtDropOnSprint calls /api/issues/{N}/sprint-label."""
        fn = _extract_function(html, "_smgmtDropOnSprint")
        assert "sprint-label" in fn

    def test_assign_sprint_removes_backlog_label_in_client(self, gh_client):
        """github_client.assign_sprint must remove the 'backlog' label when assigning a sprint."""
        # Python function — use def pattern
        idx = gh_client.find("def assign_sprint(")
        assert idx != -1, "assign_sprint not found in github_client.py"
        fn = gh_client[idx: idx + 2000]
        assert '"backlog"' in fn or "'backlog'" in fn, (
            "assign_sprint must check for and remove the 'backlog' label"
        )
        assert "remove_labels.append" in fn or "remove_labels" in fn, (
            "assign_sprint must add 'backlog' to remove_labels when assigning a sprint"
        )


# ── AC4: Drag from sprint to backlog strips sprint label ─────────────────────

class TestAC4DragSprintToBacklog:
    def test_drop_on_backlog_fn_exists(self, html):
        """_smgmtDropOnBacklog must be defined."""
        assert "_smgmtDropOnBacklog" in html

    def test_drop_on_backlog_calls_api_with_backlog_sprint_label(self, html):
        """_smgmtDropOnBacklog calls /api/issues/{N}/sprint-label with sprint_label='backlog'."""
        fn = _extract_function(html, "_smgmtDropOnBacklog")
        assert "sprint_label" in fn
        assert "'backlog'" in fn or '"backlog"' in fn

    def test_drop_on_backlog_sets_sprint_null_optimistically(self, html):
        """_smgmtDropOnBacklog sets iss.sprint = null as optimistic update."""
        fn = _extract_function(html, "_smgmtDropOnBacklog")
        assert "iss.sprint = null" in fn, (
            "_smgmtDropOnBacklog must optimistically set iss.sprint = null"
        )

    def test_backlog_drag_over_fn_exists(self, html):
        """_smgmtBacklogDragOver must be defined and accept drops from sprint panes."""
        assert "_smgmtBacklogDragOver" in html
        fn = _extract_function(html, "_smgmtBacklogDragOver")
        assert "preventDefault" in fn or "event.preventDefault" in fn, (
            "_smgmtBacklogDragOver must call event.preventDefault() to accept drop"
        )


# ── AC5: "Move to" pill on hover performs same label mutations as DnD ─────────

class TestAC5MoveToDropdown:
    def test_moveto_pill_css_exists(self, html):
        """project.html must define .smgmt-moveto-pill CSS class."""
        assert ".smgmt-moveto-pill" in html

    def test_moveto_pill_in_backlog_ticket_html(self, html):
        """_smgmtBacklogTicketHtml must include a Move to pill button."""
        fn = _extract_function(html, "_smgmtBacklogTicketHtml")
        assert "smgmt-moveto-pill" in fn or "Move to" in fn, (
            "_smgmtBacklogTicketHtml must render a 'Move to' pill button"
        )

    def test_moveto_pill_in_sprint_ticket_row_html(self, html):
        """_smgmtTicketRowHtml must include a Move to pill button on sprint tickets."""
        fn = _extract_function(html, "_smgmtTicketRowHtml")
        assert "smgmt-moveto-pill" in fn or "_smgmtMoveToOpen" in fn, (
            "_smgmtTicketRowHtml must render a 'Move to' pill button"
        )

    def test_moveto_open_fn_exists(self, html):
        """_smgmtMoveToOpen must be defined to open the Move to popup."""
        assert "_smgmtMoveToOpen" in html

    def test_moveto_confirm_fn_calls_api(self, html):
        """_smgmtMoveToConfirm must call /api/issues/{N}/sprint-label API."""
        fn = _extract_function(html, "_smgmtMoveToConfirm")
        assert "sprint-label" in fn, (
            "_smgmtMoveToConfirm must call the sprint-label API to move the ticket"
        )

    def test_moveto_popup_element_exists(self, html):
        """A shared 'Move to' popup element (smgmt-moveto-popup) must exist in the DOM."""
        assert 'id="smgmt-moveto-popup"' in html

    def test_moveto_includes_backlog_option_for_sprint_tickets(self, html):
        """_smgmtMoveToOpen must include a Backlog option when ticket is in a sprint."""
        fn = _extract_function(html, "_smgmtMoveToOpen")
        assert "Backlog" in fn or "backlog" in fn, (
            "_smgmtMoveToOpen must offer a Backlog destination for sprint tickets"
        )


# ── AC6: Board reflects changes without full page reload ─────────────────────

class TestAC6RealtimeUpdate:
    def test_drop_on_sprint_calls_load_sprint_mgmt(self, html):
        """_smgmtDropOnSprint calls loadSprintMgmt() after API call to refresh board."""
        fn = _extract_function(html, "_smgmtDropOnSprint")
        assert "loadSprintMgmt()" in fn, (
            "_smgmtDropOnSprint must call loadSprintMgmt() to refresh without full reload"
        )

    def test_drop_on_backlog_calls_load_sprint_mgmt(self, html):
        """_smgmtDropOnBacklog calls loadSprintMgmt() after API call to refresh board."""
        fn = _extract_function(html, "_smgmtDropOnBacklog")
        assert "loadSprintMgmt()" in fn, (
            "_smgmtDropOnBacklog must call loadSprintMgmt() to refresh without full reload"
        )

    def test_moveto_confirm_calls_load_sprint_mgmt(self, html):
        """_smgmtMoveToConfirm calls loadSprintMgmt() after API call."""
        fn = _extract_function(html, "_smgmtMoveToConfirm")
        assert "loadSprintMgmt()" in fn, (
            "_smgmtMoveToConfirm must call loadSprintMgmt() to refresh board"
        )

    def test_optimistic_render_before_api_call(self, html):
        """_smgmtMoveToConfirm updates _smgmtData and calls _smgmtRender before await fetch()."""
        fn = _extract_function(html, "_smgmtMoveToConfirm")
        render_idx = fn.find("_smgmtRender(_smgmtData)")
        fetch_idx  = fn.find("await fetch(")
        assert render_idx != -1 and fetch_idx != -1
        assert render_idx < fetch_idx, (
            "_smgmtMoveToConfirm must optimistically render before the API fetch"
        )
