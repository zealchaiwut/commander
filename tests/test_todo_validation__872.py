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
def project_slug():
    return "zealchaiwut-commander"


@pytest.fixture()
def existing_todo(client, project_slug):
    r = client.post(f"/api/projects/{project_slug}/todos", json={"text": "Original text"})
    assert r.status_code == 201
    return r.json()


def test_todo_validation__post_empty_string(client, project_slug):
    """AC: POST {"text": ""} should return HTTP 422 with validation error."""
    r = client.post(f"/api/projects/{project_slug}/todos", json={"text": ""})
    assert r.status_code == 422
    resp_data = r.json()
    assert "detail" in resp_data
    error_str = str(resp_data.get("detail", "")).lower()
    assert "text" in error_str


def test_todo_validation__post_whitespace_only(client, project_slug):
    """AC: POST whitespace-only text should return HTTP 422."""
    r = client.post(f"/api/projects/{project_slug}/todos", json={"text": "   "})
    assert r.status_code == 422
    resp_data = r.json()
    assert "detail" in resp_data
    error_str = str(resp_data.get("detail", "")).lower()
    assert "text" in error_str


def test_todo_validation__patch_empty_string(client, project_slug, existing_todo):
    """AC: PATCH empty text on existing todo should return HTTP 422."""
    todo_id = existing_todo["id"]
    r = client.patch(f"/api/projects/{project_slug}/todos/{todo_id}", json={"text": ""})
    assert r.status_code == 422
    resp_data = r.json()
    assert "detail" in resp_data
    error_str = str(resp_data.get("detail", "")).lower()
    assert "text" in error_str


def test_todo_validation__patch_whitespace_only(client, project_slug, existing_todo):
    """AC: PATCH whitespace-only text on existing todo should return HTTP 422."""
    todo_id = existing_todo["id"]
    r = client.patch(f"/api/projects/{project_slug}/todos/{todo_id}", json={"text": "   "})
    assert r.status_code == 422
    resp_data = r.json()
    assert "detail" in resp_data
    error_str = str(resp_data.get("detail", "")).lower()
    assert "text" in error_str


def test_todo_validation__post_valid_text(client, project_slug):
    """AC: Valid non-empty text is accepted and todo is created successfully."""
    r = client.post(f"/api/projects/{project_slug}/todos", json={"text": "Buy milk"})
    assert r.status_code == 201
    todo = r.json()
    assert todo["text"] == "Buy milk"

    list_resp = client.get(f"/api/projects/{project_slug}/todos")
    assert list_resp.status_code == 200
    todos = list_resp.json()
    assert any(t["text"] == "Buy milk" for t in todos)


def test_todo_validation__post_text_with_spaces(client, project_slug):
    """AC: Text with leading/trailing spaces and non-whitespace content is accepted."""
    r = client.post(f"/api/projects/{project_slug}/todos", json={"text": "  hello world  "})
    assert r.status_code == 201
    todo = r.json()
    assert "hello world" in todo["text"]


def test_todo_validation__patch_valid_text(client, project_slug, existing_todo):
    """AC: PATCH with valid non-empty text updates successfully."""
    todo_id = existing_todo["id"]
    r = client.patch(f"/api/projects/{project_slug}/todos/{todo_id}", json={"text": "Updated text"})
    assert r.status_code == 200
    updated = r.json()
    assert updated["text"] == "Updated text"


def test_todo_validation__error_message_references_text(client, project_slug):
    """AC: The 422 response contains a validation error referencing 'text' field."""
    r = client.post(f"/api/projects/{project_slug}/todos", json={"text": ""})
    assert r.status_code == 422
    resp_data = r.json()
    if "detail" in resp_data:
        error_detail = str(resp_data["detail"]).lower()
        assert "text" in error_detail
