"""Tests for issue #1793 — verify DELETE /api/alerts/{idx} is absent.

#1772 removed the dismiss-alert route. These tests confirm the route is truly
gone (returns 404) so any caller that expects 200 is provably stale.
"""
import pytest


@pytest.mark.live
class TestDeleteAlertRouteAbsent:
    def test_delete_alert_route_returns_404(self, client):
        """DELETE /api/alerts/{idx} must return 404 — route was removed in #1772."""
        res = client.delete("/api/alerts/0")
        assert res.status_code == 404, (
            f"Expected 404 (route removed in #1772) but got {res.status_code}. "
            "A stale DELETE /api/alerts route may have been re-added."
        )
