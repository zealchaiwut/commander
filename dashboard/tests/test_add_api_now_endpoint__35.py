"""Tests for issue #35: Add GET /api/now endpoint returning {"now": "<UTC ISO-8601 timestamp>"}"""
import pytest
import httpx
from datetime import datetime, timezone


BASE_URL = "http://localhost:8000"


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# --- Acceptance Criteria ---

def test_now_endpoint_exists_and_returns_200(client):
    # AC1: A new GET /api/now endpoint exists in server.py.
    # AC2: The endpoint returns HTTP status 200.
    r = client.get("/api/now")
    assert r.status_code == 200


def test_now_response_body_has_now_key(client):
    # AC3: The response body is a JSON object with a "now" key.
    r = client.get("/api/now")
    assert r.status_code == 200
    body = r.json()
    assert "now" in body, f"Expected 'now' key in response, got {body}"


def test_now_response_is_valid_iso8601_utc(client):
    # AC3: The "now" value is a valid UTC ISO-8601 timestamp string.
    r = client.get("/api/now")
    assert r.status_code == 200
    now_str = r.json()["now"]
    # Must be parseable as an ISO-8601 datetime
    try:
        parsed = datetime.fromisoformat(now_str)
    except ValueError as e:
        pytest.fail(f"'now' value is not a valid ISO-8601 string: {now_str!r} ({e})")
    # Must include UTC timezone info (offset +00:00)
    assert parsed.tzinfo is not None, f"Timestamp has no timezone info: {now_str!r}"
    assert parsed.utcoffset().total_seconds() == 0, (
        f"Expected UTC offset (+00:00), got {parsed.utcoffset()} in {now_str!r}"
    )


def test_now_timestamp_is_close_to_current_time(client):
    # AC3: The returned timestamp is close to the actual current UTC time (within 5 seconds).
    before = datetime.now(timezone.utc)
    r = client.get("/api/now")
    after = datetime.now(timezone.utc)

    assert r.status_code == 200
    now_str = r.json()["now"]
    parsed = datetime.fromisoformat(now_str)

    assert before <= parsed <= after, (
        f"Returned timestamp {now_str!r} is not between {before.isoformat()!r} and {after.isoformat()!r}"
    )


def test_now_content_type_is_application_json(client):
    # AC3: Content-Type is application/json.
    r = client.get("/api/now")
    assert r.status_code == 200
    assert "application/json" in r.headers.get("content-type", ""), (
        f"Expected application/json content-type, got {r.headers.get('content-type')}"
    )


def test_now_no_auth_required(client):
    # AC4: No authentication or rate limiting is required.
    r = client.get("/api/now")
    assert r.status_code not in (401, 403), (
        f"Expected no auth required but got status {r.status_code}"
    )
    assert r.status_code == 200
