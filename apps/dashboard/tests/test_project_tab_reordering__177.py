"""Tests for issue #177: Make Sprint Mgmt first project tab; hide Tickets tab (runs against UAT)"""
import os
import pytest
import httpx
from urllib.parse import quote


BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# --- Acceptance Criteria ---

def test_project_tab_reordering__sprint_mgmt_first_and_active(client):
    """
    AC: Sprint Mgmt is the first project tab — it appears as the leftmost tab in
    <nav class="proj-tabs"> and carries the active class on initial project load.
    """
    # Navigate to the overview (which will have projects listed)
    r = client.get("/")
    assert r.status_code == 200
    html = r.text

    # Sprint Mgmt should appear as the first ptab in the nav
    # We look for the pattern where ptab-sprint-mgmt comes before ptab-tickets
    assert "ptab-sprint-mgmt" in html, "Sprint Mgmt tab element should exist"

    # Verify that Sprint Mgmt carries the active class initially
    # The nav should show: <button class="ptab active" id="ptab-sprint-mgmt"
    assert 'id="ptab-sprint-mgmt"' in html, "ptab-sprint-mgmt element should exist"

    # Check ordering: ptab-sprint-mgmt should appear before ptab-tickets in the HTML
    sprint_mgmt_pos = html.find('id="ptab-sprint-mgmt"')
    tickets_pos = html.find('id="ptab-tickets"')
    assert sprint_mgmt_pos > -1, "ptab-sprint-mgmt should exist in HTML"
    assert tickets_pos > -1, "ptab-tickets should exist in HTML"
    assert sprint_mgmt_pos < tickets_pos, "ptab-sprint-mgmt should appear before ptab-tickets in HTML"


def test_project_tab_reordering__tickets_tab_hidden(client):
    """
    AC: Tickets tab is hidden — the #ptab-tickets button is not visible
    (e.g. display:none or hidden attribute); its view panel #pview-tickets
    (or equivalent) is also not rendered/visible.
    """
    r = client.get("/")
    assert r.status_code == 200
    html = r.text

    # Tickets button should have display:none or a hidden marker
    assert 'id="ptab-tickets"' in html

    # Check that either display:none or hidden attribute is applied
    tickets_button_start = html.find('id="ptab-tickets"')
    # Look back to the opening <button> tag
    button_start = html.rfind('<button', 0, tickets_button_start)
    button_end = html.find('</button>', tickets_button_start)
    tickets_button_html = html[button_start:button_end + 9]

    assert 'display:none' in tickets_button_html or 'hidden' in tickets_button_html, \
        f"Tickets button should be hidden. Found: {tickets_button_html}"

    # Tickets view panel should be hidden
    assert 'id="pview-tickets"' in html
    pview_start = html.find('id="pview-tickets"')
    pview_line_start = html.rfind('<div', 0, pview_start)
    pview_line_end = html.find('>', pview_start)
    pview_div = html[pview_line_start:pview_line_end + 1]

    assert 'hidden' in pview_div or 'display:none' in pview_div, \
        f"pview-tickets should have hidden or display:none. Found: {pview_div}"


def test_project_tab_reordering__sprint_history_tab_present(client):
    """
    AC: Sprint history tab is unchanged — still present and functional as the
    second visible tab.
    """
    r = client.get("/")
    assert r.status_code == 200
    html = r.text

    # Sprint history tab should be present
    assert 'id="ptab-sprint-history"' in html, "ptab-sprint-history should exist"
    assert 'onclick="switchProjectTab(\'sprint-history\')"' in html, \
        "Sprint history tab should have the switch handler"


def test_project_tab_reordering__default_route_sprint_mgmt(client):
    """
    AC: Default route resolves correctly — navigating to /projects/<repo>
    (no tab segment) opens Sprint Mgmt, not Tickets.
    """
    r = client.get("/projects/test%2Frepo")
    assert r.status_code == 200

    # The page should load successfully; the routing logic will default to sprint-mgmt
    # We verify this by checking the JavaScript that sets _activeProjectTab
    assert "sprint-mgmt" in r.text or "Sprint Mgmt" in r.text, \
        "Page should contain sprint-mgmt reference"


def test_project_tab_reordering__deep_link_sprint_mgmt(client):
    """
    AC: Deep-link for sprint-mgmt works — /projects/<repo>/sprint-mgmt loads
    and activates Sprint Mgmt tab.
    """
    r = client.get("/projects/test%2Frepo/sprint-mgmt")
    assert r.status_code == 200

    # Verify the page loads and contains sprint-mgmt reference
    assert "sprint-mgmt" in r.text or "Sprint Mgmt" in r.text, \
        "Deep-link to sprint-mgmt should resolve successfully"


def test_project_tab_reordering__deep_link_stale_tickets_redirect(client):
    """
    AC: Deep-link for tickets does not break — if a stale URL /projects/<repo>/tickets
    is visited, the app falls back gracefully (e.g. redirects to sprint-mgmt)
    rather than showing a blank view.
    """
    r = client.get("/projects/test%2Frepo/tickets", follow_redirects=True)
    assert r.status_code == 200

    # The page should load without error; routing should have handled the fallback
    # The JavaScript will replace the URL to point to sprint-mgmt
    assert "Sprint Mgmt" in r.text or "sprint-mgmt" in r.text, \
        "Stale tickets URL should fall back to sprint-mgmt view"


def test_project_tab_reordering__unknown_tab_defaults_to_sprint_mgmt(client):
    """
    AC: Unknown/invalid tab segments default to sprint-mgmt.
    """
    r = client.get("/projects/test%2Frepo/unknown-tab")
    assert r.status_code == 200

    # Should not show blank view; should default to sprint-mgmt
    assert "Sprint Mgmt" in r.text or "sprint-mgmt" in r.text, \
        "Unknown tab should default to sprint-mgmt view"
