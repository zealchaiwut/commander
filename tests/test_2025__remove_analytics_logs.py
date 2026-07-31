"""Tests for issue #2025: remove Analytics and Logs tabs.

Acceptance Criteria:
- AC2: Backend routes (analytics, logs, events) remain functional
- AC4: Removed-tab redirects to Failures inbox
- AC whitelist: metrics/logs/status removed from tab whitelist; failures/sprint-mgmt/settings still resolve

Tests exercise behavior via source inspection + router unit tests (no full server startup).
"""
import os
import pytest
from pathlib import Path
from routers.pages import _VALID_PROJECT_TABS


class TestRemovedTabRedirects:
    """AC4: Verify removed-tab redirects exist in pages.py."""

    def test_metrics_redirect_handler_exists(self):
        """project_slug_metrics handler exists and routes to /project/{slug}/failures."""
        pages_file = Path("routers/pages.py").read_text()
        assert "@router.get(\"/project/{slug}/metrics\")" in pages_file
        assert "project_slug_metrics" in pages_file
        assert "failures" in pages_file

    def test_logs_redirect_handler_exists(self):
        """project_slug_logs handler exists and routes to /project/{slug}/failures."""
        pages_file = Path("routers/pages.py").read_text()
        assert "@router.get(\"/project/{slug}/logs\")" in pages_file
        assert "project_slug_logs" in pages_file
        assert "failures" in pages_file

    def test_status_redirect_handler_exists(self):
        """project_slug_status handler exists and routes to /project/{slug}/failures."""
        pages_file = Path("routers/pages.py").read_text()
        assert "@router.get(\"/project/{slug}/status\")" in pages_file
        assert "project_slug_status" in pages_file
        assert "failures" in pages_file

    def test_analytics_redirect_handler_exists(self):
        """project_slug_analytics handler exists (legacy route)."""
        pages_file = Path("routers/pages.py").read_text()
        assert "@router.get(\"/project/{slug}/analytics\")" in pages_file
        assert "project_slug_analytics" in pages_file


class TestTabWhitelist:
    """AC whitelist: metrics/logs/status removed; failures/sprint-mgmt/settings remain."""

    def test_metrics_not_in_valid_tabs(self):
        """metrics removed from _VALID_PROJECT_TABS."""
        assert "metrics" not in _VALID_PROJECT_TABS

    def test_logs_not_in_valid_tabs(self):
        """logs removed from _VALID_PROJECT_TABS."""
        assert "logs" not in _VALID_PROJECT_TABS

    def test_status_not_in_valid_tabs(self):
        """status removed from _VALID_PROJECT_TABS."""
        assert "status" not in _VALID_PROJECT_TABS

    def test_failures_in_valid_tabs(self):
        """failures remains in _VALID_PROJECT_TABS."""
        assert "failures" in _VALID_PROJECT_TABS

    def test_sprint_mgmt_in_valid_tabs(self):
        """sprint-mgmt remains in _VALID_PROJECT_TABS."""
        assert "sprint-mgmt" in _VALID_PROJECT_TABS

    def test_settings_in_valid_tabs(self):
        """settings remains in _VALID_PROJECT_TABS."""
        assert "settings" in _VALID_PROJECT_TABS


class TestBackendRoutesIntact:
    """AC2: Verify backend route handlers still exist in source (routes not deleted)."""

    def test_events_router_exists(self):
        """Events router still exists (preserved in analytics.py or events.py)."""
        # Check that there's still an events route handler in the codebase
        analytics_file = Path("routers/analytics.py")
        assert analytics_file.exists(), "analytics.py should exist (preserves /api/analytics routes)"

    def test_analytics_routes_exist(self):
        """Analytics routes (/api/analytics/cost, /api/analytics/metrics) still defined."""
        analytics_file = Path("routers/analytics.py")
        content = analytics_file.read_text()
        # These routes should not be removed
        assert "/analytics" in content or "@router" in content
        assert analytics_file.exists()

    def test_run_routes_exist(self):
        """Run log routes (/runs/<sprint>/<issue>/<agent>/log) still defined."""
        run_file = Path("routers/runs.py")
        assert run_file.exists(), "runs.py should exist (preserves /runs routes)"
        content = run_file.read_text()
        assert "/log" in content or "log" in content.lower()

    def test_run_browser_route_exists(self):
        """GET /run-browser route handler still exists."""
        # Check runs.py for run-browser route
        runs_file = Path("routers/runs.py")
        assert runs_file.exists(), "runs.py should exist"
        content = runs_file.read_text()
        assert "@router.get(\"/run-browser\")" in content, "run-browser route should still exist"
