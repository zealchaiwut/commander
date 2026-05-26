"""Tests for issue #3: Add /api/health endpoint with server status, uptime, and DB connection state"""
import time
import pytest
import httpx


BASE_URL = "http://localhost:8000"


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# --- Acceptance Criteria ---

def test_add_api_health_endpoint__returns_200_when_server_running(client):
    # AC: GET /api/health returns HTTP 200 when the server is running normally
    # and the SQLite database file is reachable.
    r = client.get("/api/health")
    assert r.status_code == 200


def test_add_api_health_endpoint__response_body_has_required_fields(client):
    # AC: The response body is a JSON object containing `status` (string: "ok" or "degraded"),
    # `uptime_seconds` (float), and `database` (object with `reachable` boolean and `path` string).
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert "status" in body
    assert body["status"] in ("ok", "degraded")
    assert "uptime_seconds" in body
    assert isinstance(body["uptime_seconds"], (int, float))
    assert "database" in body
    assert isinstance(body["database"], dict)
    assert "reachable" in body["database"]
    assert isinstance(body["database"]["reachable"], bool)
    assert "path" in body["database"]
    assert isinstance(body["database"]["path"], str)


def test_add_api_health_endpoint__uptime_seconds_increases(client):
    # AC: uptime_seconds is always a non-negative float that increases on successive calls.
    r1 = client.get("/api/health")
    assert r1.status_code == 200
    uptime1 = r1.json()["uptime_seconds"]
    assert uptime1 >= 0.0

    time.sleep(1)

    r2 = client.get("/api/health")
    assert r2.status_code == 200
    uptime2 = r2.json()["uptime_seconds"]
    assert uptime2 > uptime1, (
        f"uptime_seconds did not increase: first={uptime1}, second={uptime2}"
    )


def test_add_api_health_endpoint__degraded_when_db_unreachable(client):
    # AC: When the SQLite file is unreachable, database.reachable is false,
    # status is "degraded", and HTTP status is still 200.
    import os
    import subprocess

    db_path = "/Users/chaiwutchaianuchittrakul/commander/dashboard/dashboard.db"

    # Only run if db file exists; otherwise skip
    if not os.path.exists(db_path):
        pytest.skip("dashboard.db not found — cannot test unreachable path")

    try:
        subprocess.run(["chmod", "000", db_path], check=True)
        r = client.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "degraded"
        assert body["database"]["reachable"] is False
    finally:
        subprocess.run(["chmod", "644", db_path], check=True)


def test_add_api_health_endpoint__no_auth_required_and_fast(client):
    # AC: The endpoint does not require authentication and responds within 500 ms under normal load.
    import time as _time
    start = _time.monotonic()
    r = client.get("/api/health")
    elapsed_ms = (_time.monotonic() - start) * 1000
    # No auth challenge expected (not 401/403)
    assert r.status_code not in (401, 403)
    assert r.status_code == 200
    assert elapsed_ms < 500, f"Response took {elapsed_ms:.1f}ms, expected < 500ms"


def test_add_api_health_endpoint__pydantic_model_validated(client):
    # AC: The response is validated by a Pydantic v2 model (no untyped dicts returned).
    # Verified by checking the response conforms to the expected schema shape exactly —
    # if the route returned an untyped dict, extra or missing fields could sneak through.
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    # Strict key check: only expected top-level keys
    expected_keys = {"status", "uptime_seconds", "database"}
    assert set(body.keys()) == expected_keys, (
        f"Unexpected keys in response: {set(body.keys()) - expected_keys}"
    )
    # Strict key check on database sub-object
    expected_db_keys = {"reachable", "path"}
    assert set(body["database"].keys()) == expected_db_keys, (
        f"Unexpected keys in database: {set(body['database'].keys()) - expected_db_keys}"
    )
