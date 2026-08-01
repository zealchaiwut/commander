"""Tests for issue #2070: POST /api/tickets/create accepts JSON body.

AC1: POST /api/tickets/create accepts JSON body with typed model (CreateTicketBody).
AC2: Multipart/form-data path continues to work for backward compatibility.
AC3: JSON path is covered by response_model (TicketCreateResponse).
AC4: Behavioral tests for JSON path; regression test for multipart path.
AC5: Verify both code paths execute successfully without breaking changes.
"""
import os
import pytest
import httpx


# Resolved from UAT .env at runtime; see tester skill Step 0.
# Default fallback kept only as last-resort if env vars not exported.
BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "8001")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=30.0) as c:
        yield c


# ── Acceptance Criteria ────────────────────────────────────────────────────────

def test_tickets_create_json_accepts_typed_body(client):
    """AC1: POST /api/tickets/create accepts JSON body with typed Pydantic model.

    Tests that the endpoint accepts application/json content type and returns
    201 with response_model fields (number, url).
    """
    payload = {
        "title": "AC1 test: JSON body accepted",
        "body": "Testing JSON path acceptance",
        "project": "zealchaiwut/commander",
        "sprint_label": "",
        "extra_labels": [],
        "draft_id": "",
        "milestone": "",
    }

    r = client.post("/api/tickets/create", json=payload)

    # Should return 201 Created
    assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.text}"

    # Should have response_model fields
    data = r.json()
    assert "number" in data, "Response missing 'number' field"
    assert "url" in data, "Response missing 'url' field"
    assert isinstance(data["number"], int), "number should be int"
    assert isinstance(data["url"], str), "url should be str"
    assert data["url"].startswith("https://github.com/"), "url should be GitHub issue URL"


def test_tickets_create_json_validates_required_title(client):
    """AC4: JSON path validates required fields (title) and returns 400 for empty title."""
    payload = {
        "title": "",  # Empty title should fail
        "body": "Test body",
        "project": "zealchaiwut/commander",
    }

    r = client.post("/api/tickets/create", json=payload)

    # Should reject empty required field with 400
    assert r.status_code == 400, f"Expected 400 for empty title, got {r.status_code}: {r.text}"
    assert "Title is required" in r.text or "required" in r.text.lower()


def test_tickets_create_json_with_sprint_label(client):
    """AC1: JSON path correctly parses sprint_label field in typed model."""
    payload = {
        "title": "AC1 test: sprint label parsed",
        "body": "Testing sprint label in JSON",
        "project": "zealchaiwut/commander",
        "sprint_label": "sprint-1009.1",
        "extra_labels": [],
        "draft_id": "",
        "milestone": "",
    }

    r = client.post("/api/tickets/create", json=payload)
    assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.text}"

    data = r.json()
    assert isinstance(data["number"], int)
    assert data["url"].startswith("https://github.com/")


def test_tickets_create_json_with_extra_labels(client):
    """AC1: JSON path correctly parses extra_labels array."""
    payload = {
        "title": "AC1 test: extra labels parsed",
        "body": "Testing extra_labels in JSON",
        "project": "zealchaiwut/commander",
        "extra_labels": ["bug", "enhancement"],
        "draft_id": "",
        "sprint_label": "",
        "milestone": "",
    }

    r = client.post("/api/tickets/create", json=payload)
    assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.text}"

    data = r.json()
    assert isinstance(data["number"], int)


def test_tickets_create_multipart_backward_compat(client):
    """AC2: Multipart/form-data path continues to work for backward compatibility."""
    # Use form data (multipart) instead of JSON
    form_data = {
        "title": "AC2 test: multipart backward compat",
        "body": "Testing multipart form path",
        "project": "zealchaiwut/commander",
        "sprint_label": "sprint-1009.1",
    }

    r = client.post("/api/tickets/create", data=form_data)

    # Should still work for backward compatibility
    assert r.status_code == 201, f"Expected 201 for multipart, got {r.status_code}: {r.text}"

    data = r.json()
    assert "number" in data, "Response missing 'number' field"
    assert "url" in data, "Response missing 'url' field"
    assert isinstance(data["number"], int)


def test_tickets_create_json_malformed_body_rejected(client):
    """AC4: Malformed JSON is rejected with 400."""
    # Send invalid JSON
    r = client.post(
        "/api/tickets/create",
        content=b"{ invalid json }",
        headers={"Content-Type": "application/json"},
    )

    # Should reject invalid JSON
    assert r.status_code == 400, f"Expected 400 for invalid JSON, got {r.status_code}: {r.text}"


def test_tickets_create_json_missing_title_validation(client):
    """AC4: JSON path rejects missing title field with 422 (Pydantic validation)."""
    payload = {
        "body": "Test body without title",
        "project": "zealchaiwut/commander",
    }

    r = client.post("/api/tickets/create", json=payload)

    # Pydantic should reject missing required field
    assert r.status_code in (400, 422), f"Expected 400/422 for missing title, got {r.status_code}: {r.text}"
