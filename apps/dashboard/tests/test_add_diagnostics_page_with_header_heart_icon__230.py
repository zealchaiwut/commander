"""Tests for issue #230: Add diagnostics page with header heart icon."""
import re
import os
import pathlib
import pytest

BASE_URL = os.environ.get("TEST_BASE_URL", "http://localhost:8000")
STATIC = pathlib.Path(__file__).parent.parent / "static"
HOME_HTML   = STATIC / "home-preview.html"
DIAG_HTML   = STATIC / "diagnostics.html"

HOME_SRC  = HOME_HTML.read_text()
DIAG_SRC  = DIAG_HTML.read_text()


# ── AC: Header heartbeat icon ─────────────────────────────────────────────────

def test_header_has_heartbeat_icon():
    """Header contains ti-heartbeat icon."""
    assert "ti-heartbeat" in HOME_SRC, "ti-heartbeat icon missing from home-preview.html header"


def test_header_heartbeat_has_diagnostics_title():
    """Heartbeat icon button has title='Diagnostics'."""
    assert 'title="Diagnostics"' in HOME_SRC, \
        "heartbeat button missing title='Diagnostics' in home-preview.html"


def test_header_heartbeat_links_to_diagnostics():
    """Heartbeat icon button navigates to /diagnostics."""
    assert "/diagnostics" in HOME_SRC, \
        "home-preview.html does not reference /diagnostics"


def test_header_heartbeat_positioned_between_bulk_create_and_settings():
    """Heartbeat icon appears between Bulk Create and Settings icons in the header."""
    # Locate positions of relevant icons in the header right section
    bulk_pos = HOME_SRC.find("ti-list-plus")
    heart_pos = HOME_SRC.find("ti-heartbeat")
    settings_pos = HOME_SRC.find("ti-settings")
    assert bulk_pos != -1, "ti-list-plus (Bulk Create) icon not found"
    assert heart_pos != -1, "ti-heartbeat icon not found"
    assert settings_pos != -1, "ti-settings icon not found"
    assert bulk_pos < heart_pos < settings_pos, \
        f"heartbeat icon not positioned between bulk-create and settings (positions: {bulk_pos}, {heart_pos}, {settings_pos})"


def test_health_icon_pulsedot_animation_defined():
    """pulseDot keyframe animation is defined for the health icon."""
    assert "pulseDot" in HOME_SRC, "pulseDot keyframe missing from home-preview.html"


def test_health_icon_down_class_uses_pulsedot():
    """health-down class applies pulseDot animation to the icon."""
    assert "health-down" in HOME_SRC, "health-down CSS class missing from home-preview.html"
    # The class should set animation
    idx = HOME_SRC.find("health-down")
    snippet = HOME_SRC[idx:idx+200]
    assert "pulseDot" in snippet, \
        "health-down class does not reference pulseDot animation in home-preview.html"


# ── AC: /diagnostics route ───────────────────────────────────────────────────

def test_diagnostics_route_returns_200(client):
    """GET /diagnostics returns HTTP 200."""
    r = client.get("/diagnostics")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"


def test_diagnostics_route_returns_html(client):
    """GET /diagnostics returns Content-Type text/html."""
    r = client.get("/diagnostics")
    assert "text/html" in r.headers.get("content-type", ""), \
        f"Expected text/html, got: {r.headers.get('content-type')}"


# ── AC: Page title / heading ──────────────────────────────────────────────────

def test_diagnostics_page_title():
    """diagnostics.html <title> contains 'System diagnostics'."""
    assert "System diagnostics" in DIAG_SRC, \
        "Page <title> or heading does not contain 'System diagnostics'"


def test_diagnostics_page_heading():
    """diagnostics.html <h1> contains 'System diagnostics'."""
    match = re.search(r'<h1[^>]*>([^<]*)</h1>', DIAG_SRC)
    assert match, "No <h1> found in diagnostics.html"
    assert "System diagnostics" in match.group(1), \
        f"<h1> text is '{match.group(1)}', expected 'System diagnostics'"


def test_diagnostics_renders_in_app_shell():
    """diagnostics.html has a header element (app shell)."""
    assert "<header" in DIAG_SRC, "No <header> found in diagnostics.html — app shell missing"


# ── AC: Status banner ─────────────────────────────────────────────────────────

def test_status_banner_exists():
    """Status banner element is present."""
    assert "status-banner" in DIAG_SRC, "status-banner element missing from diagnostics.html"


def test_status_banner_ok_class_defined():
    """banner-ok CSS class is defined."""
    assert "banner-ok" in DIAG_SRC, "banner-ok CSS class not defined"


def test_status_banner_degraded_class_defined():
    """banner-degraded CSS class is defined."""
    assert "banner-degraded" in DIAG_SRC, "banner-degraded CSS class not defined"


def test_status_banner_down_class_defined():
    """banner-down CSS class is defined."""
    assert "banner-down" in DIAG_SRC, "banner-down CSS class not defined"


def test_status_banner_ok_text():
    """'All systems operational' label text is present."""
    assert "All systems operational" in DIAG_SRC, \
        "Banner label 'All systems operational' missing from diagnostics.html"


def test_status_banner_degraded_text():
    """'Degraded — some checks warning' label text is present."""
    assert "Degraded" in DIAG_SRC and "some checks warning" in DIAG_SRC, \
        "Degraded banner label text missing from diagnostics.html"


def test_status_banner_down_text():
    """'System down — immediate attention needed' label text is present."""
    assert "System down" in DIAG_SRC and "immediate attention needed" in DIAG_SRC, \
        "Down banner label text missing from diagnostics.html"


def test_status_banner_timestamp_element():
    """Banner has a timestamp element for 'Updated N seconds ago'."""
    assert "banner-timestamp" in DIAG_SRC, \
        "banner-timestamp element missing from diagnostics.html"


def test_status_banner_refresh_button():
    """Manual Refresh button is present in the banner."""
    assert "Refresh" in DIAG_SRC, "Refresh button text missing from diagnostics.html"
    assert "refresh-btn" in DIAG_SRC or "manualRefresh" in DIAG_SRC, \
        "refresh-btn or manualRefresh function missing from diagnostics.html"


# ── AC: Check cards grid ──────────────────────────────────────────────────────

def test_check_cards_grid_exists():
    """cards-grid element is present."""
    assert "cards-grid" in DIAG_SRC, "cards-grid element missing from diagnostics.html"


def test_six_canonical_checks_in_order():
    """JS renders the 6 canonical checks in order."""
    expected = ['dashboard', 'database', 'github_auth', 'claude_code_auth', 'disk', 'stuck_sprints']
    # Look for ORDER array in the JS
    order_match = re.search(r"ORDER\s*=\s*\[([^\]]+)\]", DIAG_SRC)
    assert order_match, "ORDER array not found in diagnostics.html JS"
    order_str = order_match.group(1)
    for name in expected:
        assert name in order_str, f"'{name}' missing from ORDER array in diagnostics.html"


def test_responsive_grid_1col_mobile():
    """Cards grid uses 1 column on mobile (grid-template-columns: 1fr)."""
    assert "1fr" in DIAG_SRC, "1-column mobile layout missing from diagnostics.html"


def test_responsive_grid_2col_tablet():
    """Cards grid uses 2 columns on tablet (min-width: 768px)."""
    assert "768px" in DIAG_SRC, "768px tablet breakpoint missing from diagnostics.html"
    assert "repeat(2, 1fr)" in DIAG_SRC, "2-column tablet layout missing from diagnostics.html"


def test_responsive_grid_3col_desktop():
    """Cards grid uses 3 columns on desktop (min-width: 1024px)."""
    assert "1024px" in DIAG_SRC, "1024px desktop breakpoint missing from diagnostics.html"
    assert "repeat(3, 1fr)" in DIAG_SRC, "3-column desktop layout missing from diagnostics.html"


# ── AC: Check card contents ───────────────────────────────────────────────────

def test_check_display_names_defined():
    """Check display names are defined in JS."""
    assert "CHECK_DISPLAY_NAMES" in DIAG_SRC, \
        "CHECK_DISPLAY_NAMES map missing from diagnostics.html"


def test_check_card_status_indicator_symbols():
    """Status indicator symbols (✓, ⚠, ✕) are defined in JS."""
    assert "getStatusIndicator" in DIAG_SRC, \
        "getStatusIndicator function missing from diagnostics.html"
    assert "'ok'" in DIAG_SRC, "ok status not handled in diagnostics.html"


# ── AC: Recovery hints ────────────────────────────────────────────────────────

def test_recovery_hint_github_auth():
    """github_auth recovery hint contains 'gh auth login'."""
    assert "gh auth login" in DIAG_SRC, \
        "github_auth recovery hint ('gh auth login') missing from diagnostics.html"


def test_recovery_hint_disk():
    """disk recovery hint contains 'du -sh'."""
    assert "du -sh" in DIAG_SRC, \
        "disk recovery hint ('du -sh') missing from diagnostics.html"


def test_recovery_hint_stuck_sprints():
    """stuck_sprints recovery hint references stuck sprint hours."""
    assert "stuck" in DIAG_SRC and "hours" in DIAG_SRC, \
        "stuck_sprints recovery hint missing 'stuck'/'hours' from diagnostics.html"


def test_recovery_hints_hardcoded():
    """Recovery hints are hardcoded in getRecoveryHint function (not from API)."""
    assert "getRecoveryHint" in DIAG_SRC, \
        "getRecoveryHint function missing from diagnostics.html"
    assert "hardcoded" in DIAG_SRC.lower() or "case 'github_auth'" in DIAG_SRC or "switch" in DIAG_SRC, \
        "Hardcoded recovery hints switch/case not found in diagnostics.html"


# ── AC: Auto-refresh ──────────────────────────────────────────────────────────

def test_auto_refresh_30_seconds():
    """Auto-refresh interval is 30 seconds (30000 ms)."""
    assert "30000" in DIAG_SRC, \
        "30-second auto-refresh interval (30000) missing from diagnostics.html"


def test_auto_refresh_uses_setinterval():
    """startAutoRefresh uses setInterval."""
    assert "setInterval" in DIAG_SRC and "fetchHealth" in DIAG_SRC, \
        "setInterval/fetchHealth auto-refresh not found in diagnostics.html"


# ── AC: Debounce on manual refresh ───────────────────────────────────────────

def test_manual_refresh_debounce():
    """Refresh button is debounced (1 second)."""
    assert "_debounceTimer" in DIAG_SRC or "debounce" in DIAG_SRC.lower(), \
        "Debounce timer not found in diagnostics.html"
    assert "1000" in DIAG_SRC, "1-second debounce (1000) missing from diagnostics.html"


# ── AC: Unreachable banner ────────────────────────────────────────────────────

def test_unreachable_banner_exists():
    """Unreachable banner element is present."""
    assert "unreachable-banner" in DIAG_SRC, \
        "unreachable-banner element missing from diagnostics.html"


def test_unreachable_banner_text():
    """Unreachable banner shows correct message."""
    assert "Couldn't reach health endpoint" in DIAG_SRC or "reach health endpoint" in DIAG_SRC, \
        "Unreachable banner message missing from diagnostics.html"


def test_unreachable_banner_hidden_by_default():
    """Unreachable banner is hidden by default."""
    # The element should have 'hidden' class
    match = re.search(r'id="unreachable-banner"[^>]*class="([^"]*)"', DIAG_SRC)
    if not match:
        match = re.search(r'class="([^"]*)"[^>]*id="unreachable-banner"', DIAG_SRC)
    assert match, "unreachable-banner element not found with class attribute"
    assert "hidden" in match.group(1), \
        "unreachable-banner is not hidden by default"


# ── AC: Touch-friendly (iPad / Tailscale) ─────────────────────────────────────

def test_touch_friendly_min_44px_buttons():
    """Button tap targets are at least 44 × 44 px."""
    assert "44px" in DIAG_SRC, \
        "44px minimum tap target not set in diagnostics.html"


def test_touch_friendly_btn_icon_class():
    """btn-icon class has min-width and min-height set to 44px."""
    # Check that .btn-icon has at least min-width: 44px
    idx = DIAG_SRC.find(".btn-icon")
    assert idx != -1, ".btn-icon class not found in diagnostics.html"
    snippet = DIAG_SRC[idx:idx+200]
    assert "44px" in snippet, ".btn-icon min-width/height 44px not found in diagnostics.html"


# ── AC: Debug section ────────────────────────────────────────────────────────

def test_debug_section_exists():
    """Debug section is present."""
    assert "debug-section" in DIAG_SRC or "debug-toggle" in DIAG_SRC, \
        "Debug section missing from diagnostics.html"


def test_debug_section_collapsed_by_default():
    """Debug body is collapsed (hidden) by default."""
    # debug-body should NOT have 'open' class by default in the HTML
    match = re.search(r'id="debug-body"[^>]*class="([^"]*)"', DIAG_SRC)
    if not match:
        match = re.search(r'class="([^"]*)"[^>]*id="debug-body"', DIAG_SRC)
    assert match, "debug-body element not found"
    assert "open" not in match.group(1), \
        "debug-body has 'open' class by default — should be collapsed"


def test_debug_toggle_aria_expanded_false():
    """Debug toggle has aria-expanded='false' by default."""
    assert 'aria-expanded="false"' in DIAG_SRC, \
        "debug-toggle missing aria-expanded='false' in diagnostics.html"


def test_debug_copy_json_button():
    """Copy Raw /api/health JSON button exists."""
    assert "copy-json" in DIAG_SRC or "copyDebugJson" in DIAG_SRC, \
        "Copy JSON button missing from diagnostics.html"


def test_debug_uptime_row():
    """Dashboard uptime row is present in debug section."""
    assert "uptime" in DIAG_SRC.lower(), \
        "uptime row missing from debug section in diagnostics.html"


def test_debug_hostname_version_row():
    """Mac hostname + commander version row is in debug section."""
    assert "hostname" in DIAG_SRC.lower() or "host" in DIAG_SRC.lower(), \
        "hostname/host row missing from debug section in diagnostics.html"


# ── AC: diagnostics.html pulseDot for down status ────────────────────────────

def test_diagnostics_header_health_icon_pulsedot():
    """diagnostics.html defines pulseDot animation for header health icon."""
    assert "pulseDot" in DIAG_SRC, "pulseDot keyframe missing from diagnostics.html"
    assert "health-down" in DIAG_SRC, "health-down class missing from diagnostics.html"


def test_diagnostics_header_icon_green_for_ok():
    """applyHeaderHealthIcon sets green color for ok status."""
    assert "var(--green)" in DIAG_SRC, \
        "Green color for ok status missing from diagnostics.html"


def test_diagnostics_header_icon_amber_for_degraded():
    """applyHeaderHealthIcon sets amber color for degraded status."""
    assert "var(--amber)" in DIAG_SRC, \
        "Amber color for degraded status missing from diagnostics.html"


def test_diagnostics_header_icon_red_for_down():
    """applyHeaderHealthIcon sets red color for down status, adds pulseDot."""
    assert "var(--red)" in DIAG_SRC, \
        "Red color for down status missing from diagnostics.html"
    assert "health-down" in DIAG_SRC, \
        "health-down class (pulseDot) not added for down status in diagnostics.html"
