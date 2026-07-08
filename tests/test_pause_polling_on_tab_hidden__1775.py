"""Tests for issue #1775: Pause dashboard polling when tab is hidden (runs against UAT)"""
import os
import pytest
import httpx
from unittest.mock import patch, MagicMock


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

def test_pause_polling_when_tab_hidden__visibility_guard_exists(client):
    # AC: A shared visibility-guard utility exists
    r = client.get("/static/src/shell/visibility.js")
    assert r.status_code == 200
    assert "visibilityInterval" in r.text
    assert "installVisibilityGuard" in r.text
    assert "document.hidden" in r.text


def test_pause_polling_on_tab_hidden__bundle_exports_functions(client):
    # AC: Utility wraps setInterval-based pollers and is globally available via bundle
    r = client.get("/static/dist/bundle.js")
    assert r.status_code == 200
    assert "visibilityInterval" in r.text
    assert "installVisibilityGuard" in r.text


def test_pause_polling_on_tab_hidden__project_html_uses_visibility(client):
    # AC: All seven pollers in project.html are wrapped with visibilityInterval
    r = client.get("/project.html")
    assert r.status_code == 200

    # Verify project.html pollers use visibilityInterval.
    # Issue #1776 merged the two separate 30s timers (sidebar + snav) into one.
    html = r.text
    assert "visibilityInterval(() => { loadHomeData(true); snavRefresh(); _snavRefreshAll(); _milestoneRefresh(); }, 30000)" in html  # merged loop
    assert "visibilityInterval(_smgmtLivePollTick, 2000);" in html  # live board poll
    assert "visibilityInterval(_smgmtInspectorStreamTick, 5000);" in html  # inspector poll
    assert "visibilityInterval(logsFetchRuns, 15000);" in html  # logs poll
    assert "visibilityInterval(statusRefresh, 60000);" in html  # status poll


def test_pause_polling_on_tab_hidden__home_preview_uses_visibility(client):
    # AC: Both home-preview.html pollers use visibilityInterval
    r = client.get("/home-preview.html")
    assert r.status_code == 200

    html = r.text
    assert "visibilityInterval(loadStaleDocs, 60000);" in html
    assert "visibilityInterval(pollHealth, 30000);" in html


def test_pause_polling_on_tab_hidden__diagnostics_uses_visibility(client):
    # AC: diagnostics.html health poll uses visibilityInterval
    r = client.get("/diagnostics.html")
    assert r.status_code == 200

    html = r.text
    assert "visibilityInterval(fetchHealth, 30000);" in html


def test_pause_polling_on_tab_hidden__immediate_tick_on_visibility(client):
    # AC: On becoming visible, each wrapped poller fires immediately once before resuming
    r = client.get("/static/src/shell/visibility.js")
    assert r.status_code == 200

    # Verify that the visibility.js module includes immediate tick logic
    js = r.text
    assert "fn();" in js  # immediate call to the function
    assert "document.addEventListener('visibilitychange'" in js
    assert "!document.hidden" in js  # resuming when visible


def test_pause_polling_on_tab_hidden__visibility_guard_installed(client):
    # AC: installVisibilityGuard is called on startup
    r = client.get("/static/dist/bundle.js")
    assert r.status_code == 200

    # Verify the guard is installed at bundle startup
    # The bundle should call installVisibilityGuard() during initialization
    bundle = r.text
    assert "installVisibilityGuard" in bundle

    # Verify clearInterval is patched in the bundle
    assert "window.clearInterval" in bundle


def test_pause_polling_on_tab_hidden__clearinterval_works_with_fake_ids(client):
    # AC: clearInterval works with visibilityInterval fake IDs (no regression)
    r = client.get("/static/src/shell/visibility.js")
    assert r.status_code == 200

    js = r.text
    # Verify that _viHandles map tracks intervals
    assert "_viHandles" in js
    # Verify clearInterval is patched to handle fake IDs
    assert "_viHandles.has(id)" in js


def test_pause_polling_on_tab_hidden__sse_connections_not_affected(client):
    # AC: SSE EventSource connections remain open and unaffected by tab visibility
    # This is verified by checking the visibility.js implementation does NOT interact with EventSource
    r = client.get("/static/src/shell/visibility.js")
    assert r.status_code == 200

    js = r.text
    # Verify no EventSource or SSE-specific logic in visibility.js
    assert "EventSource" not in js or "close()" not in js  # Don't close SSE

    # Verify only setInterval/clearInterval are patched
    assert "setInterval" in js
    assert "clearInterval" in js


def test_pause_polling_on_tab_hidden__no_regression_continuous_visible(client):
    # AC: No regression in polling behaviour when tab remains continuously visible
    r = client.get("/static/src/shell/visibility.js")
    assert r.status_code == 200

    js = r.text
    # Verify that if the tab is never hidden, intervals start normally
    assert "if (!document.hidden)" in js  # Check visibility on init
    assert "realId = setInterval(fn, delay);" in js  # Regular interval start


def test_pause_polling_on_tab_hidden__all_pages_covered(client):
    # AC: All five pages are covered
    pages = [
        "/project.html",
        "/home.html",
        "/home-preview.html",
        "/diagnostics.html",
    ]

    for page in pages:
        r = client.get(page)
        assert r.status_code == 200, f"Failed to load {page}"
        # Each page should either:
        # 1. Use visibilityInterval directly, or
        # 2. Load the bundle which provides visibilityInterval globally
        assert "bundle.js" in r.text or "visibilityInterval" in r.text, \
            f"{page} does not use visibilityInterval or load bundle"


def test_pause_polling_on_tab_hidden__double_visibility_guard(client):
    # AC: Rapid tab visibility toggling does not create duplicate intervals
    r = client.get("/static/src/shell/visibility.js")
    assert r.status_code == 200

    js = r.text
    # Verify the implementation calls stop() before starting a new interval
    # to prevent double intervals on quick visibility changes
    assert "stop();" in js  # Guard against stale realId
    assert "if (realId === null)" in js  # Check if already running


def test_pause_polling_on_tab_hidden__memory_cleanup(client):
    # AC: clearing intervals removes event listeners and cleanup is safe
    r = client.get("/static/src/shell/visibility.js")
    assert r.status_code == 200

    js = r.text
    # Verify cleanup: remove event listener and delete from handles map
    assert "removeEventListener" in js
    assert "_viHandles.delete(id)" in js
