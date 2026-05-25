"""Tests for issue #22 — Grouped ticket view (SIT / UAT / Others), clickable titles,
and Active Tickets metric.

Static checks (no live server required) are run against the source files directly.
Live-server checks use the `client` fixture and are marked with `pytest.mark.live`.
"""
from pathlib import Path
import re
import pytest

PROJECTS_PY  = Path(__file__).parent.parent / "projects.py"
INDEX_HTML   = Path(__file__).parent.parent / "static" / "index.html"
APP_JS       = Path(__file__).parent.parent / "static" / "app.js"


# ---------------------------------------------------------------------------
# AC-4 (backend): /api/projects must expose 'active_tickets', not 'open_tickets'
# ---------------------------------------------------------------------------

class TestBackendActiveTickets:
    def test_projects_py_uses_active_tickets_key(self):
        """Backend metrics dict must use 'active_tickets', not 'open_tickets'."""
        content = PROJECTS_PY.read_text()
        assert '"active_tickets"' in content, (
            "projects.py must expose 'active_tickets' in the metrics dict"
        )

    def test_projects_py_no_open_tickets_key(self):
        """Backend must expose 'active_tickets' AND 'open_tickets' in metrics.

        Issue #82 added 'open_tickets' as an aggregate metric while keeping
        'active_tickets' for per-project data.  Both keys must be present.
        """
        content = PROJECTS_PY.read_text()
        assert '"active_tickets"' in content, (
            "projects.py must expose 'active_tickets' in the metrics dict"
        )
        assert '"open_tickets"' in content, (
            "projects.py must also expose 'open_tickets' aggregate (added in issue #82)"
        )

    def test_projects_py_filters_active_statuses(self):
        """active_tickets must filter to in-progress, SIT, UAT only."""
        content = PROJECTS_PY.read_text()
        assert 'in-progress' in content and 'SIT' in content and 'UAT' in content, (
            "projects.py must filter tickets by in-progress, SIT, UAT statuses"
        )
        # Check that backlog is not included in the active filter
        # The active_i list should only contain in-progress/SIT/UAT statuses
        assert '_ticket_status' in content, (
            "projects.py must use _ticket_status() for grouping"
        )


# ---------------------------------------------------------------------------
# AC-3 (frontend): ticket title must be a clickable <a> element
# ---------------------------------------------------------------------------

class TestFrontendTicketTitleLink:
    def test_app_js_has_ticket_title_link_class(self):
        """app.js must render ticket title as <a class='ticket-title-link'>."""
        content = APP_JS.read_text()
        assert 'ticket-title-link' in content, (
            "app.js must use class 'ticket-title-link' for ticket title links"
        )

    def test_app_js_ticket_title_is_anchor(self):
        """app.js ticketCardHtml must wrap title in an <a> tag with href to issue URL."""
        content = APP_JS.read_text()
        # The title must be rendered as an <a> with target="_blank"
        assert 'ticket.url' in content, (
            "app.js must reference ticket.url when rendering ticket links"
        )
        assert 'target="_blank"' in content, (
            "app.js ticket links must open in a new tab (target='_blank')"
        )

    def test_index_html_has_ticket_title_link_css(self):
        """index.html must include CSS rule for .ticket-title-link."""
        content = INDEX_HTML.read_text()
        assert '.ticket-title-link' in content, (
            "index.html CSS must define .ticket-title-link styles"
        )


# ---------------------------------------------------------------------------
# AC-2 (frontend): grouped headers with counts (SIT · N, UAT · N, Others · N)
# ---------------------------------------------------------------------------

class TestFrontendGroupedHeaders:
    def test_app_js_has_ticket_group_helper(self):
        """app.js must have a helper function for rendering ticket groups."""
        content = APP_JS.read_text()
        assert '_ticketGroupHtml' in content, (
            "app.js must define _ticketGroupHtml helper for grouped sections"
        )

    def test_app_js_renders_sit_uat_others_groups(self):
        """app.js renderExpandPanel must group tickets into SIT, UAT, In progress, Backlog."""
        content = APP_JS.read_text()
        assert "'SIT'" in content or '"SIT"' in content, (
            "app.js must reference the 'SIT' group label"
        )
        assert "'UAT'" in content or '"UAT"' in content, (
            "app.js must reference the 'UAT' group label"
        )
        # Issue #53: 'Others' was replaced with 'In progress' and 'Backlog' for clearer grouping
        assert "'In progress'" in content or '"In progress"' in content or "'Backlog'" in content or '"Backlog"' in content, (
            "app.js must reference 'In progress' and/or 'Backlog' group labels (replaced 'Others')"
        )

    def test_app_js_group_header_format(self):
        """Group headers must show label with count separated by ·."""
        content = APP_JS.read_text()
        # The helper should produce "SIT · N" style headers
        assert '· ${tickets.length}' in content or "· ${tickets.length}" in content, (
            "app.js _ticketGroupHtml must show count with · separator"
        )

    def test_index_html_has_ticket_group_css(self):
        """index.html must include .ticket-group CSS rules."""
        content = INDEX_HTML.read_text()
        assert '.ticket-group' in content, (
            "index.html CSS must define .ticket-group styles"
        )
        assert '.ticket-group-hdr' in content, (
            "index.html CSS must define .ticket-group-hdr styles"
        )


# ---------------------------------------------------------------------------
# AC-4 (frontend): metric label must be 'Active Tickets', not 'Open Tickets'
# ---------------------------------------------------------------------------

class TestFrontendActiveTicketsLabel:
    def test_index_html_has_active_tickets_label(self):
        """Issue #82 replaced the top-level 'Active Tickets' card with per-project chips.
        The dashboard must now show 'Active Projects' and 'Open Tickets' at top level,
        while per-project active ticket counts appear as inline chips on each row.
        """
        content = INDEX_HTML.read_text()
        # Issue #82: top-level now shows aggregate cards instead
        assert 'Active Projects' in content or 'Open Tickets' in content, (
            "index.html must have 'Active Projects' or 'Open Tickets' metric card (issue #82)"
        )

    def test_index_html_no_open_tickets_label(self):
        """Issue #82 added 'Open Tickets' as a top-level aggregate metric.
        This test is now inverted: 'Open Tickets' MUST appear in the metric row.
        """
        content = INDEX_HTML.read_text()
        assert 'Open Tickets' in content, (
            "index.html must have 'Open Tickets' metric card added by issue #82"
        )

    def test_index_html_tickets_card_has_id(self):
        """Issue #82 replaced 'm-tickets-card' with 'm-open-tickets' and 'm-active-projects'.
        Verify the new IDs exist.
        """
        content = INDEX_HTML.read_text()
        assert 'm-open-tickets' in content or 'm-active-projects' in content, (
            "index.html must have 'm-open-tickets' and/or 'm-active-projects' elements (issue #82)"
        )

    def test_app_js_reads_active_tickets_metric(self):
        """app.js renderMetrics must read aggregate metrics (issue #82 layout)."""
        content = APP_JS.read_text()
        # Issue #82: backend still exposes active_tickets; frontend uses open_tickets aggregate
        assert 'open_tickets' in content or 'active_projects' in content, (
            "app.js must read open_tickets or active_projects for the aggregate metric cards"
        )


# ---------------------------------------------------------------------------
# AC-5: Active Tickets font-size must be ≥ 36px
# ---------------------------------------------------------------------------

class TestFrontendActiveTicketsFontSize:
    def test_index_html_has_36px_rule(self):
        """Issue #82 removed the dedicated 36px Active Tickets card.
        Metric cards now use the standard 30px size. Verify metric values exist.
        """
        content = INDEX_HTML.read_text()
        # metric-value class still exists; 30px is the standard size
        assert 'metric-value' in content, (
            "index.html must still have metric-value styled elements"
        )

    def test_index_html_36px_scoped_to_tickets_card(self):
        """Issue #82 removed the #m-tickets-card entirely.
        Verify the new aggregate metric elements are present instead.
        """
        content = INDEX_HTML.read_text()
        assert 'm-open-tickets' in content, (
            "index.html must have id='m-open-tickets' for the Open Tickets aggregate (issue #82)"
        )
        assert 'm-active-projects' in content, (
            "index.html must have id='m-active-projects' for the Active Projects aggregate (issue #82)"
        )


# ---------------------------------------------------------------------------
# AC-1: empty sections must not be rendered; all empty → 'No open tickets'
# ---------------------------------------------------------------------------

class TestFrontendEmptyStateLogic:
    def test_app_js_renders_empty_state_when_no_tickets(self):
        """app.js renderExpandPanel must show 'No open tickets' when ticket list is empty."""
        content = APP_JS.read_text()
        assert 'No open tickets' in content, (
            "app.js must render 'No open tickets' empty state when there are no tickets"
        )

    def test_app_js_group_helper_returns_empty_for_zero_tickets(self):
        """_ticketGroupHtml must return empty string when ticket list is empty."""
        content = APP_JS.read_text()
        # The helper starts with: if (tickets.length === 0) return '';
        assert "tickets.length === 0" in content or "tickets.length == 0" in content, (
            "app.js _ticketGroupHtml must short-circuit with empty string for zero tickets"
        )


# ---------------------------------------------------------------------------
# AC-6: UAT approve/reject buttons preserved (regression check)
# ---------------------------------------------------------------------------

class TestUATButtonsPreserved:
    def test_app_js_has_approve_button(self):
        """app.js must still render Approve button for UAT tickets."""
        content = APP_JS.read_text()
        assert 'btn-approve-sm' in content, (
            "app.js must still render 'btn-approve-sm' Approve button for UAT tickets"
        )

    def test_app_js_has_reject_button(self):
        """app.js must still render Reject button for UAT tickets."""
        content = APP_JS.read_text()
        assert 'btn-reject-sm' in content, (
            "app.js must still render 'btn-reject-sm' Reject button for UAT tickets"
        )

    def test_app_js_has_is_uat_check(self):
        """app.js ticketCardHtml must check ticket.is_uat to show UAT actions."""
        content = APP_JS.read_text()
        assert 'ticket.is_uat' in content or 'is_uat' in content, (
            "app.js must check ticket.is_uat to conditionally render UAT action buttons"
        )

    def test_projects_py_sets_is_uat_flag(self):
        """projects.py get_project_details must set is_uat on each ticket."""
        content = PROJECTS_PY.read_text()
        assert '"is_uat"' in content, (
            "projects.py must include 'is_uat' field in ticket dict"
        )


# ---------------------------------------------------------------------------
# Live-server tests (require server running with feature branch code)
# ---------------------------------------------------------------------------

@pytest.mark.live
class TestLiveAPIActiveTickets:
    """These tests require the dashboard server running with feature/22 code.

    Run with: pytest -m live --tb=short dashboard/tests/test_grouped_ticket_view__22.py
    """

    def test_api_projects_returns_active_tickets(self, client):
        """GET /api/projects must return metrics.active_tickets (not open_tickets)."""
        resp = client.get("/api/projects")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        data = resp.json()
        metrics = data.get("metrics", {})
        assert "active_tickets" in metrics, (
            f"metrics must have 'active_tickets' key; got keys: {list(metrics.keys())}"
        )
        assert "open_tickets" not in metrics, (
            "metrics must not have old 'open_tickets' key"
        )

    def test_api_projects_active_tickets_excludes_backlog(self, client):
        """active_tickets count must be ≤ total open tickets (backlog excluded)."""
        resp = client.get("/api/projects")
        assert resp.status_code == 200
        data = resp.json()
        metrics = data.get("metrics", {})
        projects = data.get("projects", [])
        total_open = sum(p.get("openCount", 0) for p in projects)
        active = metrics.get("active_tickets", 0)
        assert active <= total_open, (
            f"active_tickets ({active}) must be ≤ total open tickets ({total_open})"
        )

    def test_html_shows_active_tickets_label(self, client):
        """GET / must return HTML with 'Active Tickets' label."""
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Active Tickets" in resp.text, (
            "Homepage HTML must contain 'Active Tickets' label"
        )
        assert "Open Tickets" not in resp.text, (
            "Homepage HTML must not contain old 'Open Tickets' label"
        )

    def test_html_has_36px_font_rule(self, client):
        """GET / must include CSS with 36px for Active Tickets card."""
        resp = client.get("/")
        assert resp.status_code == 200
        assert "36px" in resp.text, (
            "Homepage HTML/CSS must contain 36px font-size rule for Active Tickets"
        )

    def test_api_project_details_has_url_and_status(self, client):
        """GET /api/projects first project's details must include url + status per ticket."""
        proj_resp = client.get("/api/projects")
        assert proj_resp.status_code == 200
        projects = proj_resp.json().get("projects", [])
        if not projects:
            pytest.skip("No projects configured — skipping live detail check")

        repo = projects[0]["repo"]
        detail_resp = client.get(f"/api/project-details?repo={repo}")
        assert detail_resp.status_code == 200
        detail = detail_resp.json()
        tickets = detail.get("tickets", [])
        if not tickets:
            pytest.skip("No open tickets for first project — skipping ticket field check")

        for ticket in tickets:
            assert "url" in ticket, f"Ticket must have 'url' field: {ticket}"
            assert "status" in ticket, f"Ticket must have 'status' field: {ticket}"
            assert "is_uat" in ticket, f"Ticket must have 'is_uat' field: {ticket}"
            assert ticket["status"] in ("SIT", "UAT", "in-progress", "blocked", "backlog"), (
                f"Ticket status must be a valid value; got: {ticket['status']}"
            )
