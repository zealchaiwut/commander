"""Tests for issue #716 — Move Sprint History into Sprint Mgmt tab as sub-view.

NOTE (issue #798): the original two-state "Board | History" segmented control
(.smgmt-subtab-bar) was restructured into a three-tab sub-nav (.subnav /.subtab,
Board · Running · History). The structural assertions below were updated to the
new sub-nav while keeping #716's behavioural intent intact (the History sub-view
still loads the same summaries, the board is still the default, and toggling
sub-views still performs no page navigation).

Acceptance criteria tested:
  AC1 — Sprint Mgmt tab (pane-sprint-mgmt) shows a sub-nav (Board · Running ·
        History) at the top
  AC2 — "Board" segment displays the existing kanban board (default view)
  AC3 — "History" segment displays the same finished-sprint summaries
        rendered by _smgmtLoadSummaries today
  AC4 — Toggling between segments does not trigger a full page navigation
  AC5 — /project/{slug}/sprint-mgmt deep-link opens the Board segment by default
  AC6 — "Sprint history" entry is removed from the More dropdown
  AC7 — No other nav items are affected
  AC8 — Sprint history data/functionality is unchanged (same summaries/content)
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_PROJECT_HTML = _REPO_ROOT / "apps" / "dashboard" / "static" / "project.html"


@pytest.fixture(scope="module")
def html() -> str:
    assert _PROJECT_HTML.exists(), "project.html must exist"
    return _PROJECT_HTML.read_text(encoding="utf-8")


def _slice(html: str, start_marker: str, end_marker: str) -> str:
    idx = html.find(start_marker)
    assert idx >= 0, f"marker not found: {start_marker}"
    end = html.find(end_marker, idx)
    assert end > idx, f"end marker {end_marker} must come after {start_marker}"
    return html[idx:end]


def _pane_sprint_mgmt(html: str) -> str:
    """Inner HTML of the Sprint Mgmt pane (up to the next tab-pane)."""
    return _slice(html, 'id="pane-sprint-mgmt"', '<!-- Tickets tab -->')


def _subtab_bar(html: str) -> str:
    # Restructured into the .subnav strip by issue #798.
    return _slice(html, 'class="subnav"', "<!-- Board sub-view")


# ---------------------------------------------------------------------------
# AC1: sub-nav (Board · Running · History) at the top of pane-sprint-mgmt
# ---------------------------------------------------------------------------

class TestSegmentedControl:
    def test_subtab_bar_present(self, html):
        assert 'class="subnav"' in html, \
            "Sprint pane must have a sub-nav strip (.subnav, issue #798)"

    def test_subtab_bar_lives_inside_sprint_mgmt_pane(self, html):
        pane = _pane_sprint_mgmt(html)
        assert 'class="subnav"' in pane, \
            "Sub-nav must live inside pane-sprint-mgmt"

    def test_both_segment_buttons_present(self, html):
        for v in ("board", "history"):
            assert f'id="smgmt-subtab-{v}"' in html, \
                f"Sub-tab smgmt-subtab-{v} must be present"

    def test_segment_order_is_board_then_history(self, html):
        bar = _subtab_bar(html)
        order = re.findall(r'id="smgmt-subtab-(\w+)"', bar)
        # Running was inserted between Board and History by issue #798.
        assert order == ["board", "running", "history"], \
            f"Sub-tabs must be Board, Running, History; got {order}"

    def test_segment_labels_text(self, html):
        bar = _subtab_bar(html)
        for label in ("Board", "Running", "History"):
            assert label in bar, \
                f"Sub-nav must contain a '{label}' tab"

    def test_segments_call_show_subview(self, html):
        bar = _subtab_bar(html)
        assert "_smgmtShowSubView('board')" in bar, \
            "Board sub-tab must call _smgmtShowSubView('board')"
        assert "_smgmtShowSubView('history')" in bar, \
            "History sub-tab must call _smgmtShowSubView('history')"


# ---------------------------------------------------------------------------
# AC2: Board segment displays the existing kanban board, default view
# ---------------------------------------------------------------------------

class TestBoardSegment:
    def test_board_subview_exists(self, html):
        assert 'id="smgmt-subview-board"' in html, \
            "Board sub-view container smgmt-subview-board must exist"

    def test_board_subview_contains_kanban_board(self, html):
        board = _slice(html, 'id="smgmt-subview-board"', 'id="smgmt-subview-history"')
        assert 'id="smgmt-sprint-list"' in board, \
            "Board sub-view must contain the kanban sprint list"
        assert 'id="smgmt-backlog-pane"' in board, \
            "Board sub-view must contain the backlog pane"

    def test_board_subview_is_default_shown(self, html):
        # The board container carries the 'show' class in the static markup.
        m = re.search(r'<div class="([^"]*)" id="smgmt-subview-board"', html)
        assert m, "smgmt-subview-board must be a div with a class attribute"
        assert "show" in m.group(1).split(), \
            "Board sub-view must be the default (carry the 'show' class)"

    def test_board_segment_button_default_active(self, html):
        m = re.search(r'<button class="([^"]*)" id="smgmt-subtab-board"', html)
        assert m, "smgmt-subtab-board must be a button with a class attribute"
        assert "active" in m.group(1).split(), \
            "Board segment button must be active by default"


# ---------------------------------------------------------------------------
# AC3: History segment displays the same finished-sprint summaries
# ---------------------------------------------------------------------------

class TestHistorySegment:
    def test_history_subview_exists(self, html):
        assert 'id="smgmt-subview-history"' in html, \
            "History sub-view container smgmt-subview-history must exist"

    def test_history_subview_contains_summary_pane(self, html):
        hist = _slice(html, 'id="smgmt-subview-history"', '<!-- Tickets tab -->')
        assert 'id="smgmt-summary-pane"' in hist, \
            "History sub-view must contain the sprint summary pane"
        assert 'id="smgmt-summary-rows"' in hist, \
            "History sub-view must contain the summary rows container"

    def test_history_subview_hidden_by_default(self, html):
        m = re.search(r'<div class="([^"]*)" id="smgmt-subview-history"', html)
        assert m, "smgmt-subview-history must be a div with a class attribute"
        assert "show" not in m.group(1).split(), \
            "History sub-view must be hidden by default (no 'show' class)"

    def test_show_subview_history_loads_summaries(self, html):
        idx = html.find("function _smgmtShowSubView")
        assert idx >= 0, "_smgmtShowSubView must be defined"
        body = html[idx:idx + 900]
        assert "_smgmtLoadSummaries" in body, \
            "_smgmtShowSubView('history') must load summaries via _smgmtLoadSummaries"


# ---------------------------------------------------------------------------
# AC4: toggling segments does not trigger full page navigation
# ---------------------------------------------------------------------------

class TestToggleNoNavigation:
    def test_show_subview_defined(self, html):
        assert "function _smgmtShowSubView" in html, \
            "_smgmtShowSubView toggle function must exist"

    def test_show_subview_toggles_show_class(self, html):
        idx = html.find("function _smgmtShowSubView")
        body = html[idx:idx + 900]
        assert "classList.toggle('show'" in body, \
            "_smgmtShowSubView must toggle the 'show' class to switch sub-views"

    def test_show_subview_no_page_reload_or_nav(self, html):
        idx = html.find("function _smgmtShowSubView")
        end = html.find("\n}", idx)
        body = html[idx:end]
        for forbidden in ("location.reload", "location.href",
                          "history.pushState", "switchTab("):
            assert forbidden not in body, \
                f"_smgmtShowSubView must not navigate the page ({forbidden})"


# ---------------------------------------------------------------------------
# AC5: deep-link /sprint-mgmt opens the Board segment by default
# ---------------------------------------------------------------------------

class TestDeepLinkBoardDefault:
    def test_no_standalone_sprint_history_pane(self, html):
        assert 'id="pane-sprint-history"' not in html, \
            "Standalone pane-sprint-history must be removed (merged into Sprint Mgmt)"

    def test_sprint_history_route_no_longer_resolves_to_own_tab(self, html):
        """parseUrl must not route rawTab 'sprint-history' to its own removed tab —
        old deep-links fall through to the Sprint Mgmt board."""
        idx = html.find("function parseUrl")
        assert idx >= 0, "parseUrl must be defined"
        end = html.find("\n}", idx)
        body = html[idx:end]
        assert "? 'sprint-history'" not in body, \
            "parseUrl must not resolve a standalone 'sprint-history' tab anymore"


# ---------------------------------------------------------------------------
# AC6: "Sprint history" removed from the More dropdown
# ---------------------------------------------------------------------------

class TestMoreDropdown:
    def test_sprint_history_nav_entry_removed(self, html):
        assert 'id="stab-sprint-history"' not in html, \
            "Sprint history entry must be removed from the top nav"

    def test_nav_no_switchtab_sprint_history(self, html):
        assert "switchTab('sprint-history')" not in html, \
            "Top nav must not contain a switchTab('sprint-history') handler"

    def test_more_dropdown_has_no_sprint_history(self, html):
        dropdown = _slice(html, 'id="stab-dropdown-more"', "</div>")
        assert "sprint-history" not in dropdown, \
            "More dropdown must not reference sprint-history"


# ---------------------------------------------------------------------------
# AC7: no other nav items are affected
# ---------------------------------------------------------------------------

class TestOtherNavUnaffected:
    def test_more_dropdown_still_has_notes(self, html):
        assert 'id="stab-notes"' in html, \
            "Notes entry must remain in the More dropdown"

    def test_top_level_nav_items_intact(self, html):
        for el_id in ("stab-sprint-mgmt", "stab-tickets", "stab-logs",
                      "stab-metrics", "stab-settings"):
            assert f'id="{el_id}"' in html, \
                f"Nav item {el_id} must remain unaffected"


# ---------------------------------------------------------------------------
# AC8: sprint history data/functionality unchanged
# ---------------------------------------------------------------------------

class TestSummaryDataUnchanged:
    def test_load_summaries_still_defined(self, html):
        assert "async function _smgmtLoadSummaries" in html, \
            "_smgmtLoadSummaries loader must remain unchanged"

    def test_load_summaries_still_fetches_same_endpoint(self, html):
        idx = html.find("async function _smgmtLoadSummaries")
        body = html[idx:idx + 600]
        assert "/api/sprints/summaries" in body, \
            "Summaries must still come from /api/sprints/summaries"

    def test_summary_header_and_rows_intact(self, html):
        assert "Sprint Summaries" in html, "Summary pane title must be unchanged"
        assert 'id="smgmt-archived-toggle"' in html, \
            "Archived toggle in the summary pane must be unchanged"
