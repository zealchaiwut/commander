"""
Tests for issue #145 — Fix refresh button to trigger full browser reload.

AC coverage:
  AC-1  Clicking #btn-refresh triggers window.location.reload().
  AC-2  manualRefresh() is no longer called from the btn-refresh click handler.
  AC-3  Full page reload guarantees fresh state after label/status changes.
        (Verified by confirming window.location.reload() is wired as the handler.)
  AC-4  Refresh button icon and tooltip remain visually unchanged.
"""

import re
from pathlib import Path

STATIC_DIR = Path(__file__).parent.parent / "static"
HTML_FILE  = STATIC_DIR / "index.html"
JS_FILE    = STATIC_DIR / "app.js"


def _html() -> str:
    return HTML_FILE.read_text(encoding="utf-8")


def _js() -> str:
    return JS_FILE.read_text(encoding="utf-8")


class TestRefreshButtonUsesFullReload:
    """AC-1, AC-2, AC-3 — verified via app.js source."""

    def test_window_location_reload_present_in_js(self):
        """AC-1 — window.location.reload() must appear in app.js."""
        assert "window.location.reload()" in _js(), (
            "window.location.reload() not found in app.js"
        )

    def test_btn_refresh_click_handler_calls_window_reload(self):
        """AC-1 — The btn-refresh addEventListener line must call window.location.reload()."""
        js = _js()
        lines = [l for l in js.splitlines() if "btn-refresh" in l and "addEventListener" in l]
        assert len(lines) == 1, (
            f"Expected exactly 1 btn-refresh addEventListener line, got {len(lines)}: {lines}"
        )
        assert "window.location.reload()" in lines[0], (
            f"btn-refresh click handler does not call window.location.reload(): {lines[0]}"
        )

    def test_btn_refresh_click_handler_does_not_call_manual_refresh(self):
        """AC-2 — manualRefresh must not be referenced as the btn-refresh click callback."""
        js = _js()
        pattern = r"getElementById\(['\"]btn-refresh['\"]\).*addEventListener\(['\"]click['\"]\s*,\s*manualRefresh"
        assert not re.search(pattern, js), (
            "btn-refresh click handler is still wired to manualRefresh in app.js"
        )

    def test_manual_refresh_function_still_defined(self):
        """AC-2 (out-of-scope guard) — manualRefresh() must not be removed."""
        js = _js()
        assert "function manualRefresh" in js, (
            "manualRefresh() function was removed from app.js; it must be preserved"
        )

    def test_full_reload_is_the_refresh_mechanism(self):
        """AC-3 — window.location.reload() is the guaranteed mechanism for a fresh state view."""
        js = _js()
        # Confirm that the wiring uses an arrow function wrapping window.location.reload
        # rather than any other fetch-based approach.
        assert re.search(r"addEventListener\(['\"]click['\"]\s*,\s*\(\s*\)\s*=>\s*window\.location\.reload\(\)", js), (
            "btn-refresh click handler should be an arrow function calling window.location.reload()"
        )


class TestRefreshButtonVisualsUnchanged:
    """AC-4 — Button icon and tooltip verified via index.html source."""

    def test_refresh_button_tooltip_unchanged(self):
        """AC-4 — title attribute must remain 'Refresh dashboard'."""
        assert 'title="Refresh dashboard"' in _html(), (
            "btn-refresh tooltip title='Refresh dashboard' has been changed or removed"
        )

    def test_refresh_icon_element_present(self):
        """AC-4 — SVG icon with id='refresh-icon' must still be in the HTML."""
        assert 'id="refresh-icon"' in _html(), (
            "refresh-icon SVG element is missing from index.html"
        )

    def test_refresh_button_aria_label_unchanged(self):
        """AC-4 — aria-label must remain 'Refresh' for screen-reader accessibility."""
        assert 'aria-label="Refresh"' in _html(), (
            "btn-refresh aria-label='Refresh' has been changed or removed"
        )

    def test_refresh_icon_circular_arrow_svg_unchanged(self):
        """AC-4 — The circular-arrow SVG path data must be unchanged."""
        html = _html()
        assert "M13.5 2.5A7 7 0 1 0 14.5 9" in html, (
            "Circular-arrow SVG path in refresh icon has changed"
        )
        assert 'points="14.5 2 14.5 6 10.5 6"' in html, (
            "Circular-arrow polyline in refresh icon has changed"
        )
