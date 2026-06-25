"""Tests for issue #872: Validate non-empty text on project-todo create/PATCH."""
import os
import pytest
import httpx


BASE_URL = os.environ.get("UAT_BASE_URL") or (
    "http://localhost:" + os.environ.get("UAT_PORT", "")
)
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 "
        "to resolve UAT before pytest."
    )


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


@pytest.fixture
def project_slug(client):
    """Use the existing commander project for testing."""
    # The commander project already exists on the UAT server
    # The project slug is zealchaiwut-commander (dash, not slash)
    return "zealchaiwut-commander"


# --- Acceptance Criteria ---

def test_todo_validation__post_empty_string(client, project_slug):
    """AC: POST {\"text\": \"\"} should return HTTP 422 with validation error."""
    r = client.post(f"/api/projects/{project_slug}/todos", json={"text": ""})
    assert (
        r.status_code == 422
    ), f"Expected 422, got {r.status_code}: {r.text}"
    resp_data = r.json()
    assert "detail" in resp_data, f"No error detail in response: {resp_data}"
    # Check that the error mentions the 'text' field
    error_str = str(resp_data.get("detail", "")).lower()
    assert "text" in error_str, f"Error should mention 'text' field: {resp_data}"


def test_todo_validation__post_whitespace_only(client, project_slug):
    """AC: POST whitespace-only text should return HTTP 422."""
    r = client.post(f"/api/projects/{project_slug}/todos", json={"text": "   "})
    assert (
        r.status_code == 422
    ), f"Expected 422, got {r.status_code}: {r.text}"
    resp_data = r.json()
    assert "detail" in resp_data, f"No error detail in response: {resp_data}"
    error_str = str(resp_data.get("detail", "")).lower()
    assert "text" in error_str, f"Error should mention 'text' field: {resp_data}"


def test_todo_validation__patch_empty_string(client, project_slug):
    """AC: PATCH empty text on existing todo should return HTTP 422."""
    # First create a valid todo
    create_resp = client.post(
        f"/api/projects/{project_slug}/todos", json={"text": "Original text"}
    )
    assert create_resp.status_code in (
        200,
        201,
    ), f"Failed to create todo: {create_resp.text}"
    todo_id = create_resp.json()["id"]

    # Now try to PATCH with empty text
    r = client.patch(
        f"/api/projects/{project_slug}/todos/{todo_id}", json={"text": ""}
    )
    assert (
        r.status_code == 422
    ), f"Expected 422, got {r.status_code}: {r.text}"
    resp_data = r.json()
    assert "detail" in resp_data, f"No error detail in response: {resp_data}"
    error_str = str(resp_data.get("detail", "")).lower()
    assert "text" in error_str, f"Error should mention 'text' field: {resp_data}"


def test_todo_validation__patch_whitespace_only(client, project_slug):
    """AC: PATCH whitespace-only text on existing todo should return HTTP 422."""
    # First create a valid todo
    create_resp = client.post(
        f"/api/projects/{project_slug}/todos", json={"text": "Original text"}
    )
    assert create_resp.status_code in (
        200,
        201,
    ), f"Failed to create todo: {create_resp.text}"
    todo_id = create_resp.json()["id"]

    # Now try to PATCH with whitespace-only text
    r = client.patch(
        f"/api/projects/{project_slug}/todos/{todo_id}", json={"text": "   "}
    )
    assert (
        r.status_code == 422
    ), f"Expected 422, got {r.status_code}: {r.text}"
    resp_data = r.json()
    assert "detail" in resp_data, f"No error detail in response: {resp_data}"
    error_str = str(resp_data.get("detail", "")).lower()
    assert "text" in error_str, f"Error should mention 'text' field: {resp_data}"


def test_todo_validation__post_valid_text(client, project_slug):
    """AC: Valid non-empty text is accepted and todo is created successfully."""
    r = client.post(f"/api/projects/{project_slug}/todos", json={"text": "Buy milk"})
    assert r.status_code in (
        200,
        201,
    ), f"Expected 200/201, got {r.status_code}: {r.text}"
    todo = r.json()
    assert todo["text"] == "Buy milk", f"Text mismatch: {todo}"

    # Verify it appears in GET list
    list_resp = client.get(f"/api/projects/{project_slug}/todos")
    assert list_resp.status_code == 200, f"Failed to list todos: {list_resp.text}"
    todos = list_resp.json()
    assert any(
        t["text"] == "Buy milk" for t in todos
    ), f"Todo not found in list: {todos}"


def test_todo_validation__post_text_with_spaces(client, project_slug):
    """AC: Text with leading/trailing spaces and non-whitespace content is accepted."""
    r = client.post(
        f"/api/projects/{project_slug}/todos",
        json={"text": "  hello world  "},
    )
    assert r.status_code in (
        200,
        201,
    ), f"Expected 200/201, got {r.status_code}: {r.text}"
    todo = r.json()
    # The validator strips for checking, but the actual text may be stored as-is
    assert "hello world" in todo["text"], f"Text should contain content: {todo}"


def test_todo_validation__patch_valid_text(client, project_slug):
    """AC: PATCH with valid non-empty text updates successfully."""
    # Create initial todo
    create_resp = client.post(
        f"/api/projects/{project_slug}/todos", json={"text": "Original"}
    )
    assert create_resp.status_code in (
        200,
        201,
    ), f"Failed to create todo: {create_resp.text}"
    todo_id = create_resp.json()["id"]

    # Update with valid text
    r = client.patch(
        f"/api/projects/{project_slug}/todos/{todo_id}",
        json={"text": "Updated text"},
    )
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    updated = r.json()
    assert updated["text"] == "Updated text", f"Text not updated: {updated}"


def test_todo_validation__error_message_references_text(client, project_slug):
    """AC: The 422 response contains a validation error referencing 'text' field."""
    r = client.post(f"/api/projects/{project_slug}/todos", json={"text": ""})
    assert (
        r.status_code == 422
    ), f"Expected 422, got {r.status_code}: {r.text}"
    resp_data = r.json()

    # Check the error detail mentions 'text'
    if "detail" in resp_data:
        error_detail = str(resp_data["detail"]).lower()
        assert (
            "text" in error_detail
        ), f"Error detail should mention 'text': {resp_data}"
