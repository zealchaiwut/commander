"""Tests for issue #11: Add manual refresh button to dashboard"""
import pytest
import httpx


BASE_URL = "http://127.0.0.1:8002"


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# --- Acceptance Criteria ---

def test_manual_refresh_button__refresh_button_visible_in_html(client):
    # AC: A visible refresh button is present on the dashboard UI at all times.
    r = client.get("/")
    assert r.status_code == 200
    html = r.text
    # Check that the refresh button element exists with id="btn-refresh"
    assert 'id="btn-refresh"' in html, "btn-refresh element not found in HTML"
    # Check it has an aria-label for accessibility
    assert 'aria-label="Refresh"' in html, "Refresh button missing aria-label"
    # Check it has a visible SVG icon (refresh icon)
    assert 'id="refresh-icon"' in html, "refresh-icon SVG not found in HTML"


def test_manual_refresh_button__fetch_without_page_reload(client):
    # AC: Clicking the refresh button triggers a fresh data fetch from the server
    # without a full page reload.
    # This is implemented via manualRefresh() calling loadProjects() + loadPlanUsage()
    # We verify the JS contains the manualRefresh function that calls fetch APIs
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    assert "manualRefresh" in js, "manualRefresh function not in app.js"
    assert "loadProjects" in js, "loadProjects not called in app.js"
    # Verify the button is wired up via addEventListener in init()
    assert "btn-refresh" in js, "btn-refresh not referenced in app.js"
    assert "addEventListener" in js, "addEventListener not used to wire up refresh"


def test_manual_refresh_button__loading_indicator_during_refresh(client):
    # AC: While the refresh is in progress, the button displays a loading indicator
    # (spinner or disabled state) to prevent duplicate requests.
    r = client.get("/")
    assert r.status_code == 200
    html = r.text
    # Check CSS animation for spinner exists
    assert "spinning" in html, "spinning CSS class not found"
    assert "@keyframes spin" in html, "spin keyframe animation not defined"

    # Check JS disables the button and adds spinner class during fetch
    r2 = client.get("/static/app.js")
    assert r2.status_code == 200
    js = r2.text
    assert "btn.disabled = true" in js, "Button not disabled during refresh"
    assert 'icon.classList.add("spinning")' in js or "classList.add('spinning')" in js, \
        "Spinning class not added to icon during refresh"


def test_manual_refresh_button__button_returns_to_normal_after_fetch(client):
    # AC: After the fetch completes (success or error), the button returns to
    # its normal clickable state within 3 seconds.
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    # Check the finally block re-enables the button
    assert "btn.disabled = false" in js, "Button not re-enabled after refresh"
    assert 'icon.classList.remove("spinning")' in js or "classList.remove('spinning')" in js, \
        "Spinning class not removed after refresh"
    # Check it uses setTimeout (within 3s) to restore state
    assert "setTimeout" in js, "setTimeout not used to restore button state"


def test_manual_refresh_button__error_message_on_fetch_failure(client):
    # AC: If the fetch fails, a non-blocking error message is displayed to the user
    # (e.g., a banner or toast) without clearing existing data.
    r = client.get("/")
    assert r.status_code == 200
    html = r.text
    # Check toast element exists in DOM
    assert 'id="toast-error"' in html, "toast-error element not found in HTML"
    # Check it is positioned fixed (non-blocking overlay)
    assert "position:fixed" in html or "position: fixed" in html, \
        "Toast is not positioned fixed (non-blocking)"

    # Check JS shows toast on error
    r2 = client.get("/static/app.js")
    assert r2.status_code == 200
    js = r2.text
    assert "showToast" in js, "showToast function not defined"
    assert "Refresh failed" in js, "Error message not found in showToast call"


def test_manual_refresh_button__api_projects_returns_data_for_refresh(client):
    # AC: Clicking the refresh button triggers a fresh data fetch from the server.
    # Verify the /api/projects endpoint (called by loadProjects) works correctly.
    r = client.get("/api/projects")
    assert r.status_code == 200
    data = r.json()
    assert "projects" in data, "/api/projects response missing 'projects' key"
    assert "metrics" in data, "/api/projects response missing 'metrics' key"


def test_manual_refresh_button__accessible_on_desktop_and_mobile(client):
    # AC: The refresh button is accessible on both desktop and mobile viewports
    # (Tailscale-accessible layout).
    pytest.skip("manual — cannot be HTTP-tested: requires browser viewport inspection")
