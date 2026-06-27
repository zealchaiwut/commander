"""Tests for issue #872: Validate non-empty text on project-todo create/PATCH (runs against UAT)"""
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

PROJECT = "commander"


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


@pytest.fixture
def test_todo(client):
    """Create a test todo to operate on for PATCH tests."""
    r = client.post(f"/api/projects/{PROJECT}/todos", json={"text": "Original text"})
    if r.status_code in (200, 201):
        return r.json()["id"]
    pytest.skip(f"Could not create test todo: {r.status_code}")


# --- Acceptance Criteria ---

def test_todo_validation__empty_string_on_create(client):
    # AC: `TodoCreate.text` rejects empty string (`""`) with HTTP 422 on `POST /api/projects/{slug}/todos`
    r = client.post(f"/api/projects/{PROJECT}/todos", json={"text": ""})
    assert r.status_code == 422, f"Expected 422, got {r.status_code}: {r.text}"


def test_todo_validation__whitespace_only_on_create(client):
    # AC: `TodoCreate.text` rejects whitespace-only strings (e.g. `"   "`) with HTTP 422
    r = client.post(f"/api/projects/{PROJECT}/todos", json={"text": "   "})
    assert r.status_code == 422, f"Expected 422, got {r.status_code}: {r.text}"


def test_todo_validation__empty_string_on_patch(client, test_todo):
    # AC: `TodoUpdate.text` rejects empty string (`""`) with HTTP 422 on `PATCH /api/projects/{slug}/todos/{id}`
    r = client.patch(f"/api/projects/{PROJECT}/todos/{test_todo}", json={"text": ""})
    assert r.status_code == 422, f"Expected 422, got {r.status_code}: {r.text}"


def test_todo_validation__whitespace_only_on_patch(client, test_todo):
    # AC: `TodoUpdate.text` rejects whitespace-only strings with HTTP 422 on `PATCH /api/projects/{slug}/todos/{id}`
    r = client.patch(f"/api/projects/{PROJECT}/todos/{test_todo}", json={"text": "   "})
    assert r.status_code == 422, f"Expected 422, got {r.status_code}: {r.text}"


def test_todo_validation__valid_text_on_create(client):
    # AC: Valid non-empty text (including text with leading/trailing spaces that has non-whitespace content) is accepted and the todo is created/updated successfully
    r = client.post(f"/api/projects/{PROJECT}/todos", json={"text": " Buy milk "})
    assert r.status_code in (200, 201), f"Expected 200/201, got {r.status_code}: {r.text}"
    data = r.json()
    assert data["text"] == " Buy milk ", "Text should be preserved as-is"
    # Verify it appears in the list
    r = client.get(f"/api/projects/{PROJECT}/todos")
    assert r.status_code == 200
    todos = r.json()
    assert any(t["text"] == " Buy milk " for t in todos), "Created todo should appear in list"


def test_todo_validation__valid_text_on_patch(client, test_todo):
    # AC: Valid non-empty text is accepted and the todo is updated successfully
    r = client.patch(f"/api/projects/{PROJECT}/todos/{test_todo}", json={"text": " Updated text "})
    assert r.status_code in (200, 201), f"Expected 200/201, got {r.status_code}: {r.text}"
    data = r.json()
    assert data["text"] == " Updated text ", "Text should be preserved as-is"
    # Verify the update persists
    r = client.get(f"/api/projects/{PROJECT}/todos/{test_todo}")
    if r.status_code == 200:
        assert r.json()["text"] == " Updated text ", "Updated text should persist"


def test_todo_validation__422_response_mentions_text_field(client):
    # AC: The 422 response body contains a validation error message referencing the `text` field
    r = client.post(f"/api/projects/{PROJECT}/todos", json={"text": ""})
    assert r.status_code == 422
    response_body = r.json()
    # Pydantic's 422 response includes a 'detail' array with field information
    assert "detail" in response_body, f"422 response should have 'detail': {response_body}"
    details = response_body.get("detail", [])
    assert isinstance(details, list) and len(details) > 0, "422 detail should be a non-empty list"
    # Check that at least one detail mentions 'text'
    text_mentioned = any("text" in str(detail).lower() for detail in details)
    assert text_mentioned, f"422 response should mention 'text' field. Details: {details}"
