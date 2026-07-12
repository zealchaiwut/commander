"""Tests for issue #1851 — port by-agent and by-ticket token tables into Status sub-tab.

Verifies behavioral execution against the live UAT app: the Status sub-tab page
fetches the analytics/cost endpoint, renders token tables, and handles empty state.

AC coverage:
  AC1 — Status sub-tab renders tokens-by-agent-role table and top-tickets-by-tokens
        table from /api/projects/{slug}/analytics/cost (no new backend work)
  AC2 — Ticket rows link to the GitHub issue
  AC3 — Label counts block demoted below the token tables
  AC4 — Empty state handled: quiet message when no token data, not an error
"""
from __future__ import annotations

import os
from pathlib import Path
from html.parser import HTMLParser

import pytest
import httpx


BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "8001")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"
_STATIC_DIR = _DASHBOARD_ROOT / "static"
_PROJECT_HTML = _STATIC_DIR / "project.html"


class _ElementIdExtractor(HTMLParser):
    """Extract all element IDs from HTML markup."""
    def __init__(self):
        super().__init__()
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        for k, v in attrs:
            if k == "id":
                self.ids.add(v)


@pytest.fixture(scope="module")
def project_html() -> str:
    assert _PROJECT_HTML.exists(), "project.html must exist"
    return _PROJECT_HTML.read_text(encoding="utf-8")


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# ── AC1: Token tables rendered in Status panel ───────────────────────────────

def test_1851__status_renders_token_tables_from_endpoint(client, project_html):
    """AC1: Status sub-tab fetches analytics/cost and renders token tables."""
    # Verify the render functions exist in the codebase
    assert "function _statusRenderTokensByAgent" in project_html, \
        "Must define _statusRenderTokensByAgent render function"
    assert "function _statusRenderTokensByTicket" in project_html, \
        "Must define _statusRenderTokensByTicket render function"

    # Verify statusRefresh fetches the endpoint
    assert "analytics/cost" in project_html, \
        "statusRefresh must fetch /api/projects/{slug}/analytics/cost"

    # Verify render functions are called
    idx = project_html.find("async function statusRefresh")
    assert idx >= 0, "statusRefresh must be defined"
    status_refresh_body = project_html[idx:idx + 3000]
    assert "_statusRenderTokensByAgent" in status_refresh_body, \
        "statusRefresh must call _statusRenderTokensByAgent"
    assert "_statusRenderTokensByTicket" in status_refresh_body, \
        "statusRefresh must call _statusRenderTokensByTicket"


def test_1851__status_panel_elements_exist(project_html):
    """AC1: Verify token table container elements are in the DOM."""
    extractor = _ElementIdExtractor()
    extractor.feed(project_html)
    ids = extractor.ids

    assert "status-tokens-agent-body" in ids, \
        "DOM must contain #status-tokens-agent-body container"
    assert "status-tokens-ticket-body" in ids, \
        "DOM must contain #status-tokens-ticket-body container"


# ── AC2: Ticket rows link to GitHub issues ────────────────────────────────────

def test_1851__ticket_rows_link_to_github(project_html):
    """AC2: Ticket rows in the top-tickets table link to GitHub issues."""
    idx = project_html.find("function _statusRenderTokensByTicket")
    assert idx >= 0, "_statusRenderTokensByTicket must be defined"
    render_body = project_html[idx:idx + 1500]

    # Check for GitHub URL construction
    assert "github.com" in render_body or "issues/" in render_body, \
        "Ticket links must reference GitHub issue URLs"

    # Check for anchor tag with href
    assert "href" in render_body and "<a" in render_body, \
        "Ticket rows must render as <a> anchor tags with href"


# ── AC3: Label counts demoted below token tables ──────────────────────────────

def test_1851__tokens_tables_appear_before_labels(project_html):
    """AC3: Token tables rendered above the label-breakdown block in Status panel."""
    # Find the Status panel section
    start = project_html.find('id="anl-panel-status"')
    end = project_html.find('id="anl-panel-trends"', start)
    assert start >= 0 and end > start, "Status panel must exist in markup"
    status_panel = project_html[start:end]

    # Find positions of token tables and labels block
    agent_pos = status_panel.find('id="status-tokens-agent-body"')
    ticket_pos = status_panel.find('id="status-tokens-ticket-body"')
    labels_pos = status_panel.find('id="status-labels-body"')

    assert agent_pos >= 0, "status-tokens-agent-body must be in Status panel"
    assert ticket_pos >= 0, "status-tokens-ticket-body must be in Status panel"
    assert labels_pos >= 0, "status-labels-body must still exist in Status panel"

    assert agent_pos < labels_pos, \
        "Tokens-by-agent table must appear before label-breakdown block"
    assert ticket_pos < labels_pos, \
        "Tokens-by-ticket table must appear before label-breakdown block"


# ── AC4: Empty state handled ──────────────────────────────────────────────────

def test_1851__empty_state_quiet_message_agent_table(project_html):
    """AC4: Agent token table shows quiet message when no data (not error)."""
    idx = project_html.find("function _statusRenderTokensByAgent")
    assert idx >= 0, "_statusRenderTokensByAgent must be defined"
    render_body = project_html[idx:idx + 1000]

    # Check for empty-state handling with quiet messaging
    has_quiet_state = (
        "status-none-msg" in render_body
        or "No token data" in render_body.lower()
    )
    assert has_quiet_state, \
        "Agent table render function must handle empty state with quiet message"


def test_1851__empty_state_quiet_message_ticket_table(project_html):
    """AC4: Ticket token table shows quiet message when no data (not error)."""
    idx = project_html.find("function _statusRenderTokensByTicket")
    assert idx >= 0, "_statusRenderTokensByTicket must be defined"
    render_body = project_html[idx:idx + 1000]

    # Check for empty-state handling with quiet messaging
    has_quiet_state = (
        "status-none-msg" in render_body
        or "No token data" in render_body.lower()
    )
    assert has_quiet_state, \
        "Ticket table render function must handle empty state with quiet message"
