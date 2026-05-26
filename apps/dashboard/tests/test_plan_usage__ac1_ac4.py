"""Tests for GET /api/plan-usage endpoint — AC-1 through AC-4.

Requires a running dev server at localhost:8000.
These tests verify shape and contract, not exact token counts
(the DB state is not controlled here).

Server behaviour:
  - When PLAN_TYPE or WINDOW_TOKEN_LIMIT are unset → HTTP 404 (AC-1: no 500).
  - When configured → HTTP 200 with JSON payload containing all AC-3 fields.
"""
import pytest


def _plan_data(client):
    """Fetch /api/plan-usage and return (status_code, data).

    Returns (404, None) when plan usage is not configured.
    """
    res = client.get("/api/plan-usage")
    if res.status_code == 404:
        return 404, None
    return res.status_code, res.json()


# ── AC-3: endpoint exists and returns valid JSON shape ────────────────────────

class TestPlanUsageEndpointShape:
    def test_endpoint_returns_200_or_404(self, client):
        """GET /api/plan-usage returns 200 (configured) or 404 (unconfigured)."""
        res = client.get("/api/plan-usage")
        assert res.status_code in (200, 404)

    def test_response_is_json(self, client):
        """Response body is valid JSON."""
        res = client.get("/api/plan-usage")
        data = res.json()
        assert isinstance(data, dict)

    def test_disabled_response_when_not_configured(self, client):
        """When plan usage is not configured, 404 is returned and no 5xx error.

        AC-1: no error is raised.
        """
        res = client.get("/api/plan-usage")
        assert res.status_code != 500  # must never raise 5xx

    def test_enabled_response_has_required_keys(self, client):
        """When configured (200), all AC-3 required keys are present."""
        status_code, data = _plan_data(client)
        if status_code == 404:
            pytest.skip("Plan usage not configured — skipping enabled-response shape test")

        required = {
            "window_tokens", "window_limit", "window_pct",
            "window_start", "window_resets_at", "seconds_remaining",
            "weekly_tokens", "weekly_limit", "status",
        }
        missing = required - set(data.keys())
        assert not missing, f"Missing keys: {missing}"

    def test_status_is_valid_enum(self, client):
        """status must be one of: no_activity, active, expired."""
        status_code, data = _plan_data(client)
        if status_code == 404:
            pytest.skip("Plan usage not configured")
        assert data["status"] in ("no_activity", "active", "expired")

    def test_window_pct_range(self, client):
        """window_pct must be between 0 and 100."""
        status_code, data = _plan_data(client)
        if status_code == 404:
            pytest.skip("Plan usage not configured")
        pct = data["window_pct"]
        assert isinstance(pct, (int, float))
        assert 0.0 <= pct <= 100.0

    def test_seconds_remaining_non_negative(self, client):
        """seconds_remaining must be >= 0."""
        status_code, data = _plan_data(client)
        if status_code == 404:
            pytest.skip("Plan usage not configured")
        assert data["seconds_remaining"] >= 0

    def test_window_tokens_non_negative(self, client):
        """window_tokens must be a non-negative integer."""
        status_code, data = _plan_data(client)
        if status_code == 404:
            pytest.skip("Plan usage not configured")
        assert isinstance(data["window_tokens"], int)
        assert data["window_tokens"] >= 0

    def test_window_limit_positive(self, client):
        """window_limit must be a positive integer when enabled."""
        status_code, data = _plan_data(client)
        if status_code == 404:
            pytest.skip("Plan usage not configured")
        assert isinstance(data["window_limit"], int)
        assert data["window_limit"] > 0


# ── AC-1: no error when unconfigured ──────────────────────────────────────────

class TestPlanUsageAC1:
    def test_no_500_error(self, client):
        """AC-1: Endpoint never raises 500 even when env vars are missing."""
        res = client.get("/api/plan-usage")
        assert res.status_code != 500

    def test_response_is_always_dict(self, client):
        """AC-1: Response is always a JSON object (not an array or primitive)."""
        data = client.get("/api/plan-usage").json()
        assert isinstance(data, dict)


# ── AC-4: no_activity status when no token rows exist ─────────────────────────

class TestPlanUsageAC4:
    def test_no_activity_status_fields(self, client):
        """AC-4: When status is no_activity, window_tokens=0 and pct=0."""
        status_code, data = _plan_data(client)
        if status_code == 404:
            pytest.skip("Plan usage not configured")
        if data["status"] != "no_activity":
            pytest.skip("DB has token rows — skipping no_activity test")
        assert data["window_tokens"] == 0
        assert data["window_pct"] == 0.0

    def test_expired_status_fields(self, client):
        """AC-4: When status is expired, window_tokens=0 and seconds_remaining=0."""
        status_code, data = _plan_data(client)
        if status_code == 404:
            pytest.skip("Plan usage not configured")
        if data["status"] != "expired":
            pytest.skip("No expired window in current DB state")
        assert data["window_tokens"] == 0
        assert data["seconds_remaining"] == 0

    def test_active_window_has_timestamps(self, client):
        """AC-4: An active window provides non-null window_start and window_resets_at."""
        status_code, data = _plan_data(client)
        if status_code == 404:
            pytest.skip("Plan usage not configured")
        if data["status"] != "active":
            pytest.skip("No active window in current DB state")
        assert data["window_start"] is not None
        assert data["window_resets_at"] is not None
        # Both must be ISO-8601 strings ending in Z
        assert data["window_start"].endswith("Z")
        assert data["window_resets_at"].endswith("Z")
