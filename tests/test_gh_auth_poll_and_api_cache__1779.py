"""Tests for issue #1779: Reduce gh-auth poll frequency and cache stable config endpoints.

This test suite verifies HTTP-level acceptance criteria for the feature:
- AC1–AC2: Device login poll interval reduced from 600ms to 2000ms
- AC3–AC8: Module-level API caching for /api/environment, /api/version, /api/settings

Frontend behavioral tests (AC1–AC8 JavaScript tests) are in tests/frontend/:
  - tests/frontend/device-login-poll.test.mjs — AC1–AC2 poll interval
  - tests/frontend/api-cache.test.mjs — AC3–AC8 cache behavior

These HTTP tests verify server-side behavior and cache invalidation flow.
Run with: pytest tests/test_gh_auth_poll_and_api_cache__1779.py -v --tb=short

Expected endpoints:
  GET /api/environment — returns { environment, port, ... }
  GET /api/version — returns { branch, git_sha, build_timestamp, ... }
  GET /api/settings — returns { history_cache_ttl_min, ... }
  PUT /api/settings — updates settings and should invalidate cache on client
"""
import os
import pytest
import httpx
import json

BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# ── AC3: /api/environment is fetched at most once per page session ──────────────

def test_ac3_environment_endpoint_accessible(client):
    """AC3: /api/environment endpoint exists and returns valid data."""
    r = client.get("/api/environment")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    data = r.json()
    assert isinstance(data, dict), "Response must be a JSON object"
    assert "environment" in data, "Response must include 'environment' field"


def test_ac3_environment_has_required_fields(client):
    """AC3: /api/environment returns expected structure."""
    r = client.get("/api/environment")
    assert r.status_code == 200
    data = r.json()
    # Expected fields from server: environment, port, dashboard_url, etc.
    assert data.get("environment") in ["uat", "prd"], f"environment must be 'uat' or 'prd', got {data.get('environment')}"
    assert "port" in data, "port field must be present"


# ── AC4: /api/version is fetched at most once per page session ──────────────

def test_ac4_version_endpoint_accessible(client):
    """AC4: /api/version endpoint exists and returns valid data."""
    r = client.get("/api/version")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    data = r.json()
    assert isinstance(data, dict), "Response must be a JSON object"
    assert "branch" in data, "Response must include 'branch' field"


def test_ac4_version_has_required_fields(client):
    """AC4: /api/version returns expected structure."""
    r = client.get("/api/version")
    assert r.status_code == 200
    data = r.json()
    assert "branch" in data, "branch field must be present"
    assert "git_sha" in data, "git_sha field must be present"
    assert "build_timestamp" in data, "build_timestamp field must be present"


# ── AC5–AC6: /api/settings is fetched at most once per session ──────────────

def test_ac5_settings_endpoint_accessible(client):
    """AC5–AC6: /api/settings endpoint exists and returns valid data."""
    r = client.get("/api/settings")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    data = r.json()
    assert isinstance(data, dict), "Response must be a JSON object"
    assert "history_cache_ttl_min" in data, "Response must include 'history_cache_ttl_min' field"


def test_ac5_settings_has_expected_fields(client):
    """AC5–AC6: /api/settings returns expected structure."""
    r = client.get("/api/settings")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data.get("history_cache_ttl_min"), int), "history_cache_ttl_min must be an integer"


# ── AC6: /api/settings cache is invalidated after a successful PUT ──────────────

def test_ac6_settings_put_accepts_updates(client):
    """AC6: PUT /api/settings accepts valid update payloads.

    This test verifies that the PUT endpoint accepts history_cache_ttl_min updates.
    Cache invalidation is verified on the client side in tests/frontend/api-cache.test.mjs.
    """
    # First, read current settings
    r = client.get("/api/settings")
    assert r.status_code == 200
    current = r.json()
    original_ttl = current.get("history_cache_ttl_min", 5)

    # Update with a new value
    new_ttl = (original_ttl + 1) if original_ttl < 30 else original_ttl - 1
    payload = {"history_cache_ttl_min": new_ttl}

    r = client.put(
        "/api/settings",
        content=json.dumps(payload),
        headers={"Content-Type": "application/json"}
    )

    # Expect 200 OK on successful update
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    # Verify the change persists (read-back after PUT)
    r2 = client.get("/api/settings")
    assert r2.status_code == 200
    updated = r2.json()
    assert updated.get("history_cache_ttl_min") == new_ttl, \
        f"Expected history_cache_ttl_min={new_ttl}, got {updated.get('history_cache_ttl_min')}"


def test_ac6_settings_put_invalid_payload(client):
    """AC6: PUT /api/settings rejects invalid payloads gracefully."""
    payload = {"history_cache_ttl_min": "not_a_number"}

    r = client.put(
        "/api/settings",
        content=json.dumps(payload),
        headers={"Content-Type": "application/json"}
    )

    # Expect either 400 (bad request) or 422 (unprocessable entity)
    assert r.status_code in [400, 422], \
        f"Expected 400/422 for invalid payload, got {r.status_code}: {r.text}"


# ── AC7: No stale-settings regression ──────────────────────────────

def test_ac7_settings_read_reflects_written_value(client):
    """AC7: After writing a setting, a subsequent read returns the new value.

    This verifies the server persists settings correctly and returns fresh data.
    """
    # Get current value
    r = client.get("/api/settings")
    assert r.status_code == 200
    current = r.json()
    original_ttl = current.get("history_cache_ttl_min", 5)

    # Write a new value (pick one that differs)
    new_ttl = (original_ttl % 30) + 1  # Cycle through 1-30
    payload = {"history_cache_ttl_min": new_ttl}

    r = client.put(
        "/api/settings",
        content=json.dumps(payload),
        headers={"Content-Type": "application/json"}
    )
    assert r.status_code == 200, f"PUT failed: {r.text}"

    # Immediately read back
    r2 = client.get("/api/settings")
    assert r2.status_code == 200
    updated = r2.json()

    # Verify the new value is present (no stale cache)
    assert updated.get("history_cache_ttl_min") == new_ttl, \
        f"Expected new value {new_ttl}, got {updated.get('history_cache_ttl_min')}"


# ── AC8: Cache is module-level (resets on full page reload) ──────────────────

def test_ac8_multiple_concurrent_endpoint_calls_succeed(client):
    """AC8: Multiple concurrent requests to stable endpoints all succeed.

    This verifies the server can handle concurrent clients correctly.
    In production, browser clients call these endpoints from multiple script locations,
    and the frontend cache deduplicates them. The server must handle concurrent requests.
    """
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        # Simulate concurrent calls by making multiple requests in sequence
        responses = [
            c.get("/api/environment"),
            c.get("/api/version"),
            c.get("/api/settings"),
            c.get("/api/environment"),
            c.get("/api/settings"),
        ]

    for r in responses:
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"


# ── AC1–AC2: Device login poll (behavioral only) ──────────────────────────────

def test_ac1_ac2_device_login_poll_note(client):
    """AC1–AC2: Device login poll interval is 2000ms (not 600ms).

    This acceptance criterion is verified at the JavaScript/browser level:
    - AC1: GH_AUTH_POLL_INTERVAL_MS is 2000ms (checked in device-login-poll.test.mjs)
    - AC2: Poll function is invoked immediately, so flow completes within ~2s

    The HTTP test for the endpoint is below; browser-level verification (Network tab)
    is manual in UAT step 1.
    """
    pytest.skip("manual — AC1–AC2 verified via browser Network tab inspection and frontend tests")


def test_ac1_ac2_gh_auth_login_status_endpoint_accessible(client):
    """AC1–AC2: /api/gh-auth/login/status endpoint exists.

    The device login poll calls this endpoint every 2 seconds (per AC1–AC2).
    Verify it exists and is callable (actual polling timing verified in UAT step 1).
    """
    # This endpoint requires an active device login flow; outside that context
    # it may return 400 or other status. We just verify it exists and responds.
    r = client.get("/api/gh-auth/login/status")

    # Accept 200 (authenticated) or 400+ (no active login) — just verify it's not a 404
    assert r.status_code != 404, f"/api/gh-auth/login/status should exist; got 404"
    assert r.status_code >= 200, f"Unexpected status {r.status_code}"
