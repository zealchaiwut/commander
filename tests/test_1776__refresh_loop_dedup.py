"""
Tests for issue #1776: Deduplicate 30s refresh loops and remove dead running-all fetch.

AC1: loadHomeData no longer fetches /api/sprints/running-all
AC2: Exactly one setInterval (30s) for the main refresh loop
AC3: _snavRefreshAll() not called inside loadHomeData (fires exactly once per 30s)
AC4: /api/sprint-nav-status deduplicated via shared client cache
     (behavioral cache tests are in tests/frontend/snav-cache.test.mjs)
AC5: SSE handler calls loadHomeData on sprint_finished and sprint_reconciled
AC6: Structural verification that the merged timer bundles all previous work
"""
from __future__ import annotations

import re
from pathlib import Path

PROJECT_HTML = (
    Path(__file__).parent.parent / "apps" / "dashboard" / "static" / "project.html"
)


def _html() -> str:
    return PROJECT_HTML.read_text(encoding="utf-8")


def _extract_function_body(html: str, fn_name: str) -> str | None:
    """Return the JS body (including braces) of the named top-level function."""
    pattern = rf'(?:async\s+)?function\s+{re.escape(fn_name)}\s*\('
    m = re.search(pattern, html)
    if not m:
        return None
    brace_start = html.index("{", m.start())
    depth = 0
    pos = brace_start
    while pos < len(html):
        if html[pos] == "{":
            depth += 1
        elif html[pos] == "}":
            depth -= 1
            if depth == 0:
                return html[brace_start : pos + 1]
        pos += 1
    return None


# ── AC1 ───────────────────────────────────────────────────────────────────────

def test_loadHomeData_does_not_fetch_running_all():
    """AC1: loadHomeData must not fetch /api/sprints/running-all."""
    body = _extract_function_body(_html(), "loadHomeData")
    assert body is not None, "loadHomeData function not found in project.html"
    assert "/api/sprints/running-all" not in body, (
        "loadHomeData must not call /api/sprints/running-all "
        "(it was a dead fetch whose response was never used)"
    )


def test_running_all_destructure_removed():
    """AC1: runningResp destructure is removed from loadHomeData."""
    body = _extract_function_body(_html(), "loadHomeData")
    assert body is not None
    assert "runningResp" not in body, (
        "runningResp destructure must be removed — "
        "the variable was never read after the fetch"
    )


# ── AC2 ───────────────────────────────────────────────────────────────────────

def test_exactly_one_30s_refresh_timer_for_main_loop():
    """AC2: Only one visibilityInterval(..., 30000) wires the main data refresh loop."""
    html = _html()
    # The merged timer must call all four functions
    pattern = (
        r"visibilityInterval\s*\(\s*\(\)\s*=>\s*\{[^}]*"
        r"loadHomeData[^}]*snavRefresh[^}]*_snavRefreshAll[^}]*_milestoneRefresh"
        r"[^}]*\}\s*,\s*30000\s*\)"
    )
    matches = re.findall(pattern, html, re.DOTALL)
    assert len(matches) == 1, (
        f"Expected exactly one merged 30s refresh timer containing all four refresh calls, "
        f"found {len(matches)}"
    )


def test_startSidebarRefresh_no_longer_called():
    """AC2: startSidebarRefresh() call site removed (timers are now merged)."""
    html = _html()
    assert "startSidebarRefresh();" not in html, (
        "startSidebarRefresh() must not be called — "
        "it created the redundant second 30s timer"
    )


def test_split_timers_removed():
    """AC2: The two old separate timers do not coexist with the merged one."""
    html = _html()
    # Old Timer B: { snavRefresh(); _snavRefreshAll(); _milestoneRefresh(); }  (no loadHomeData)
    old_timer_b = "visibilityInterval(() => { snavRefresh(); _snavRefreshAll(); _milestoneRefresh(); }, 30000);"
    assert old_timer_b not in html, (
        "Old separate Timer B must be removed — its work is now inside the merged timer"
    )


# ── AC3 ───────────────────────────────────────────────────────────────────────

def test_snavRefreshAll_not_inside_loadHomeData():
    """AC3: _snavRefreshAll is not called inside loadHomeData (fires only once per tick)."""
    body = _extract_function_body(_html(), "loadHomeData")
    assert body is not None
    assert "_snavRefreshAll" not in body, (
        "_snavRefreshAll must not be called inside loadHomeData; "
        "it belongs only in the merged 30s timer callback"
    )


# ── AC4 ───────────────────────────────────────────────────────────────────────

def test_snav_nav_status_cache_defined_in_html():
    """AC4: Client-side cache for /api/sprint-nav-status is defined in project.html."""
    html = _html()
    assert "snavNavStatusFetch" in html, (
        "snavNavStatusFetch cache helper must appear in project.html "
        "(used by snavRefresh and statusRefresh to deduplicate requests)"
    )


def test_snavRefresh_uses_cache_not_bare_fetch():
    """AC4: snavRefresh uses snavNavStatusFetch (not a bare fetch) for sprint-nav-status."""
    body = _extract_function_body(_html(), "snavRefresh")
    assert body is not None, "snavRefresh function not found"
    assert "snavNavStatusFetch" in body, (
        "snavRefresh must call snavNavStatusFetch to go through the shared cache"
    )
    # Must not use bare fetch for sprint-nav-status so the cache isn't bypassed
    bare_fetch = re.search(
        r"\bfetch\s*\(\s*['\"`]/api/sprint-nav-status", body
    )
    assert bare_fetch is None, (
        "snavRefresh must not call bare fetch('/api/sprint-nav-status') — "
        "use snavNavStatusFetch to avoid duplicating the request"
    )


def test_statusRefresh_uses_cache_not_bare_fetch():
    """AC4: statusRefresh uses snavNavStatusFetch for sprint-nav-status."""
    body = _extract_function_body(_html(), "statusRefresh")
    assert body is not None, "statusRefresh function not found"
    assert "snavNavStatusFetch" in body, (
        "statusRefresh must call snavNavStatusFetch to share the cache with snavRefresh"
    )
    bare_fetch = re.search(
        r"\bfetch\s*\(\s*['\"`]/api/sprint-nav-status", body
    )
    assert bare_fetch is None, (
        "statusRefresh must not call bare fetch('/api/sprint-nav-status') — "
        "use snavNavStatusFetch to avoid duplicating the request"
    )


# ── AC5 ───────────────────────────────────────────────────────────────────────

def test_sse_handler_calls_loadHomeData_on_sprint_events():
    """AC5: _projConnectSse SSE handler calls loadHomeData on sprint_finished/sprint_reconciled."""
    body = _extract_function_body(_html(), "_projConnectSse")
    assert body is not None, "_projConnectSse function not found"
    assert "loadHomeData" in body, (
        "_projConnectSse must call loadHomeData when sprint_finished or sprint_reconciled "
        "fires so the sidebar updates immediately without waiting for the 30s tick"
    )


# ── AC6 ── structural rate estimate ───────────────────────────────────────────

def test_refresh_intervals_diagnostic_registered():
    """AC6/UAT: window.__refreshIntervals is set so DevTools can count active 30s timers."""
    html = _html()
    assert "window.__refreshIntervals" in html, (
        "window.__refreshIntervals must be assigned the single merged timer handle "
        "so UAT step 3 can count active timers via the DevTools console"
    )
