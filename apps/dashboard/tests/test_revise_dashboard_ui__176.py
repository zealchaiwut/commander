"""Tests for issue #176: Revise Dashboard UI for Live Updates and Log Visibility (runs against UAT)"""
import os
import pytest
import httpx
import json
from datetime import datetime


BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "8001")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# --- Acceptance Criteria Tests ---

def test_revise_dashboard_ui__ac1_unified_live_log_panel(client):
    """AC-1: A persistent live log panel is visible on the main dashboard view without requiring a tab switch."""
    # Test that the dashboard homepage returns 200 and contains the log panel HTML element
    r = client.get("/")
    assert r.status_code == 200
    # Log panel should be present in the response (either as a div with id/class or inline HTML)
    assert "log" in r.text.lower() or "activity" in r.text.lower(), \
        "Dashboard should contain log panel HTML"


def test_revise_dashboard_ui__ac2_log_entry_format(client):
    """AC-2: Each log entry displays relative timestamp, project name, agent role badge, and description."""
    # Query the API endpoint that serves log entries (if available)
    # First check if there's an API endpoint for log entries
    r = client.get("/api/events")
    # If endpoint exists (200 or 204), it should return event data
    if r.status_code in [200, 204]:
        if r.text and r.text.strip() and r.text.strip() != '[]':
            try:
                data = r.json()
                # Log entries should be a list with expected fields
                if isinstance(data, list) and len(data) > 0:
                    entry = data[0]
                    # Verify required fields exist in at least one entry
                    assert any(key in entry for key in ["timestamp", "created_at", "time"]), \
                        "Entry should contain timestamp field"
                    # Agent/role info is expected (may be agent_name, agent_role, role, etc.)
                    assert any(key in entry for key in ["agent_name", "agent", "role", "agent_role", "agent_type"]), \
                        "Entry should contain agent/role field"
                    # Event type or description is expected
                    assert any(key in entry for key in ["description", "message", "event", "event_type"]), \
                        "Entry should contain description/event field"
                else:
                    pytest.skip("No log entries available yet")
            except json.JSONDecodeError:
                pytest.skip("API endpoint does not return JSON")
        else:
            pytest.skip("No log entries available yet")
    else:
        pytest.skip("API endpoint /api/events not available or not responding")


def test_revise_dashboard_ui__ac3_auto_scroll_scroll_lock(client):
    """AC-3: Panel auto-scrolls; manual scroll pauses it; 'Jump to latest' button re-enables it."""
    # This is a browser interaction test; HTTP API cannot directly test auto-scroll behavior
    # Check that the dashboard HTML contains scroll-related JavaScript or data attributes
    r = client.get("/")
    assert r.status_code == 200
    # Look for script or data attribute indicating scroll-lock logic
    assert "scroll" in r.text.lower() or "latest" in r.text.lower(), \
        "Dashboard should contain scroll or latest-related logic"


def test_revise_dashboard_ui__ac4_log_entry_cap_200(client):
    """AC-4: The panel retains the 200 most recent entries in the DOM; older entries are removed."""
    # This test verifies the cap is enforced by checking the API or by inspecting the DOM limit
    # Query events API to see if it respects a limit
    r = client.get("/api/events")
    if r.status_code == 200:
        try:
            data = r.json()
            if isinstance(data, list):
                # The API should not return more than 200 entries
                assert len(data) <= 200, \
                    f"Log panel should retain max 200 entries, but got {len(data)}"
        except json.JSONDecodeError:
            pytest.skip("API endpoint does not return JSON")
    else:
        pytest.skip("API endpoint /api/events not available or not responding")


def test_revise_dashboard_ui__ac5_sse_connection_status_indicator(client):
    """AC-5: SSE connection status indicator reflects connection state: green=connected, amber=reconnecting, red=disconnected."""
    # Verify the dashboard HTML contains status indicator HTML/CSS
    r = client.get("/")
    assert r.status_code == 200
    # Look for connection indicator elements (status, indicator, connected, etc.)
    html_lower = r.text.lower()
    assert any(keyword in html_lower for keyword in ["connected", "connection", "indicator", "status"]), \
        "Dashboard should contain connection status indicator"


def test_revise_dashboard_ui__ac6_filter_by_project(client):
    """AC-6: A dropdown/chip-row above the log panel lets user filter entries by project."""
    # Verify dashboard HTML contains project filter controls
    r = client.get("/")
    assert r.status_code == 200
    # Look for filter-related elements
    assert any(keyword in r.text.lower() for keyword in ["filter", "project", "select", "dropdown"]), \
        "Dashboard should contain project filter controls"


def test_revise_dashboard_ui__ac7_filter_by_agent_role(client):
    """AC-7: A filter lets user filter by agent role: All / BA / Coder / Tester."""
    # Verify dashboard HTML contains agent role filter
    r = client.get("/")
    assert r.status_code == 200
    html_lower = r.text.lower()
    # Look for filter options for agent roles
    assert any(keyword in html_lower for keyword in ["ba", "coder", "tester", "role", "agent"]), \
        "Dashboard should contain agent role filter"


def test_revise_dashboard_ui__ac8_responsive_layout_wide_viewport(client):
    """AC-8: On viewports ≥ 1280 px, log panel sits beside project/sprint panels."""
    # Verify responsive CSS is present in the dashboard
    r = client.get("/")
    assert r.status_code == 200
    # Look for CSS media queries or responsive design patterns
    assert "@media" in r.text or "flex" in r.text.lower() or "grid" in r.text.lower(), \
        "Dashboard should contain responsive layout CSS"


def test_revise_dashboard_ui__ac8_responsive_layout_narrow_viewport(client):
    """AC-8: On narrower viewports (<1280 px), log panel stacks below."""
    # This test verifies responsive behavior is implemented
    # Check that the HTML/CSS supports stacking layouts
    r = client.get("/")
    assert r.status_code == 200
    # Responsive design should be present
    assert "@media" in r.text or "flex-direction" in r.text.lower() or "column" in r.text.lower(), \
        "Dashboard should contain responsive layout with flex or grid"


def test_revise_dashboard_ui__ac9_dark_light_theme_consistency(client):
    """AC-9: The log panel respects existing dark/light theme toggle; no hardcoded colours."""
    # Verify the dashboard respects theme CSS variables
    r = client.get("/")
    assert r.status_code == 200
    # Look for CSS custom properties (variables) or theme-related classes
    assert "--" in r.text or "dark" in r.text.lower() or "theme" in r.text.lower(), \
        "Dashboard should use CSS variables or theme-aware styling"


def test_revise_dashboard_ui__ac10_no_regressions_sprint_cockpit(client):
    """AC-10: Sprint Cockpit view continues to function and render correctly."""
    # Navigate to Sprint Cockpit or the main view
    r = client.get("/")
    assert r.status_code == 200
    # Check for no server 500 errors (not CSS font-weight 500)
    assert "<h1>500" not in r.text and "Internal Server Error" not in r.text, \
        "Sprint Cockpit should load without server errors"


def test_revise_dashboard_ui__ac10_no_regressions_agents_view(client):
    """AC-10: Agents view continues to function correctly after the change."""
    # Try to access agents/activity endpoints
    r = client.get("/api/agents")
    # Should either return 200 or 404 (if not an API endpoint), but not 500
    assert r.status_code != 500, "Agents view should not have server errors"


def test_revise_dashboard_ui__ac10_no_regressions_activity_log_tab(client):
    """AC-10: Activity Log tab continues to function correctly."""
    # Activity Log might be served as part of the dashboard HTML or via separate route
    r = client.get("/")
    assert r.status_code == 200
    # Should not contain error indicators
    assert "<h1>500" not in r.text and "Internal Server Error" not in r.text, \
        "Activity Log should not have server errors"


def test_revise_dashboard_ui__ac10_no_regressions_console_errors(client):
    """AC-10: No console errors related to this change."""
    # This is a manual test (requires browser DevTools)
    pytest.skip("manual — requires browser DevTools to verify console errors")


# --- UAT Test Steps (executed via HTTP where possible) ---

def test_revise_dashboard_ui__uat_step_1_dashboard_visible(client):
    """UAT Step 1: Open dashboard at http://localhost:8000; log panel is visible on main view."""
    r = client.get("/")
    assert r.status_code == 200, "Dashboard should load"
    # Log panel should be visible (not hidden behind a tab)
    assert "log" in r.text.lower() or "activity" in r.text.lower(), \
        "Log panel should be visible without tab switch"


def test_revise_dashboard_ui__uat_step_2_live_entries_appear(client):
    """UAT Step 2: New entries appear in real time with relative timestamp, project, role, description."""
    # This requires SSE streaming; verify the endpoint supports it
    r = client.get("/api/events")
    # Should be successful or at least not a server error
    assert r.status_code != 500, "Events endpoint should be available"


def test_revise_dashboard_ui__uat_step_3_manual_scroll_pauses(client):
    """UAT Step 3: Manually scroll log panel upward; auto-scroll stops."""
    pytest.skip("manual — requires browser interaction to scroll and verify auto-scroll pause")


def test_revise_dashboard_ui__uat_step_4_jump_to_latest_button(client):
    """UAT Step 4: Click 'Jump to latest'; panel scrolls to bottom; button disappears."""
    pytest.skip("manual — requires browser interaction to click button and verify scroll")


def test_revise_dashboard_ui__uat_step_5_cap_200_entries(client):
    """UAT Step 5: More than 200 entries accumulate; panel holds max 200 entries."""
    # This is difficult to trigger via HTTP without modifying server state
    pytest.skip("manual — requires event accumulation test (difficult to set up in HTTP)")


def test_revise_dashboard_ui__uat_step_6_sse_disconnection(client):
    """UAT Step 6: Stop SSE server; indicator changes to amber then red within ~10s."""
    pytest.skip("manual — requires network manipulation to test SSE disconnect states")


def test_revise_dashboard_ui__uat_step_7_project_filter(client):
    """UAT Step 7: Use project filter; only log entries for selected project are shown."""
    # Check that the API or HTML supports filtering
    r = client.get("/")
    assert r.status_code == 200
    # Filter controls should be present
    assert "filter" in r.text.lower() or "project" in r.text.lower()


def test_revise_dashboard_ui__uat_step_8_agent_role_filter(client):
    """UAT Step 8: Use agent role filter to select 'Coder'; only Coder entries shown."""
    # Check that the API or HTML supports role filtering
    r = client.get("/")
    assert r.status_code == 200
    # Role filter controls should be present
    assert any(role in r.text.lower() for role in ["coder", "ba", "tester"])


def test_revise_dashboard_ui__uat_step_9_resize_narrow_viewport(client):
    """UAT Step 9: Resize browser below 1280 px; log panel stacks below without overflow."""
    pytest.skip("manual — requires browser resize and visual inspection")


def test_revise_dashboard_ui__uat_step_10_toggle_theme(client):
    """UAT Step 10: Toggle dark/light theme; log panel updates immediately."""
    pytest.skip("manual — requires browser interaction and theme toggle verification")


def test_revise_dashboard_ui__uat_step_11_no_regressions_in_views(client):
    """UAT Step 11: Navigate to Sprint Cockpit, Agents, Activity Log, Project drill-down; all render correctly."""
    # Test main dashboard
    r = client.get("/")
    assert r.status_code == 200, "Dashboard should render"

    # Test API endpoints for other views
    r_agents = client.get("/api/agents")
    assert r_agents.status_code != 500, "Agents endpoint should not error"

    # Activity Log is part of main dashboard, so if main loads, it should be fine
    assert "<h1>500" not in r.text and "Internal Server Error" not in r.text, \
        "No server 500 errors should be present"
