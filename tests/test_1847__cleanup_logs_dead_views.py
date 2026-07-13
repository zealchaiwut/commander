"""Tests for issue #1847 — cleanup: delete unreachable Logs Search/Issues views.

AC coverage:
  AC1 — #logs-search-pane markup, logs-issues container reference, logsSearchSubmit(),
         issues-grouping JS branches, and logs-issue-group* CSS are removed from project.html
  AC2 — logsSetView() no longer special-cases 'issues' or 'search' modes
  AC3 — Activity, Timeline, and Raw view panes and their tab buttons are still present
"""
from __future__ import annotations

from pathlib import Path

STATIC_DIR = Path(__file__).parent.parent / "apps" / "dashboard" / "static"


def _html() -> str:
    return (STATIC_DIR / "project.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# AC1 — dead markup and JS removed
# ---------------------------------------------------------------------------

class TestDeadMarkupRemoved:
    def test_logs_search_pane_markup_absent(self):
        assert 'id="logs-search-pane"' not in _html(), (
            "#logs-search-pane HTML element must be removed (AC1)"
        )

    def test_logs_issues_container_reference_absent(self):
        assert "getElementById('logs-issues')" not in _html(), (
            "getElementById('logs-issues') reference must be removed (AC1)"
        )

    def test_logs_search_submit_function_absent(self):
        assert "function logsSearchSubmit(" not in _html(), (
            "logsSearchSubmit() function definition must be removed (AC1)"
        )

    def test_logs_search_submit_button_absent(self):
        assert "logsSearchSubmit()" not in _html(), (
            "All calls to logsSearchSubmit() must be removed (AC1)"
        )

    def test_logs_issue_group_css_absent(self):
        assert ".logs-issue-group" not in _html(), (
            ".logs-issue-group* CSS rules must be removed (AC1)"
        )

    def test_lsr_state_absent(self):
        assert "_lsrState" not in _html(), (
            "_lsrState (search-view state) must be removed (AC1)"
        )


# ---------------------------------------------------------------------------
# AC2 — logsSetView() no longer special-cases 'issues'/'search'
# ---------------------------------------------------------------------------

class TestLogsSetViewClean:
    def test_logs_set_view_defined(self):
        assert "function logsSetView(" in _html(), (
            "logsSetView() must still be defined (AC2)"
        )

    def test_issues_mode_special_case_absent(self):
        content = _html()
        # Find the logsSetView function body and verify it doesn't guard 'issues'/'search'
        start = content.find("function logsSetView(")
        assert start != -1, "logsSetView must exist"
        # Grab enough of the function body to check the first few lines
        snippet = content[start:start + 300]
        assert "mode === 'issues'" not in snippet, (
            "logsSetView must not special-case 'issues' mode (AC2)"
        )
        assert "mode === 'search'" not in snippet, (
            "logsSetView must not special-case 'search' mode (AC2)"
        )

    def test_issues_search_collapse_line_absent(self):
        content = _html()
        assert "if (mode === 'issues' || mode === 'search')" not in content, (
            "The issues/search mode collapse guard must be removed from logsSetView (AC2)"
        )


# ---------------------------------------------------------------------------
# AC3 — Activity, Timeline, Raw panes and tab buttons still present
# ---------------------------------------------------------------------------

class TestExistingViewsPreserved:
    def test_logs_activity_pane_present(self):
        assert 'id="logs-activity"' in _html(), (
            "Activity pane (#logs-activity) must still exist (AC3)"
        )

    def test_logs_timeline_pane_present(self):
        assert 'id="logs-timeline"' in _html(), (
            "Timeline pane (#logs-timeline) must still exist (AC3)"
        )

    def test_logs_raw_pane_present(self):
        assert 'id="logs-raw"' in _html(), (
            "Raw pane (#logs-raw) must still exist (AC3)"
        )

    def test_activity_tab_button_present(self):
        assert "logsSetView('activity')" in _html(), (
            "Activity tab button must still call logsSetView('activity') (AC3)"
        )

    def test_timeline_tab_button_present(self):
        assert "logsSetView('timeline')" in _html(), (
            "Timeline tab button must still call logsSetView('timeline') (AC3)"
        )

    def test_raw_tab_button_present(self):
        assert "logsSetView('raw')" in _html(), (
            "Raw tab button must still call logsSetView('raw') (AC3)"
        )
