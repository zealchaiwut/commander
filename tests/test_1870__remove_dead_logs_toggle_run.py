"""
Tests for issue #1870 — Remove dead logsToggleRun/_logsTicketStatsHtml/_logsIcaCostHtml
after Timeline removal.

The Runs/Timeline view was removed in #1850, leaving these functions with no
remaining callers.  This ticket removes the orphaned dead code and cleans up
the _logsState fields they exclusively depended on.

AC coverage:
  AC1 — logsToggleRun function is absent from project.html
  AC2 — _logsTicketStatsHtml function is absent from project.html
  AC3 — _logsIcaCostHtml function is absent from project.html
  AC4 — _logsFetchTicketStats and _logsFetchIcaCost helpers are absent
         (their only caller was logsToggleRun)
  AC5 — _logsConnectLive is absent (its only caller was logsToggleRun)
  AC6 — _logsState does NOT contain ticketStats, icaCost, expanded,
         loadingStats, or loadingIcaCost fields
  AC7 — Core logs infrastructure is preserved: logsDestroy, _logsDisconnectLive,
         _logsEventsHtml, _logsFetchRunEvents are still present
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parent.parent
PROJECT_HTML = ROOT / "apps" / "dashboard" / "static" / "project.html"


def _html() -> str:
    return PROJECT_HTML.read_text(encoding="utf-8")


# ── AC1: logsToggleRun removed ────────────────────────────────────────────────


def test_ac1_logs_toggle_run_function_removed():
    """AC1: logsToggleRun must be deleted — its entry points were all removed in #1850."""
    assert "function logsToggleRun(" not in _html(), (
        "logsToggleRun still defined — delete this dead function (AC1)"
    )


def test_ac1_no_calls_to_logs_toggle_run():
    """AC1: No remaining calls to logsToggleRun."""
    assert "logsToggleRun(" not in _html(), (
        "stale call to logsToggleRun( found — remove all references (AC1)"
    )


# ── AC2: _logsTicketStatsHtml removed ────────────────────────────────────────


def test_ac2_logs_ticket_stats_html_function_removed():
    """AC2: _logsTicketStatsHtml must be deleted — only called from logsToggleRun."""
    assert "function _logsTicketStatsHtml(" not in _html(), (
        "_logsTicketStatsHtml still defined — delete this dead function (AC2)"
    )


def test_ac2_no_calls_to_logs_ticket_stats_html():
    """AC2: No remaining calls to _logsTicketStatsHtml."""
    assert "_logsTicketStatsHtml(" not in _html(), (
        "stale call to _logsTicketStatsHtml( found — remove all references (AC2)"
    )


# ── AC3: _logsIcaCostHtml removed ────────────────────────────────────────────


def test_ac3_logs_ica_cost_html_function_removed():
    """AC3: _logsIcaCostHtml must be deleted — only called from logsToggleRun."""
    assert "function _logsIcaCostHtml(" not in _html(), (
        "_logsIcaCostHtml still defined — delete this dead function (AC3)"
    )


def test_ac3_no_calls_to_logs_ica_cost_html():
    """AC3: No remaining calls to _logsIcaCostHtml."""
    assert "_logsIcaCostHtml(" not in _html(), (
        "stale call to _logsIcaCostHtml( found — remove all references (AC3)"
    )


# ── AC4: dead fetch helpers removed ──────────────────────────────────────────


def test_ac4_logs_fetch_ticket_stats_removed():
    """AC4: _logsFetchTicketStats must be deleted — its only caller was logsToggleRun."""
    assert "function _logsFetchTicketStats(" not in _html(), (
        "_logsFetchTicketStats still defined — delete this dead helper (AC4)"
    )


def test_ac4_logs_fetch_ica_cost_removed():
    """AC4: _logsFetchIcaCost must be deleted — its only caller was logsToggleRun."""
    assert "function _logsFetchIcaCost(" not in _html(), (
        "_logsFetchIcaCost still defined — delete this dead helper (AC4)"
    )


# ── AC5: _logsConnectLive removed ────────────────────────────────────────────


def test_ac5_logs_connect_live_removed():
    """AC5: _logsConnectLive must be deleted — its only caller was logsToggleRun."""
    assert "function _logsConnectLive(" not in _html(), (
        "_logsConnectLive still defined — delete this dead function (AC5)"
    )


def test_ac5_no_calls_to_logs_connect_live():
    """AC5: No remaining calls to _logsConnectLive."""
    assert "_logsConnectLive(" not in _html(), (
        "stale call to _logsConnectLive( found — remove all references (AC5)"
    )


# ── AC6: dead _logsState fields removed ──────────────────────────────────────


def test_ac6_logs_state_ticket_stats_field_removed():
    """AC6: _logsState must not contain a ticketStats field."""
    html = _html()
    state_start = html.find("const _logsState = {")
    assert state_start != -1, "_logsState object not found"
    brace_depth = 0
    obj_start = html.index("{", state_start)
    obj_end = obj_start
    for i in range(obj_start, len(html)):
        if html[i] == "{":
            brace_depth += 1
        elif html[i] == "}":
            brace_depth -= 1
            if brace_depth == 0:
                obj_end = i
                break
    state_body = html[obj_start : obj_end + 1]
    assert "ticketStats" not in state_body, (
        "_logsState still contains ticketStats — remove this field (AC6)"
    )


def test_ac6_logs_state_ica_cost_field_removed():
    """AC6: _logsState must not contain an icaCost field."""
    html = _html()
    state_start = html.find("const _logsState = {")
    assert state_start != -1, "_logsState object not found"
    brace_depth = 0
    obj_start = html.index("{", state_start)
    obj_end = obj_start
    for i in range(obj_start, len(html)):
        if html[i] == "{":
            brace_depth += 1
        elif html[i] == "}":
            brace_depth -= 1
            if brace_depth == 0:
                obj_end = i
                break
    state_body = html[obj_start : obj_end + 1]
    assert "icaCost" not in state_body, (
        "_logsState still contains icaCost — remove this field (AC6)"
    )


def test_ac6_logs_state_expanded_field_removed():
    """AC6: _logsState must not contain an expanded field."""
    html = _html()
    state_start = html.find("const _logsState = {")
    assert state_start != -1, "_logsState object not found"
    brace_depth = 0
    obj_start = html.index("{", state_start)
    obj_end = obj_start
    for i in range(obj_start, len(html)):
        if html[i] == "{":
            brace_depth += 1
        elif html[i] == "}":
            brace_depth -= 1
            if brace_depth == 0:
                obj_end = i
                break
    state_body = html[obj_start : obj_end + 1]
    assert "expanded" not in state_body, (
        "_logsState still contains expanded — remove this field (AC6)"
    )


def test_ac6_logs_state_loading_stats_field_removed():
    """AC6: _logsState must not contain a loadingStats field."""
    html = _html()
    state_start = html.find("const _logsState = {")
    assert state_start != -1, "_logsState object not found"
    brace_depth = 0
    obj_start = html.index("{", state_start)
    obj_end = obj_start
    for i in range(obj_start, len(html)):
        if html[i] == "{":
            brace_depth += 1
        elif html[i] == "}":
            brace_depth -= 1
            if brace_depth == 0:
                obj_end = i
                break
    state_body = html[obj_start : obj_end + 1]
    assert "loadingStats" not in state_body, (
        "_logsState still contains loadingStats — remove this field (AC6)"
    )


def test_ac6_logs_state_loading_ica_cost_field_removed():
    """AC6: _logsState must not contain a loadingIcaCost field."""
    html = _html()
    state_start = html.find("const _logsState = {")
    assert state_start != -1, "_logsState object not found"
    brace_depth = 0
    obj_start = html.index("{", state_start)
    obj_end = obj_start
    for i in range(obj_start, len(html)):
        if html[i] == "{":
            brace_depth += 1
        elif html[i] == "}":
            brace_depth -= 1
            if brace_depth == 0:
                obj_end = i
                break
    state_body = html[obj_start : obj_end + 1]
    assert "loadingIcaCost" not in state_body, (
        "_logsState still contains loadingIcaCost — remove this field (AC6)"
    )


# ── AC7: core logs infrastructure preserved ───────────────────────────────────


def test_ac7_logs_destroy_still_present():
    """AC7: logsDestroy must remain — it is called by tab teardown logic."""
    assert "function logsDestroy(" in _html(), (
        "logsDestroy was removed — this function must be preserved (AC7)"
    )


def test_ac7_logs_disconnect_live_still_present():
    """AC7: _logsDisconnectLive must remain — called by logsDestroy."""
    assert "function _logsDisconnectLive(" in _html(), (
        "_logsDisconnectLive was removed — this function must be preserved (AC7)"
    )


def test_ac7_logs_events_html_still_present():
    """AC7: _logsEventsHtml must remain — renders activity log entries."""
    assert "function _logsEventsHtml(" in _html(), (
        "_logsEventsHtml was removed — this function must be preserved (AC7)"
    )


def test_ac7_logs_fetch_run_events_still_present():
    """AC7: _logsFetchRunEvents must remain — fetches dispatch log for a run."""
    assert "function _logsFetchRunEvents(" in _html(), (
        "_logsFetchRunEvents was removed — this function must be preserved (AC7)"
    )


def test_ac7_logs_state_still_defined():
    """AC7: _logsState object must still be defined."""
    assert "const _logsState = {" in _html(), (
        "_logsState object was removed — it must be preserved (AC7)"
    )
