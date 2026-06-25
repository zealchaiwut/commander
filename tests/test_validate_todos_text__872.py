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


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


@pytest.fixture
def project(client):
    """Create a test project for todos."""
    # Use a fixed slug for all tests in this run
    return "test-todos-validate-872"


# --- Acceptance Criteria ---

def test_validate_todos_text__post_empty_string_rejects_with_422(client, project):
    # AC: `TodoCreate.text` rejects empty string ("") with HTTP 422
    r = client.post(f"/api/projects/{project}/todos", json={"text": ""})
    assert r.status_code == 422
    assert "text" in r.text.lower()


def test_validate_todos_text__post_whitespace_only_rejects_with_422(client, project):
    # AC: `TodoCreate.text` rejects whitespace-only strings (e.g. "   ") with HTTP 422
    r = client.post(f"/api/projects/{project}/todos", json={"text": "   "})
    assert r.status_code == 422
    assert "text" in r.text.lower()


def test_validate_todos_text__patch_empty_string_rejects_with_422(client, project):
    # AC: `TodoUpdate.text` rejects empty string ("") with HTTP 422
    # First create a valid todo
    create_r = client.post(f"/api/projects/{project}/todos", json={"text": "Initial text"})
    assert create_r.status_code == 201
    todo_id = create_r.json()["id"]

    # Try to update with empty string
    r = client.patch(f"/api/projects/{project}/todos/{todo_id}", json={"text": ""})
    assert r.status_code == 422
    assert "text" in r.text.lower()


def test_validate_todos_text__patch_whitespace_only_rejects_with_422(client, project):
    # AC: `TodoUpdate.text` rejects whitespace-only strings with HTTP 422
    # First create a valid todo
    create_r = client.post(f"/api/projects/{project}/todos", json={"text": "Initial text"})
    assert create_r.status_code == 201
    todo_id = create_r.json()["id"]

    # Try to update with whitespace only
    r = client.patch(f"/api/projects/{project}/todos/{todo_id}", json={"text": "   "})
    assert r.status_code == 422
    assert "text" in r.text.lower()


def test_validate_todos_text__valid_text_accepted_in_create(client, project):
    # AC: Valid non-empty text (including text with leading/trailing spaces that has
    #     non-whitespace content) is accepted and the todo is created successfully
    r = client.post(f"/api/projects/{project}/todos", json={"text": "Buy milk"})
    assert r.status_code == 201
    data = r.json()
    assert data["text"] == "Buy milk"

    # Verify it appears in the list
    list_r = client.get(f"/api/projects/{project}/todos")
    assert list_r.status_code == 200
    todos = list_r.json()
    assert any(t["id"] == data["id"] for t in todos)


def test_validate_todos_text__valid_text_with_spaces_accepted(client, project):
    # AC: Valid non-empty text with leading/trailing spaces
    r = client.post(f"/api/projects/{project}/todos", json={"text": "  padded text  "})
    assert r.status_code == 201
    data = r.json()
    # Text may be stored as-is or stripped; at minimum, the creation succeeds
    assert data["id"]


def test_validate_todos_text__valid_text_accepted_in_patch(client, project):
    # AC: Valid non-empty text is accepted in PATCH
    # First create a todo
    create_r = client.post(f"/api/projects/{project}/todos", json={"text": "Initial"})
    assert create_r.status_code == 201
    todo_id = create_r.json()["id"]

    # Update with new text
    r = client.patch(f"/api/projects/{project}/todos/{todo_id}", json={"text": "Updated text"})
    assert r.status_code == 200
    data = r.json()
    assert data["text"] == "Updated text"


def test_validate_todos_text__422_response_references_text_field(client, project):
    # AC: The 422 response body contains a validation error message referencing the `text` field
    r = client.post(f"/api/projects/{project}/todos", json={"text": ""})
    assert r.status_code == 422
    body = r.json()
    # Pydantic validation errors include "detail" key with error list
    assert "detail" in body
    error_str = str(body).lower()
    assert "text" in error_str
