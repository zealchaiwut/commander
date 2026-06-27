"""Tests for issue #872: Validate non-empty text on project-todo create/PATCH."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import services.sprint_manager.todo_repo as todo_repo  # noqa: E402
from services.sprint_manager.models import ProjectTodo  # noqa: E402
from routers import todos as todos_router_mod  # noqa: E402


@pytest.fixture()
def bound_repo(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'todos.db'}")
    ProjectTodo.__table__.create(bind=engine, checkfirst=True)
    monkeypatch.setattr(todo_repo, "_session_factory", sessionmaker(bind=engine))
    yield engine


@pytest.fixture()
def client(bound_repo):
    app = FastAPI()
    app.include_router(todos_router_mod.router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def project():
    return "test-todos-validate-872"


@pytest.fixture()
def existing_todo(client, project):
    r = client.post(f"/api/projects/{project}/todos", json={"text": "Initial text"})
    assert r.status_code == 201
    return r.json()


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


def test_validate_todos_text__patch_empty_string_rejects_with_422(client, project, existing_todo):
    # AC: `TodoUpdate.text` rejects empty string ("") with HTTP 422
    todo_id = existing_todo["id"]
    r = client.patch(f"/api/projects/{project}/todos/{todo_id}", json={"text": ""})
    assert r.status_code == 422
    assert "text" in r.text.lower()


def test_validate_todos_text__patch_whitespace_only_rejects_with_422(client, project, existing_todo):
    # AC: `TodoUpdate.text` rejects whitespace-only strings with HTTP 422
    todo_id = existing_todo["id"]
    r = client.patch(f"/api/projects/{project}/todos/{todo_id}", json={"text": "   "})
    assert r.status_code == 422
    assert "text" in r.text.lower()


def test_validate_todos_text__valid_text_accepted_in_create(client, project):
    # AC: Valid non-empty text is accepted and the todo is created successfully
    r = client.post(f"/api/projects/{project}/todos", json={"text": "Buy milk"})
    assert r.status_code == 201
    data = r.json()
    assert data["text"] == "Buy milk"

    list_r = client.get(f"/api/projects/{project}/todos")
    assert list_r.status_code == 200
    todos = list_r.json()
    assert any(t["id"] == data["id"] for t in todos)


def test_validate_todos_text__valid_text_with_spaces_accepted(client, project):
    # AC: Valid non-empty text with leading/trailing spaces
    r = client.post(f"/api/projects/{project}/todos", json={"text": "  padded text  "})
    assert r.status_code == 201
    data = r.json()
    assert data["id"]


def test_validate_todos_text__valid_text_accepted_in_patch(client, project, existing_todo):
    # AC: Valid non-empty text is accepted in PATCH
    todo_id = existing_todo["id"]
    r = client.patch(f"/api/projects/{project}/todos/{todo_id}", json={"text": "Updated text"})
    assert r.status_code == 200
    data = r.json()
    assert data["text"] == "Updated text"


def test_validate_todos_text__422_response_references_text_field(client, project):
    # AC: The 422 response body contains a validation error message referencing the `text` field
    r = client.post(f"/api/projects/{project}/todos", json={"text": ""})
    assert r.status_code == 422
    body = r.json()
    assert "detail" in body
    error_str = str(body).lower()
    assert "text" in error_str
