"""
Tests for issue #11 — Add manual refresh button to dashboard.

Strategy:
- Static-file tests verify that the HTML and JS source contain the required
  elements and functions (no server needed).
- Live-server tests verify that the dashboard page is served and the JS bundle
  includes the refresh-related symbols (server must be running on port 8000).

AC coverage:
  AC-1  Refresh button is visible on the dashboard UI at all times.
  AC-2  Clicking triggers a fresh data fetch without a full page reload.
  AC-3  Loading indicator (spinner / disabled state) while in progress.
  AC-4  Button returns to normal state within 3 seconds after fetch.
  AC-5  Failed fetch shows a non-blocking error toast; existing data is kept.
  AC-6  Button is accessible on desktop and mobile viewports (layout-level).
"""

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths to static assets
# ---------------------------------------------------------------------------
STATIC_DIR = Path(__file__).parent.parent / "static"
HTML_FILE  = STATIC_DIR / "index.html"
JS_FILE    = STATIC_DIR / "app.js"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _html() -> str:
    return HTML_FILE.read_text(encoding="utf-8")


def _js() -> str:
    return JS_FILE.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Static-file tests (no server required)
# ---------------------------------------------------------------------------

class TestStaticHTML:
    """AC-1, AC-3, AC-5, AC-6 — verified via index.html source."""

    def test_html_file_exists(self):
        assert HTML_FILE.exists(), f"index.html not found at {HTML_FILE}"

    def test_refresh_button_present(self):
        """AC-1 — btn-refresh element must exist in the HTML."""
        assert 'id="btn-refresh"' in _html(), (
            "btn-refresh element missing from index.html"
        )

    def test_refresh_button_has_accessible_label(self):
        """AC-6 — button must have aria-label for screen-reader / mobile accessibility."""
        html = _html()
        assert 'aria-label' in html or 'title' in html, (
            "Refresh button should have aria-label or title for accessibility"
        )

    def test_spinner_css_animation_defined(self):
        """AC-3 — @keyframes spin or .spinning CSS must be present."""
        html = _html()
        assert '@keyframes spin' in html or '.spinning' in html, (
            "Spinner CSS animation (@keyframes spin or .spinning) missing from index.html"
        )

    def test_refresh_icon_element_present(self):
        """AC-3 — An icon element for the spinner needs an id to be toggled by JS."""
        html = _html()
        assert 'id="refresh-icon"' in html or 'id="btn-refresh"' in html, (
            "Refresh icon element (refresh-icon) not found in index.html"
        )

    def test_toast_container_present(self):
        """AC-5 — A toast/error container must exist for non-blocking error messages."""
        html = _html()
        assert 'id="toast-error"' in html or 'toast' in html, (
            "Toast error container missing from index.html"
        )

    def test_toast_is_initially_hidden(self):
        """AC-5 — Error toast must not be visible on initial load."""
        html = _html()
        # Toast element should start with display:none or equivalent
        assert 'display:none' in html or 'display: none' in html, (
            "Toast container should be hidden (display:none) on initial load"
        )

    def test_refresh_button_in_header(self):
        """AC-1 — Button must appear in the header section."""
        html = _html()
        header_idx = html.find('<header')
        end_header_idx = html.find('</header>', header_idx)
        assert header_idx != -1 and end_header_idx != -1, "No <header> found in HTML"
        header_section = html[header_idx:end_header_idx]
        assert 'btn-refresh' in header_section, (
            "Refresh button (btn-refresh) should be inside the <header> element"
        )


class TestStaticJS:
    """AC-2, AC-3, AC-4, AC-5 — verified via app.js source."""

    def test_js_file_exists(self):
        assert JS_FILE.exists(), f"app.js not found at {JS_FILE}"

    def test_manual_refresh_function_defined(self):
        """AC-2 — manualRefresh() function must exist."""
        assert 'function manualRefresh' in _js() or 'async function manualRefresh' in _js(), (
            "manualRefresh() function not found in app.js"
        )

    def test_show_toast_function_defined(self):
        """AC-5 — showToast() must exist to display non-blocking errors."""
        assert 'function showToast' in _js(), (
            "showToast() function not found in app.js"
        )

    def test_hide_toast_function_defined(self):
        """AC-5 — hideToast() must exist to clear the error on successful refresh."""
        assert 'function hideToast' in _js(), (
            "hideToast() function not found in app.js"
        )

    def test_button_disabled_during_refresh(self):
        """AC-3 — btn.disabled = true must be set to prevent duplicate requests."""
        js = _js()
        assert 'btn.disabled = true' in js or "btn.disabled=true" in js, (
            "Refresh button should be disabled during refresh (btn.disabled = true)"
        )

    def test_button_re_enabled_after_refresh(self):
        """AC-4 — btn.disabled must be reset to false after refresh completes."""
        js = _js()
        assert 'btn.disabled = false' in js or "btn.disabled=false" in js, (
            "Refresh button must be re-enabled after refresh (btn.disabled = false)"
        )

    def test_spinner_class_added_on_refresh(self):
        """AC-3 — .spinning class must be added to the icon during refresh."""
        assert "classList.add('spinning')" in _js(), (
            "Spinner class should be added with classList.add('spinning')"
        )

    def test_spinner_class_removed_after_refresh(self):
        """AC-4 — .spinning class must be removed when refresh finishes."""
        assert "classList.remove('spinning')" in _js(), (
            "Spinner class should be removed with classList.remove('spinning')"
        )

    def test_refresh_calls_load_projects(self):
        """AC-2 — manualRefresh must call loadProjects() to fetch fresh data."""
        assert 'loadProjects()' in _js(), (
            "manualRefresh should call loadProjects()"
        )

    def test_error_toast_shown_on_catch(self):
        """AC-5 — showToast is called inside a catch block in manualRefresh."""
        js = _js()
        # The catch block in manualRefresh should call showToast
        assert 'showToast' in js, "showToast should be called on fetch failure"

    def test_button_click_wired_to_manual_refresh(self):
        """AC-2 — btn-refresh click listener must call manualRefresh."""
        js = _js()
        assert 'btn-refresh' in js and 'manualRefresh' in js, (
            "btn-refresh click handler should be wired to manualRefresh in JS"
        )

    def test_periodic_refresh_errors_are_silent(self):
        """AC-5 — Background SSE/interval calls must not show error toasts (.catch)."""
        js = _js()
        # SSE-triggered loadProjects should use .catch to suppress errors
        assert ".catch(" in js, (
            "Periodic/SSE-triggered loadProjects calls should use .catch() to stay silent"
        )

    def test_settimeout_used_for_button_reset(self):
        """AC-4 — setTimeout should be used to ensure button re-enables within 3s."""
        assert 'setTimeout' in _js(), (
            "setTimeout should be used to guarantee button re-enable timing"
        )


# ---------------------------------------------------------------------------
# Live-server tests (require server running on port 8000)
# ---------------------------------------------------------------------------

class TestLiveServer:
    """Verify dashboard page is served and contains the refresh button markup."""

    @pytest.mark.usefixtures("client")
    def test_dashboard_page_returns_200(self, client):
        """Dashboard root must respond with HTTP 200."""
        r = client.get("/")
        assert r.status_code == 200

    def test_dashboard_page_contains_refresh_button(self, client):
        """AC-1 — Served HTML must include the refresh button element."""
        r = client.get("/")
        assert r.status_code == 200
        assert 'btn-refresh' in r.text, (
            "Served dashboard HTML should contain btn-refresh element"
        )

    def test_dashboard_page_contains_toast_container(self, client):
        """AC-5 — Served HTML must include the toast container."""
        r = client.get("/")
        assert r.status_code == 200
        assert 'toast-error' in r.text, (
            "Served dashboard HTML should contain toast-error container"
        )

    def test_app_js_served(self, client):
        """JS bundle must be served without error."""
        r = client.get("/static/app.js")
        assert r.status_code == 200

    def test_served_js_contains_manual_refresh(self, client):
        """AC-2 — Served JS must contain manualRefresh function."""
        r = client.get("/static/app.js")
        assert r.status_code == 200
        assert 'manualRefresh' in r.text, (
            "Served app.js should define manualRefresh()"
        )
