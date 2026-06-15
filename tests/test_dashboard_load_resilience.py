"""Tests for dashboard cold-load resilience when API is unreachable."""
from pathlib import Path

PROJECT_HTML = (
    Path(__file__).resolve().parents[1]
    / "apps"
    / "dashboard"
    / "static"
    / "project.html"
)


def _html() -> str:
    return PROJECT_HTML.read_text(encoding="utf-8")


def test_unreachable_banner_present():
    """Cold load shows a fixed banner instead of eternal Loading… defaults."""
    html = _html()
    assert 'id="dashboard-unreachable-banner"' in html
    assert "dashboard-unreachable-msg" in html


def test_prefetch_surfaces_error_instead_of_silent_catch():
    """_prefetchFullRepo must call _renderDashboardLoadError on failure."""
    html = _html()
    assert "_renderDashboardLoadError" in html
    assert "_prefetchFullRepo" in html
    assert "Can't reach dashboard — is the server running?" in html
    prefetch_block = html.split("async function _prefetchFullRepo")[1].split("async function init")[0]
    assert "catch (e) { /* ignore */ }" not in prefetch_block


def test_init_starts_fetch_before_switch_tab():
    """API prefetch must start before guarded switchTab so bundle gaps don't block load."""
    init_block = _html().split("async function init()")[1].split("// ── Sprint Mgmt toolbar")[0]
    fetch_pos = init_block.index("const homePromise = loadHomeData(false)")
    switch_pos = init_block.index("if (typeof switchTab === 'function') switchTab(tab, false)")
    assert fetch_pos < switch_pos, "loadHomeData must start before switchTab"


def test_health_poll_retry_helper_present():
    """Auto-reconnect polls /api/health then retries load."""
    html = _html()
    assert "_pollHealthThenRetry" in html
    assert "_retryDashboardLoad" in html
    assert "fetch('/api/health', { cache: 'no-store' })" in html


def test_load_home_data_updates_header_on_failure():
    """Home fetch failure must not leave proj-header-name stuck on Loading… only."""
    html = _html()
    assert "Dashboard unreachable" in html
    assert "_renderDashboardLoadError('Home data unavailable" in html
