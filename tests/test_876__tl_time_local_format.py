"""Tests for issue #876 — Format recent-activity timestamp to short local time.

Each test anchors to a specific acceptance criterion.

AC1  .tl-time renders HH:MM not raw ISO timestamp
AC2  Formatted time reflects local timezone (not UTC)
AC3  Formatted time fits 40px min-width (short string)
AC4  Change at the tl-row render line (where a.time is used)
AC5  All valid ISO created_at values display correctly parsed
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOME_HTML = REPO_ROOT / "apps" / "dashboard" / "static" / "home.html"


@pytest.fixture(scope="module")
def html() -> str:
    assert HOME_HTML.exists(), f"home.html not found at {HOME_HTML}"
    return HOME_HTML.read_text()


# ── AC1: .tl-time renders HH:MM, not raw ISO ─────────────────────────────────

def test_tl_time_does_not_render_raw_iso(html):
    """tl-time cell must not write a.time directly into the DOM."""
    # If the tl-time span directly interpolates a.time (unformatted), the raw
    # ISO string leaks into the cell.  The raw pattern is:
    #   tl-time">${esc(a.time
    assert 'tl-time">${esc(a.time' not in html, (
        "tl-time cell still renders raw a.time — must format to HH:MM first"
    )


def test_tl_time_uses_formatted_time(html):
    """tl-time cell must use a formatting helper, not the raw value."""
    # The cell should reference a formatted variable/function result, not a.time
    # directly.  Accept any pattern where a local-time helper is called and its
    # result is placed in the tl-time span.
    m = re.search(
        r'tl-time["\']>\$\{[^}]*(?:fmt|format|localTime|shortTime|toLocaleTimeString|getHours|HH|slice)[^}]*\}',
        html,
        re.IGNORECASE,
    )
    assert m, (
        "tl-time cell must use a time-formatting expression (e.g. toLocaleTimeString, "
        "getHours/getMinutes, or a named helper) — raw a.time interpolation not allowed"
    )


# ── AC2: reflects local timezone ─────────────────────────────────────────────

def test_local_time_not_utc(html):
    """The formatting must use local time methods, not UTC methods."""
    # UTC methods: getUTCHours, getUTCMinutes, toUTCString, toISOString
    # Local methods: getHours, getMinutes, toLocaleTimeString
    # The fix should NOT use UTC getters for the HH:MM display.
    assert "getUTCHours" not in html or "getHours" in html, (
        "Found getUTCHours but no getHours — must use local timezone getters"
    )
    # Confirm at least one local getter is present near tl-time logic
    local_getter = re.search(
        r'(?:getHours|getMinutes|toLocaleTimeString)',
        html,
    )
    assert local_getter, (
        "No local time getter (getHours/getMinutes/toLocaleTimeString) found — "
        "time formatting must use local timezone, not UTC"
    )


# ── AC3: output is short (fits 40px) ─────────────────────────────────────────

def test_time_format_is_compact(html):
    """The formatting must produce a short HH:MM string, not a long date string."""
    # Disallow patterns that would produce long strings like toLocaleString() with
    # date parts, or a.time directly.  The output should be at most 5 chars (HH:MM).
    # We assert there is NO toLocaleString() call inside the tl-time expression
    # (which would include date info), only toLocaleTimeString or manual padding.
    tl_time_section = re.search(
        r'tl-time["\']>\$\{([^}]+)\}',
        html,
    )
    assert tl_time_section, "tl-time cell interpolation not found"
    expr = tl_time_section.group(1)
    # Must not call the full toLocaleString (which includes date)
    assert "toLocaleString()" not in expr, (
        "toLocaleString() produces a date+time string that overflows 40px — "
        "use toLocaleTimeString() or manual HH:MM padding"
    )


# ── AC4: change is at the tl-row render line ─────────────────────────────────

def test_tl_row_uses_formatted_time(html):
    """The tl-row template literal must use a formatted time, not raw a.time."""
    # Find the tl-row construction and ensure the tl-time span uses a formatting
    # call rather than the bare a.time property.
    tl_row_match = re.search(
        r'tl-row["\']>[^`]*tl-time["\']>\$\{([^}]+)\}',
        html,
        re.DOTALL,
    )
    assert tl_row_match, "tl-row + tl-time construction not found in home.html"
    time_expr = tl_row_match.group(1)
    # The expression must not be just esc(a.time ...) — must transform the value
    assert time_expr.strip() != "esc(a.time || '')", (
        "tl-time still uses esc(a.time || '') verbatim — must format to HH:MM"
    )
    assert "a.time" not in time_expr or any(
        kw in time_expr
        for kw in ("getHours", "getMinutes", "toLocaleTimeString", "fmt", "format", "slice", "pad")
    ), (
        f"tl-time expression '{time_expr}' references a.time but applies no formatting"
    )


# ── AC5: valid ISO timestamps parse and display correctly ─────────────────────

def test_iso_parsing_handles_new_date(html):
    """The code must parse a.time via new Date() before formatting."""
    # ISO strings like '2026-06-15T02:42:00Z' must be passed through Date constructor
    # before extracting hours/minutes.
    assert "new Date(" in html, (
        "No 'new Date(' found in home.html — must parse a.time as a Date object "
        "before extracting HH:MM components"
    )


def test_padding_ensures_two_digits(html):
    """Hours and minutes must be zero-padded to two digits."""
    # Without padding, 9:05 renders as '9:5' which is wrong.
    # Acceptable patterns: padStart(2,'0'), String(...).padStart, or toLocaleTimeString.
    has_pad = (
        "padStart(2" in html
        or "padStart( 2" in html
        or "toLocaleTimeString" in html
        or re.search(r"String\(\w+\)\.pad", html)
    )
    assert has_pad, (
        "No zero-padding found — single-digit hours/minutes (e.g. 9:05) will "
        "render as '9:5'; use padStart(2,'0') or toLocaleTimeString"
    )
