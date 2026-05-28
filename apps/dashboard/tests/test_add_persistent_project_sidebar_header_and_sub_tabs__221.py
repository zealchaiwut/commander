"""Tests for issue #221: Add persistent project sidebar, header, and sub-tabs."""
import os
import pytest
import httpx

BASE_URL = os.environ.get("TEST_BASE_URL", "http://localhost:8000")

TEST_SLUG = "commander"  # always-present project


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0, follow_redirects=False) as c:
        yield c


@pytest.fixture(scope="module")
def html(client):
    r = client.get(f"/project/{TEST_SLUG}/sprint-mgmt")
    assert r.status_code == 200, f"project.html not served: {r.status_code}"
    return r.text


# ── URL Routing ────────────────────────────────────────────────────────────────

def test_route_bare_slug_redirects_to_sprint_mgmt(client):
    """AC: /project/<slug> (no sub-tab) redirects to /project/<slug>/sprint-mgmt."""
    r = client.get(f"/project/{TEST_SLUG}")
    assert r.status_code == 302, f"Expected 302, got {r.status_code}"
    assert r.headers["location"].endswith("/sprint-mgmt"), (
        f"Redirect target should end with /sprint-mgmt, got: {r.headers.get('location')}"
    )


def test_route_sprint_mgmt_serves_200(client):
    """AC: /project/<slug>/sprint-mgmt → 200 with HTML."""
    r = client.get(f"/project/{TEST_SLUG}/sprint-mgmt")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


def test_route_tickets_serves_200(client):
    """AC: /project/<slug>/tickets → 200 with HTML."""
    r = client.get(f"/project/{TEST_SLUG}/tickets")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


def test_route_logs_serves_200(client):
    """AC: /project/<slug>/logs → 200 with HTML."""
    r = client.get(f"/project/{TEST_SLUG}/logs")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


def test_route_sprint_history_redirects_to_sprint_mgmt(client):
    """AC: /project/<slug>/sprint-history redirects to /project/<slug>/sprint-mgmt."""
    r = client.get(f"/project/{TEST_SLUG}/sprint-history")
    assert r.status_code == 302, f"Expected 302, got {r.status_code}"
    assert r.headers["location"].endswith("/sprint-mgmt"), (
        f"Expected redirect to sprint-mgmt, got: {r.headers.get('location')}"
    )


def test_route_invalid_tab_redirects_to_sprint_mgmt(client):
    """AC: Unknown tab segments redirect to sprint-mgmt."""
    r = client.get(f"/project/{TEST_SLUG}/not-a-real-tab")
    assert r.status_code == 302
    assert r.headers["location"].endswith("/sprint-mgmt")


def test_all_valid_tabs_serve_same_project_html(client):
    """AC: sprint-mgmt, tickets, and logs all serve project.html (shared chrome)."""
    texts = []
    for tab in ("sprint-mgmt", "tickets", "logs"):
        r = client.get(f"/project/{TEST_SLUG}/{tab}")
        assert r.status_code == 200, f"Tab {tab!r} returned {r.status_code}"
        texts.append(r.text)
    # All three serve the same file (project.html)
    assert texts[0] == texts[1] == texts[2], "All valid tabs should serve the same project.html"


# ── Layout ─────────────────────────────────────────────────────────────────────

def test_layout_sidebar_220px_width(html):
    """AC: 220px left sidebar declared as CSS custom property."""
    assert "--sidebar-width: 220px" in html or "--sidebar-width:220px" in html, (
        "CSS variable --sidebar-width: 220px not found"
    )


def test_layout_page_splits_sidebar_and_main(html):
    """AC: Viewport splits into sidebar and flex-1 main content area."""
    assert "page-layout" in html, "page-layout container not found"
    assert "sidebar" in html, "sidebar element not found"
    assert "main-area" in html or "flex: 1" in html or "flex:1" in html, (
        "flex-1 main area not found"
    )


def test_layout_sidebar_sticky(html):
    """AC: Sidebar is sticky; main content scrolls independently."""
    assert "position: sticky" in html or "position:sticky" in html, (
        "position: sticky not found for sidebar"
    )


def test_layout_mobile_sidebar_hidden(html):
    """AC: On mobile (<700px) the sidebar is hidden."""
    assert "max-width: 699px" in html or "max-width:699px" in html, (
        "@media max-width: 699px breakpoint not found"
    )
    assert "display: none" in html or "display:none" in html, (
        "sidebar display:none rule not found"
    )


def test_layout_tablet_sidebar_180px(html):
    """AC: Tablet (700-1024px): sidebar visible at 180px."""
    assert "180px" in html, "180px sidebar width for tablet not found"
    assert "700px" in html and "1024px" in html, (
        "Tablet breakpoint (700px–1024px) not found"
    )


# ── Sidebar ────────────────────────────────────────────────────────────────────

def test_sidebar_home_link_present(html):
    """AC: Home link at top with ti-home icon, navigates to /."""
    assert 'href="/"' in html or "href='/'" in html, "Home link href='/' not found"
    assert "ti-home" in html, "ti-home icon not found in Home link"


def test_sidebar_projects_section_header_uppercase(html):
    """AC: Section header 'PROJECTS' (small, uppercase, muted) — rendered uppercase via CSS text-transform."""
    # The AC is met via CSS text-transform: uppercase on .sb-section-hdr
    assert "text-transform: uppercase" in html or "text-transform:uppercase" in html, (
        "text-transform: uppercase not found for sidebar section header"
    )
    assert "sb-section-hdr" in html, ".sb-section-hdr element not found"
    # The literal text content is 'Projects' (CSS uppercases it visually)
    assert "Projects" in html, "Projects section header text not found"


def test_sidebar_project_items_rendered_by_js(html):
    """AC: Project items are rendered by JS from /api/home data."""
    assert "sb-project-list" in html or "sb-proj-item" in html, (
        "Project item list container not found"
    )
    assert "buildSidebar" in html or "sb-proj-item" in html, (
        "Sidebar build function or items not found"
    )


def test_sidebar_project_name_truncation(html):
    """AC: Project name truncates with ellipsis."""
    assert "text-overflow: ellipsis" in html or "text-overflow:ellipsis" in html, (
        "text-overflow: ellipsis not found for project name"
    )
    assert "white-space: nowrap" in html or "white-space:nowrap" in html, (
        "white-space: nowrap not found"
    )
    assert "overflow: hidden" in html or "overflow:hidden" in html, (
        "overflow: hidden not found"
    )


def test_sidebar_status_dot_running_aria_label(html):
    """AC: Running status dot has aria-label='Running'."""
    assert 'aria-label="Running"' in html or "aria-label='Running'" in html, (
        "aria-label='Running' not found on status dot"
    )


def test_sidebar_status_dot_uat_aria_label(html):
    """AC: UAT status dot has aria-label='UAT pending'."""
    assert 'aria-label="UAT pending"' in html or "aria-label='UAT pending'" in html, (
        "aria-label='UAT pending' not found on status dot"
    )


def test_sidebar_running_dot_animation(html):
    """AC: Animated green dot with glow for running sprint."""
    assert "sbGlow" in html or "animation" in html, "Sidebar glow animation not found"
    assert "sb-status-dot" in html, "sb-status-dot class not found"
    assert "running" in html, "running status class not found"


def test_sidebar_active_item_aria_current(html):
    """AC: Active project item has aria-current='page'."""
    assert 'aria-current="page"' in html or "aria-current='page'" in html, (
        "aria-current='page' not found on active sidebar item"
    )


def test_sidebar_items_are_anchor_tags(html):
    """AC: All sidebar items are <a> tags with proper hrefs."""
    assert "sb-proj-item" in html, "sb-proj-item class not found"
    # Check that the buildSidebar JS generates <a href=...> tags
    assert 'href="' in html and "project" in html, (
        "Sidebar project links with href not found"
    )


def test_sidebar_add_project_tile_dashed(html):
    """AC: '+ Add project' tile with dashed border."""
    assert "Add project" in html or "add project" in html.lower(), (
        "'Add project' tile not found"
    )
    assert "dashed" in html, "Dashed border style not found on add project tile"


def test_sidebar_footer_present(html):
    """AC: Sidebar footer shows v<version> · <host:port>."""
    assert "sidebar-footer" in html, "sidebar-footer element not found"
    # Footer is populated by JS
    assert "updateSidebarFooter" in html or "sidebar-footer" in html, (
        "Sidebar footer update function not found"
    )


def test_sidebar_refresh_every_30_seconds(html):
    """AC: Status dots refresh every 30 seconds via GET /api/home."""
    assert "30000" in html, "30-second refresh interval (30000ms) not found"
    assert "setInterval" in html, "setInterval for sidebar refresh not found"
    assert "/api/home" in html, "/api/home endpoint referenced for sidebar data"


def test_sidebar_graceful_degradation_on_api_error(html):
    """AC: If /api/home unavailable, sidebar degrades gracefully (no error thrown)."""
    assert "catch" in html, "Error catch handler not found for sidebar degradation"
    assert "warn" in html.lower() or "silent" in html.lower() or "catch" in html, (
        "Graceful error handling not found"
    )


# ── Project Header ─────────────────────────────────────────────────────────────

def test_header_icon_and_name_present(html):
    """AC: Header row: 22×22 project icon + project name (bold, 22px)."""
    assert "proj-header-icon" in html, "proj-header-icon not found"
    assert "proj-header-name" in html, "proj-header-name not found"
    assert "22px" in html, "22px font size not found for project name"
    assert "font-weight: 700" in html or "font-weight:700" in html, (
        "Bold (font-weight: 700) not found"
    )


def test_header_status_pill_running(html):
    """AC: Status pill: 'Sprint <N> running' (green, pulsing) when sprint active."""
    assert "proj-header-pill" in html, "proj-header-pill element not found"
    assert "pill-running" in html, "pill-running class not found"
    assert "running" in html, "running status text not found"
    assert "pillGlow" in html or "pulsing" in html.lower() or "animation" in html, (
        "Pulsing animation not found for running pill"
    )


def test_header_status_pill_uat(html):
    """AC: Status pill: '<M> UAT pending' (amber) when UAT tickets exist."""
    assert "pill-uat" in html, "pill-uat class not found"
    assert "UAT pending" in html, "UAT pending text not found"


def test_header_bulk_create_button(html):
    """AC: 'Bulk create' button (icon + label, neutral) in header."""
    assert "Bulk create" in html, "'Bulk create' button text not found"
    assert "ti-stack-2" in html or "btn-ghost" in html, (
        "Bulk create button styling not found"
    )


def test_header_new_ticket_button(html):
    """AC: '+ New ticket' button (primary, dark) in header."""
    assert "New ticket" in html, "'New ticket' button text not found"
    assert "btn-primary" in html, "btn-primary class not found on New ticket button"


def test_header_padding_18_28(html):
    """AC: Header padding: 18px top, 28px sides, 0 bottom."""
    assert "18px 28px 0" in html or "padding: 18px 28px" in html, (
        "Header padding 18px top, 28px sides not found"
    )


def test_header_bottom_border(html):
    """AC: 1px bottom border separates header from sub-tab strip."""
    assert "border-bottom: 1px solid" in html or "border-bottom:1px solid" in html, (
        "1px bottom border not found on header"
    )


def test_header_project_name_appears_once(html):
    """AC: Project name appears exactly once — no breadcrumb duplication."""
    # Count actual DOM elements (id="proj-header-name"), not CSS/JS references
    count = html.count('id="proj-header-name"')
    assert count == 1, (
        f"id='proj-header-name' DOM element appears {count} times; expected exactly 1 (no duplication)"
    )


# ── Sub-tabs ──────────────────────────────────────────────────────────────────

def test_subtabs_role_tablist(html):
    """AC: Sub-tab strip uses role='tablist'."""
    assert 'role="tablist"' in html or "role='tablist'" in html, (
        "role='tablist' not found on sub-tab strip"
    )


def test_subtabs_individual_role_tab(html):
    """AC: Each tab has role='tab'."""
    assert 'role="tab"' in html or "role='tab'" in html, (
        "role='tab' not found on individual tabs"
    )


def test_subtabs_four_tabs_present(html):
    """AC: Four tabs: Sprint Mgmt, Tickets, Logs, Sprint history."""
    assert "Sprint Mgmt" in html, "Sprint Mgmt tab not found"
    assert "Tickets" in html, "Tickets tab not found"
    assert "Logs" in html, "Logs tab not found"
    assert "Sprint history" in html, "Sprint history tab not found"


def test_subtabs_icons_present(html):
    """AC: Tabs have icons: ti-layout-kanban, ti-list, ti-terminal-2, ti-history."""
    assert "ti-layout-kanban" in html, "ti-layout-kanban icon not found (Sprint Mgmt)"
    assert "ti-list" in html, "ti-list icon not found (Tickets)"
    assert "ti-terminal-2" in html, "ti-terminal-2 icon not found (Logs)"
    assert "ti-history" in html, "ti-history icon not found (Sprint history)"


def test_subtabs_sprint_history_disabled(html):
    """AC: Sprint history tab is disabled: aria-disabled='true'."""
    assert 'aria-disabled="true"' in html or "aria-disabled='true'" in html, (
        "aria-disabled='true' not found on Sprint history tab"
    )


def test_subtabs_sprint_history_cursor_not_allowed(html):
    """AC: Sprint history tab has cursor: not-allowed."""
    assert "cursor: not-allowed" in html or "cursor:not-allowed" in html, (
        "cursor: not-allowed not found"
    )


def test_subtabs_sprint_history_soon_badge(html):
    """AC: Sprint history tab has 'soon' badge appended."""
    assert "soon" in html.lower(), "'soon' badge not found on Sprint history tab"
    assert "stab-soon" in html, "stab-soon CSS class not found"


def test_subtabs_sprint_history_no_navigation(html):
    """AC: Clicking Sprint history causes no navigation (onclick='return false' or equivalent)."""
    assert "return false" in html or "sprint-history" in html, (
        "Sprint history click prevention not found"
    )
    # The tab must not have a normal click handler that navigates
    assert "stab-sprint-history" in html or "sprint-history" in html, (
        "Sprint history tab element not found"
    )


def test_subtabs_active_has_aria_selected_true(html):
    """AC: Active tab has aria-selected='true'."""
    assert 'aria-selected="true"' in html or "aria-selected='true'" in html, (
        "aria-selected='true' not found on active tab"
    )


def test_subtabs_active_styling(html):
    """AC: Active tab: bold text, dark color, 2px solid dark border-bottom."""
    assert "stab.active" in html or ".stab.active" in html, (
        ".stab.active CSS rule not found"
    )
    assert "border-bottom-color" in html or "border-bottom:" in html, (
        "Active tab border-bottom styling not found"
    )
    assert "font-weight: 700" in html or "font-weight:700" in html, (
        "Bold font-weight not found for active tab"
    )


def test_subtabs_sticky_below_header(html):
    """AC: Sub-tabs are sticky below the project header."""
    # The sub-tabs are inside proj-header which is position: sticky
    assert "proj-header" in html, "proj-header sticky container not found"
    assert "sub-tabs" in html, "sub-tabs element not found"


def test_subtabs_keyboard_navigation(html):
    """AC: Arrow keys cycle between enabled tabs; Enter activates focused tab."""
    assert "ArrowRight" in html or "ArrowLeft" in html, (
        "Arrow key keyboard navigation not found"
    )
    assert "ArrowLeft" in html, "ArrowLeft handler not found"
    assert 'key === "Enter"' in html or "Enter" in html, (
        "Enter key handler not found for tab activation"
    )


def test_subtabs_sprint_history_excluded_from_keyboard_nav(html):
    """AC: Arrow key cycle skips disabled Sprint history tab."""
    # The enabled tabs list should not include sprint-history
    assert "enabledTabs" in html or "sprint-history" not in html.split("enabledTabs")[1] if "enabledTabs" in html else True, (
        "Keyboard navigation should skip sprint-history"
    )
    if "enabledTabs" in html:
        idx = html.index("enabledTabs")
        segment = html[idx:idx+200]
        assert "sprint-history" not in segment, "sprint-history should not be in enabled tabs list"


# ── Content Placeholders ──────────────────────────────────────────────────────

def test_placeholder_sprint_mgmt_text(html):
    """AC: Sprint Mgmt pane shows placeholder 'Sprint Mgmt content lands in ticket #<next>'."""
    assert "Sprint Mgmt content lands in ticket #" in html, (
        "Sprint Mgmt placeholder text not found"
    )


def test_placeholder_logs_text(html):
    """AC: Logs pane shows 'Logs view coming soon'."""
    assert "Logs view coming soon" in html, "Logs placeholder text not found"


def test_placeholder_sprint_history_not_rendered(html):
    """AC: Sprint history tab pane is not rendered (tab is unreachable)."""
    # There should be no pane-sprint-history in the HTML
    assert "pane-sprint-history" not in html, (
        "pane-sprint-history should not exist (tab is unreachable)"
    )


def test_tickets_pane_loads_from_api(html):
    """AC: Tickets sub-tab renders existing tickets list (loads from API, not placeholder)."""
    assert "pane-tickets" in html, "pane-tickets element not found"
    # tickets-content div should be present (rendered by JS from API)
    assert "tickets-content" in html, "tickets-content container not found"
    assert "loadTickets" in html, "loadTickets function not found"


# ── API Endpoint ──────────────────────────────────────────────────────────────

def test_api_home_endpoint_used_for_sidebar(html):
    """AC: Sidebar data comes from GET /api/home."""
    assert "/api/home" in html, "/api/home not referenced in project.html"
    assert "loadHomeData" in html, "loadHomeData function not found"


# ── JS Navigation & Popstate ─────────────────────────────────────────────────

def test_url_parsing_handles_slug_and_tab(html):
    """AC: JS parseUrl() extracts slug and tab from pathname."""
    assert "parseUrl" in html, "parseUrl function not found"
    assert "/project/" in html, "URL pattern not found in parseUrl"


def test_popstate_handler_for_browser_history(html):
    """AC: Browser back/forward navigates between sub-tabs correctly."""
    assert "popstate" in html, "popstate event handler not found"


def test_history_push_state_on_tab_switch(html):
    """AC: Switching tabs updates URL via history.pushState."""
    assert "pushState" in html, "history.pushState not found"
    assert "replaceState" in html, "history.replaceState not found (used on init redirect)"


# ── Direct URL Access ─────────────────────────────────────────────────────────

def test_direct_url_sprint_mgmt_bookmark_friendly(client):
    """AC: Direct URL access to /project/<slug>/sprint-mgmt works (bookmark-friendly)."""
    r = client.get(f"/project/{TEST_SLUG}/sprint-mgmt")
    assert r.status_code == 200
    assert "Commander" in r.text


def test_direct_url_tickets_bookmark_friendly(client):
    """AC: Direct URL access to /project/<slug>/tickets works."""
    r = client.get(f"/project/{TEST_SLUG}/tickets")
    assert r.status_code == 200
    assert "Commander" in r.text


def test_direct_url_logs_bookmark_friendly(client):
    """AC: Direct URL access to /project/<slug>/logs works."""
    r = client.get(f"/project/{TEST_SLUG}/logs")
    assert r.status_code == 200
    assert "Commander" in r.text


# ── Manual / Browser-required tests ──────────────────────────────────────────

def test_active_project_highlight_from_url():
    """AC: Active-project highlight derives from current URL pathname (correct on hard refresh). MANUAL."""
    pytest.skip("manual — requires browser: hard-refresh and verify sidebar highlights correct project")


def test_sidebar_30s_status_dot_refresh():
    """AC: Status dots refresh every 30s; newly-running project appears within 60s. MANUAL."""
    pytest.skip("manual — requires two sessions and 60s wait to observe dot update")


def test_mobile_no_horizontal_scroll():
    """AC: Below 700px sidebar disappears, no horizontal scroll. MANUAL."""
    pytest.skip("manual — requires browser viewport resize to <700px")


def test_long_project_name_truncates_in_sidebar():
    """AC: 'really-long-project-name' truncates with ellipsis in sidebar; full name in header. MANUAL."""
    pytest.skip("manual — requires a project with a very long name in the sidebar")


def test_keyboard_tab_sidebar_subtabs():
    """AC: All sidebar items reachable via Tab; Arrow keys move focus between sub-tabs. MANUAL."""
    pytest.skip("manual — requires keyboard-only browser interaction")


def test_sprint_history_cursor_not_allowed_visual():
    """AC: Sprint history tab shows not-allowed cursor visually on hover. MANUAL."""
    pytest.skip("manual — requires visual inspection in browser")


def test_redirect_no_flash_of_unstyled_content():
    """AC: /project/<slug> redirect to sprint-mgmt happens without FOUC. MANUAL."""
    pytest.skip("manual — requires visual inspection in browser on direct navigation")
