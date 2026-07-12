"""Tests for issue #1851 — port by-agent and by-ticket token tables into Status sub-tab.

AC coverage:
  AC1 — Status sub-tab renders tokens-by-agent-role table and top-tickets-by-tokens
        table from /api/projects/{slug}/analytics/cost (no new backend work)
  AC2 — Ticket rows link to the GitHub issue
  AC3 — Label counts block demoted below the token tables
  AC4 — Empty state handled: quiet message when no token data, not an error
"""
from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"
_STATIC_DIR = _DASHBOARD_ROOT / "static"
_PROJECT_HTML = _STATIC_DIR / "project.html"


@pytest.fixture(scope="module")
def html() -> str:
    assert _PROJECT_HTML.exists(), "project.html must exist"
    return _PROJECT_HTML.read_text(encoding="utf-8")


def _status_panel(html: str) -> str:
    """Return just the Status panel markup (from anl-panel-status to anl-panel-trends)."""
    start = html.find('id="anl-panel-status"')
    end = html.find('id="anl-panel-trends"', start)
    assert start >= 0 and end > start
    return html[start:end]


# ── AC1: Token tables present in Status panel ─────────────────────────────────

class TestTokenTablesPresentInStatusPanel:
    def test_status_panel_has_tokens_agent_body(self, html):
        assert 'id="status-tokens-agent-body"' in html, \
            "Status panel must contain a status-tokens-agent-body element"

    def test_status_panel_has_tokens_ticket_body(self, html):
        assert 'id="status-tokens-ticket-body"' in html, \
            "Status panel must contain a status-tokens-ticket-body element"

    def test_status_refresh_fetches_cost_endpoint(self, html):
        idx = html.find("async function statusRefresh")
        assert idx >= 0, "statusRefresh must be defined"
        body = html[idx:idx + 2500]
        assert "analytics/cost" in body, \
            "statusRefresh must fetch the analytics/cost endpoint"

    def test_render_tokens_by_agent_function_exists(self, html):
        assert "function _statusRenderTokensByAgent" in html, \
            "_statusRenderTokensByAgent function must be defined"

    def test_render_tokens_by_ticket_function_exists(self, html):
        assert "function _statusRenderTokensByTicket" in html, \
            "_statusRenderTokensByTicket function must be defined"

    def test_render_functions_called_from_status_refresh(self, html):
        idx = html.find("async function statusRefresh")
        assert idx >= 0
        body = html[idx:idx + 2500]
        assert "_statusRenderTokensByAgent" in body, \
            "statusRefresh must call _statusRenderTokensByAgent"
        assert "_statusRenderTokensByTicket" in body, \
            "statusRefresh must call _statusRenderTokensByTicket"


# ── AC2: Ticket rows link to GitHub issues ────────────────────────────────────

class TestTicketRowsLinkToGitHub:
    def test_render_ticket_function_builds_anchor_links(self, html):
        idx = html.find("function _statusRenderTokensByTicket")
        assert idx >= 0, "_statusRenderTokensByTicket must be defined"
        body = html[idx:idx + 1200]
        assert "href" in body, \
            "_statusRenderTokensByTicket must build <a href=...> links for ticket rows"

    def test_ticket_links_point_to_github_issues(self, html):
        idx = html.find("function _statusRenderTokensByTicket")
        assert idx >= 0
        body = html[idx:idx + 1200]
        assert "github.com" in body or "issues/" in body, \
            "Ticket links in _statusRenderTokensByTicket must reference GitHub issue URLs"


# ── AC3: Label counts demoted below token tables ──────────────────────────────

class TestLabelCountsDemoted:
    def test_tokens_agent_appears_before_labels_in_status_panel(self, html):
        panel = _status_panel(html)
        agent_pos = panel.find('id="status-tokens-agent-body"')
        labels_pos = panel.find('id="status-labels-body"')
        assert agent_pos >= 0, "status-tokens-agent-body must be in the Status panel"
        assert labels_pos >= 0, "status-labels-body must still exist in the Status panel"
        assert agent_pos < labels_pos, \
            "Tokens-by-agent table must appear before the label counts block in the Status panel"

    def test_tokens_ticket_appears_before_labels_in_status_panel(self, html):
        panel = _status_panel(html)
        ticket_pos = panel.find('id="status-tokens-ticket-body"')
        labels_pos = panel.find('id="status-labels-body"')
        assert ticket_pos >= 0, "status-tokens-ticket-body must be in the Status panel"
        assert ticket_pos < labels_pos, \
            "Tokens-by-ticket table must appear before the label counts block in the Status panel"


# ── AC4: Empty state ──────────────────────────────────────────────────────────

class TestEmptyState:
    def test_tokens_by_agent_has_quiet_empty_state(self, html):
        idx = html.find("function _statusRenderTokensByAgent")
        assert idx >= 0, "_statusRenderTokensByAgent must be defined"
        body = html[idx:idx + 900]
        has_empty = (
            "status-none-msg" in body
            or "No token data" in body
            or "no token" in body.lower()
        )
        assert has_empty, \
            "_statusRenderTokensByAgent must render a quiet empty-state message when rows=[]"

    def test_tokens_by_ticket_has_quiet_empty_state(self, html):
        idx = html.find("function _statusRenderTokensByTicket")
        assert idx >= 0, "_statusRenderTokensByTicket must be defined"
        body = html[idx:idx + 900]
        has_empty = (
            "status-none-msg" in body
            or "No token data" in body
            or "no token" in body.lower()
        )
        assert has_empty, \
            "_statusRenderTokensByTicket must render a quiet empty-state message when rows=[]"
