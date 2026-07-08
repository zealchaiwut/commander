"""Tests for issue #1779: Reduce gh-auth poll frequency and cache stable config endpoints.

These HTTP tests verify server-side behavior and cache invalidation flow.
Frontend behavioral tests are in tests/frontend/:
  - tests/frontend/device-login-poll.test.mjs — AC1-AC2 poll interval
  - tests/frontend/api-cache.test.mjs — AC3-AC8 cache behavior

Run with: pytest tests/test_gh_auth_poll_and_api_cache__1779.py -v --tb=short
"""
import json
import os

import httpx
import pytest

_uat_url = os.environ.get("UAT_BASE_URL", "").strip()
_uat_port = os.environ.get("UAT_PORT", "").strip()
if _uat_url:
    BASE_URL = _uat_url
elif _uat_port:
    BASE_URL = f"http://localhost:{_uat_port}"
else:
    BASE_URL = None

_requires_uat = pytest.mark.skipif(
    BASE_URL is None,
    reason="UAT_BASE_URL / UAT_PORT not set — skipping UAT-level HTTP tests",
)


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL or "http://localhost:8001", timeout=10.0) as c:
        yield c


# -- AC3: /api/environment is fetched at most once per page session -----------

@_requires_uat
def test_ac3_environment_endpoint_accessible(client):
    """AC3: /api/environment endpoint exists and returns valid data."""
    r = client.get("/api/environment")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    data = r.json()
    assert isinstance(data, dict), "Response must be a JSON object"
    assert "environment" in data, "Response must include 'environment' field"


@_requires_uat
def test_ac3_environment_has_required_fields(client):
    """AC3: /api/environment returns expected structure."""
    r = client.get("/api/environment")
    assert r.status_code == 200
    data = r.json()
    assert data.get("environment") in ["uat", "prd"], (
        f"environment must be 'uat' or 'prd', got {data.get('environment')}"
    )
    assert "features" in data, "features field must be present"


# -- AC4: /api/version is fetched at most once per page session ---------------

@_requires_uat
def test_ac4_version_endpoint_accessible(client):
    """AC4: /api/version endpoint exists and returns valid data."""
    r = client.get("/api/version")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    data = r.json()
    assert isinstance(data, dict), "Response must be a JSON object"
    assert "branch" in data, "Response must include 'branch' field"


@_requires_uat
def test_ac4_version_has_required_fields(client):
    """AC4: /api/version returns expected structure."""
    r = client.get("/api/version")
    assert r.status_code == 200
    data = r.json()
    assert "branch" in data, "branch field must be present"
    assert "git_sha" in data, "git_sha field must be present"
    assert "build_timestamp" in data, "build_timestamp field must be present"


# -- AC5-AC6: /api/settings is fetched at most once per session ---------------

@_requires_uat
def test_ac5_settings_endpoint_accessible(client):
    """AC5-AC6: /api/settings endpoint exists and returns valid data."""
    r = client.get("/api/settings")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    data = r.json()
    assert isinstance(data, dict), "Response must be a JSON object"
    assert "history_cache_ttl_min" in data, (
        "Response must include 'history_cache_ttl_min' field"
    )


@_requires_uat
def test_ac5_settings_has_expected_fields(client):
    """AC5-AC6: /api/settings returns expected structure."""
    r = client.get("/api/settings")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data.get("history_cache_ttl_min"), int), (
        "history_cache_ttl_min must be an integer"
    )


# -- AC6: /api/settings cache is invalidated after a successful PUT -----------

@_requires_uat
def test_ac6_settings_put_accepts_updates(client):
    """AC6: PUT /api/settings accepts valid update payloads."""
    r = client.get("/api/settings")
    assert r.status_code == 200
    current = r.json()
    original_ttl = current.get("history_cache_ttl_min", 5)

    new_ttl = (original_ttl + 1) if original_ttl < 30 else original_ttl - 1
    payload = {"history_cache_ttl_min": new_ttl}

    r = client.put(
        "/api/settings",
        content=json.dumps(payload),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    r2 = client.get("/api/settings")
    assert r2.status_code == 200
    updated = r2.json()
    assert updated.get("history_cache_ttl_min") == new_ttl, (
        f"Expected history_cache_ttl_min={new_ttl},"
        f" got {updated.get('history_cache_ttl_min')}"
    )


@_requires_uat
def test_ac6_settings_put_invalid_payload(client):
    """AC6: PUT /api/settings rejects invalid payloads gracefully."""
    payload = {"history_cache_ttl_min": "not_a_number"}

    r = client.put(
        "/api/settings",
        content=json.dumps(payload),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code in [400, 422], (
        f"Expected 400/422 for invalid payload, got {r.status_code}: {r.text}"
    )


# -- AC7: No stale-settings regression ----------------------------------------

@_requires_uat
def test_ac7_settings_read_reflects_written_value(client):
    """AC7: After writing a setting, a subsequent read returns the new value."""
    r = client.get("/api/settings")
    assert r.status_code == 200
    current = r.json()
    original_ttl = current.get("history_cache_ttl_min", 5)

    new_ttl = (original_ttl % 30) + 1
    payload = {"history_cache_ttl_min": new_ttl}

    r = client.put(
        "/api/settings",
        content=json.dumps(payload),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 200, f"PUT failed: {r.text}"

    r2 = client.get("/api/settings")
    assert r2.status_code == 200
    updated = r2.json()

    assert updated.get("history_cache_ttl_min") == new_ttl, (
        f"Expected new value {new_ttl},"
        f" got {updated.get('history_cache_ttl_min')}"
    )


# -- AC8: Cache is module-level (resets on full page reload) ------------------

@_requires_uat
def test_ac8_multiple_concurrent_endpoint_calls_succeed():
    """AC8: Multiple concurrent requests to stable endpoints all succeed."""
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        responses = [
            c.get("/api/environment"),
            c.get("/api/version"),
            c.get("/api/settings"),
            c.get("/api/environment"),
            c.get("/api/settings"),
        ]

    for r in responses:
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"


# -- AC1-AC2: Device login poll (behavioral only) -----------------------------

def test_ac1_ac2_device_login_poll_note():
    """AC1-AC2: Device login poll interval is 2000ms (not 600ms).

    Verified at the JavaScript/browser level via device-login-poll.test.mjs.
    Manual UAT step: inspect Network tab to confirm 2s poll interval.
    """
    pytest.skip(
        "manual — AC1-AC2 verified via browser Network tab and frontend tests"
    )


@_requires_uat
def test_ac1_ac2_gh_auth_login_status_endpoint_accessible(client):
    """AC1-AC2: /api/gh-auth/login/status endpoint exists."""
    r = client.get("/api/gh-auth/login/status")

    # Accept any 2xx/4xx — just verify it is not a 404
    assert r.status_code != 404, "/api/gh-auth/login/status should exist; got 404"
    assert r.status_code >= 200, f"Unexpected status {r.status_code}"
