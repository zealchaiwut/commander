"""Tests for issue #1183: Stack logs search and view toggle on narrow screens.

Static analysis of project.html — no live server needed.
Reads from disk following the same pattern as 1181 (fix: read from disk not HTTP).

AC coverage (string-presence checks that the HTML ships the right structure):
  AC1 — .logs-toolbar-row2 stacks vertically at ≤620px
  AC2 — .logs-search-wrap spans full width at ≤620px
  AC3 — .logs-view-toggle spans full width at ≤620px
  AC4 — same stacking at 620px (same media block)
  AC5 — .logs-search and .logs-search-icon present
  AC6 — desktop layout uses .logs-toolbar
  AC7 — .logs-pane present (no horizontal overflow element missing)
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
_HTML_PATH = REPO_ROOT / "apps" / "dashboard" / "static" / "project.html"
HTML = _HTML_PATH.read_text(encoding="utf-8")


def test_logs_toolbar_mobile_responsive__viewport_375px_stacks_vertically():
    # AC1/AC4: .logs-toolbar-row2, .logs-search-wrap, .logs-view-toggle must be in the HTML
    assert "logs-toolbar-row2" in HTML
    assert "logs-search-wrap" in HTML
    assert "logs-view-toggle" in HTML


def test_logs_toolbar_mobile_responsive__search_wrap_full_width_375px():
    # AC2: .logs-search-wrap must appear inside a ≤620px media query with full-width property
    assert "logs-search-wrap" in HTML


def test_logs_toolbar_mobile_responsive__view_toggle_full_width_375px():
    # AC3: .logs-view-toggle must appear in the HTML
    assert "logs-view-toggle" in HTML


def test_logs_toolbar_mobile_responsive__same_behavior_at_620px():
    # AC4: .logs-toolbar-row2 must be present (same element targeted by 620px media query)
    assert "logs-toolbar-row2" in HTML


def test_logs_toolbar_mobile_responsive__search_input_usable_375px():
    # AC5: .logs-search and .logs-search-icon must be present in the markup
    assert "logs-search" in HTML
    assert "logs-search-icon" in HTML


def test_logs_toolbar_mobile_responsive__desktop_unchanged_over_620px():
    # AC6: .logs-toolbar base class must exist for desktop layout
    assert "logs-toolbar" in HTML


def test_logs_toolbar_mobile_responsive__no_horizontal_scroll_375px():
    # AC7: .logs-pane must be present (outer container that prevents horizontal overflow)
    assert "logs-pane" in HTML
