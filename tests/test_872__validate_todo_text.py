"""Tests for issue #872 — validate non-empty text on project-todo create/PATCH.

AC coverage:
  AC1 — TodoCreate.text rejects empty string ("") with HTTP 422 on POST
  AC2 — TodoCreate.text rejects whitespace-only strings (e.g. "   ") with HTTP 422 on POST
  AC3 — TodoUpdate.text rejects empty string ("") with HTTP 422 on PATCH
  AC4 — TodoUpdate.text rejects whitespace-only strings with HTTP 422 on PATCH
  AC5 — Valid non-empty text (including text with leading/trailing spaces) is accepted
  AC6 — 422 response body contains a validation error message referencing the text field
"""
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
def existing_todo(client):
    r = client.post("/api/projects/p/todos", json={"text": "original"})
    assert r.status_code == 201
    return r.json()


# ── AC1: POST rejects empty string ────────────────────────────────────────────

def test_ac1_post_rejects_empty_string(client):
    r = client.post("/api/projects/p/todos", json={"text": ""})
    assert r.status_code == 422


# ── AC2: POST rejects whitespace-only strings ─────────────────────────────────

def test_ac2_post_rejects_spaces_only(client):
    r = client.post("/api/projects/p/todos", json={"text": "   "})
    assert r.status_code == 422


def test_ac2_post_rejects_tab_only(client):
    r = client.post("/api/projects/p/todos", json={"text": "\t\n"})
    assert r.status_code == 422


# ── AC3: PATCH rejects empty string on text field ─────────────────────────────

def test_ac3_patch_rejects_empty_string(client, existing_todo):
    tid = existing_todo["id"]
    r = client.patch(f"/api/projects/p/todos/{tid}", json={"text": ""})
    assert r.status_code == 422


# ── AC4: PATCH rejects whitespace-only strings ────────────────────────────────

def test_ac4_patch_rejects_spaces_only(client, existing_todo):
    tid = existing_todo["id"]
    r = client.patch(f"/api/projects/p/todos/{tid}", json={"text": "   "})
    assert r.status_code == 422


def test_ac4_patch_rejects_newline_only(client, existing_todo):
    tid = existing_todo["id"]
    r = client.patch(f"/api/projects/p/todos/{tid}", json={"text": "\n"})
    assert r.status_code == 422


# ── AC5: Valid text is accepted ────────────────────────────────────────────────

def test_ac5_post_accepts_valid_text(client):
    r = client.post("/api/projects/p/todos", json={"text": "Buy milk"})
    assert r.status_code == 201
    assert r.json()["text"] == "Buy milk"


def test_ac5_post_accepts_text_with_leading_trailing_spaces(client):
    r = client.post("/api/projects/p/todos", json={"text": "  Buy milk  "})
    assert r.status_code == 201


def test_ac5_patch_accepts_valid_text(client, existing_todo):
    tid = existing_todo["id"]
    r = client.patch(f"/api/projects/p/todos/{tid}", json={"text": "Updated text"})
    assert r.status_code == 200
    assert r.json()["text"] == "Updated text"


def test_ac5_patch_omitting_text_still_works(client, existing_todo):
    """PATCH with no text field (only done) must not trigger text validation."""
    tid = existing_todo["id"]
    r = client.patch(f"/api/projects/p/todos/{tid}", json={"done": True})
    assert r.status_code == 200
    assert r.json()["done"] is True


# ── AC6: 422 body references the text field ───────────────────────────────────

def test_ac6_post_422_references_text_field(client):
    r = client.post("/api/projects/p/todos", json={"text": ""})
    assert r.status_code == 422
    body = r.json()
    error_text = str(body).lower()
    assert "text" in error_text


def test_ac6_patch_422_references_text_field(client, existing_todo):
    tid = existing_todo["id"]
    r = client.patch(f"/api/projects/p/todos/{tid}", json={"text": "   "})
    assert r.status_code == 422
    body = r.json()
    error_text = str(body).lower()
    assert "text" in error_text
