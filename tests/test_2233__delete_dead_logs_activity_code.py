"""Tests for issue #2233: Delete dead Logs/Activity code still executing on every page load.

AC-1: evlFetch, evlRender, evlRenderTimeline and the Events-Activity-Log +
      error-badge blocks (~project.html:26410-27900) removed from project.html

AC-2: src/logs-error-badge.js, src/logs-view-controls.js, src/activity-grouping.js
      deleted and dropped from the bundle entry index.js.
      NOTE: src/logpanel.js is intentionally retained — colorizeLogLine is still
      called by live sprint board code (lines ~11679, 15247, 17824 of project.html)
      and by progress activity overlays. Deleting it would cause ReferenceError in
      the sprint inspector log viewer, violating AC-4.

AC-3: npm run build succeeds — verified structurally by confirming index.js does
      not import the deleted modules (a missing import causes esbuild to abort).

AC-4: Loading a project page produces no console errors and issues no request to
      the removed endpoints — verified by confirming evlFetch() is not called at
      page-load time and _logsBadgeFetch() is fully removed from project.html.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "apps" / "dashboard" / "static" / "src"
PROJECT_HTML = REPO_ROOT / "apps" / "dashboard" / "static" / "project.html"
INDEX_JS = SRC_DIR / "index.js"

_HTML = PROJECT_HTML.read_text()
_INDEX = INDEX_JS.read_text()


# ── AC-1: dead JS functions removed from project.html ────────────────────────

class TestDeadJsFunctionsRemoved:
    """The three primary dead functions are no longer defined in project.html (AC-1)."""

    def test_evl_fetch_function_not_defined(self):
        assert "function evlFetch(" not in _HTML, (
            "evlFetch() function must be removed from project.html (dead since #2025 removed logs tab)"
        )

    def test_evl_render_function_not_defined(self):
        assert "function evlRender(" not in _HTML, (
            "evlRender() function must be removed from project.html"
        )

    def test_evl_render_timeline_function_not_defined(self):
        assert "function evlRenderTimeline(" not in _HTML, (
            "evlRenderTimeline() function must be removed from project.html"
        )

    def test_logs_tab_js_section_header_removed(self):
        assert "// ── Logs Tab (issue #420)" not in _HTML, (
            "The '// ── Logs Tab (issue #420)' JS section must be removed from project.html"
        )

    def test_evl_activity_log_section_header_removed(self):
        assert "// ── Events Activity Log (issue #633)" not in _HTML, (
            "The '// ── Events Activity Log (issue #633)' JS section must be removed"
        )

    def test_evl_branch_cleanup_section_removed(self):
        assert "// ── Branch cleanup helpers (issue #634)" not in _HTML, (
            "The '// ── Branch cleanup helpers' JS section must be removed"
        )


# ── AC-2: dead module files deleted, removed from bundle entry ────────────────

class TestDeadModulesDeleted:
    """The three orphaned JS modules are deleted from disk and from index.js (AC-2)."""

    def test_activity_grouping_js_deleted(self):
        assert not (SRC_DIR / "activity-grouping.js").exists(), (
            "src/activity-grouping.js must be deleted (dead: evlGroupEventsByRun is only "
            "called from the removed evl section)"
        )

    def test_logs_error_badge_js_deleted(self):
        assert not (SRC_DIR / "logs-error-badge.js").exists(), (
            "src/logs-error-badge.js must be deleted (dead: logsReadLastVisit, "
            "logsWriteLastVisit, buildEvlFetchUrl are only used by removed code)"
        )

    def test_logs_view_controls_js_deleted(self):
        assert not (SRC_DIR / "logs-view-controls.js").exists(), (
            "src/logs-view-controls.js must be deleted (dead: shouldAutoLoadRaw, "
            "pickAutoSprintLabel, logsToolbarVisibility are only used by removed code)"
        )

    def test_activity_grouping_not_imported_in_index(self):
        assert "activity-grouping" not in _INDEX, (
            "index.js must not import from activity-grouping.js (file deleted)"
        )

    def test_logs_error_badge_not_imported_in_index(self):
        assert "logs-error-badge" not in _INDEX, (
            "index.js must not import from logs-error-badge.js (file deleted)"
        )

    def test_logs_view_controls_not_imported_in_index(self):
        assert "logs-view-controls" not in _INDEX, (
            "index.js must not import from logs-view-controls.js (file deleted)"
        )

    def test_logpanel_js_retained(self):
        """logpanel.js must NOT be deleted — colorizeLogLine is used by live sprint
        board code and progress activity overlays (see project.html lines ~11679,
        15247, 15686, 17824, 17850, 17895). Deleting it would cause ReferenceError."""
        assert (SRC_DIR / "logpanel.js").exists(), (
            "src/logpanel.js must be retained; colorizeLogLine is still called by "
            "live sprint board inspector and progress activity log rendering"
        )

    def test_dead_evl_symbols_not_exported_from_index(self):
        """index.js must not expose the dead evl helpers on window/globalThis."""
        for sym in ("evlGroupEventsByRun", "logsReadLastVisit", "logsWriteLastVisit",
                    "buildEvlFetchUrl", "logsCountNewErrors", "evlIsErrorEvent",
                    "shouldAutoLoadRaw", "pickAutoSprintLabel", "logsToolbarVisibility"):
            assert sym not in _INDEX, (
                f"index.js must not export dead symbol '{sym}' (module deleted)"
            )


# ── AC-4: no dead calls at page-load time ────────────────────────────────────

class TestPageLoadNoDeadCalls:
    """evlFetch() is not triggered at page-load; _logsBadgeFetch() is removed (AC-4)."""

    def test_evl_fetch_not_called_in_html(self):
        """evlFetch must not appear anywhere in project.html (not just the page-load
        path). All callers are in the deleted section or were the init guard."""
        assert "evlFetch" not in _HTML, (
            "evlFetch must be fully removed from project.html — it was triggering a "
            "network request to /api/projects/*/events on every page load"
        )

    def test_logs_badge_fetch_not_in_project_html(self):
        assert "_logsBadgeFetch" not in _HTML, (
            "_logsBadgeFetch() must be removed — it fetched /api/projects/*/events "
            "on every page load even when not on the logs tab"
        )

    def test_evl_is_error_event_sse_guard_removed(self):
        """The SSE handler must no longer call evlIsErrorEvent to increment the badge."""
        assert "evlIsErrorEvent" not in _HTML, (
            "The evlIsErrorEvent badge-increment guard in the SSE handler must be removed"
        )

    def test_logs_update_nav_badge_not_called(self):
        """_logsUpdateNavBadge no-ops since the badge element was removed in #2025.
        The function itself and all calls to it must be gone."""
        assert "_logsUpdateNavBadge" not in _HTML, (
            "_logsUpdateNavBadge must be removed — it always no-ops because "
            "#logs-nav-badge element was removed in #2025"
        )

    def test_logs_badge_count_state_removed(self):
        """_logsBadgeCount state variable must be removed along with its callers."""
        assert "_logsBadgeCount" not in _HTML, (
            "_logsBadgeCount state must be removed (no badge element to update)"
        )
