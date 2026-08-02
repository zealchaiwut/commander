"""Tests for issue #2119: JSON create-ticket 422 leaks raw pydantic ValidationError repr (runs against UAT)"""
import os
import pytest
import httpx


# Resolved from UAT .env at runtime; see tester skill Step 0.
# Default kept only as a last-resort fallback if BASE_URL not exported.
BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# --- Acceptance Criteria ---

def test_json_validation_error_format__returns_formatted_detail(client):
    # AC: JSON validation failure returns a clean, formatted detail string instead of raw Pydantic repr
    # Send invalid JSON body (title is required, send wrong type)
    r = client.post(
        "/api/tickets/create",
        json={"title": 123, "body": "test body"},  # title must be str, not int
        headers={"Content-Type": "application/json"}
    )
    assert r.status_code == 422
    detail = r.json().get("detail", "")
    # Verify the detail is formatted, not raw Pydantic repr
    # Raw repr would contain things like "ValidationError" or "model fields"
    # Formatted version should start with "Invalid ticket body:" and contain field:msg pairs
    assert "Invalid ticket body:" in detail
    assert "title" in detail.lower()
    # Verify it does NOT contain raw Pydantic validation error markers that would leak implementation
    assert "ValidationError" not in detail
    assert "model_fields" not in detail


def test_json_validation_error_format__multiple_field_errors(client):
    # AC: Multiple validation errors are formatted consistently with field location and message
    r = client.post(
        "/api/tickets/create",
        json={"title": 123, "body": 456, "project": []},  # multiple type mismatches
        headers={"Content-Type": "application/json"}
    )
    assert r.status_code == 422
    detail = r.json().get("detail", "")
    # Verify it's formatted with the preamble
    assert "Invalid ticket body:" in detail
    # Multiple errors should be separated by semicolons
    assert ";" in detail
    # Verify no raw Pydantic repr leak
    assert "ValidationError" not in detail


def test_json_validation_error_format__missing_required_field(client):
    # AC: Missing required field validation error is formatted cleanly
    r = client.post(
        "/api/tickets/create",
        json={"body": "test"},  # missing required 'title' field
        headers={"Content-Type": "application/json"}
    )
    assert r.status_code == 422
    detail = r.json().get("detail", "")
    assert "Invalid ticket body:" in detail
    assert "title" in detail.lower()
    assert "ValidationError" not in detail
