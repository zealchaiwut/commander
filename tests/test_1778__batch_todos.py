"""Tests for issue #1778 AC3 and AC8 — GET /api/todos batch endpoint.

Coverage (AC8: unit/integration tests cover the new batch todos endpoint):
  - empty project list  → {} (no projects queried)
  - unknown slug         → {slug: []}
  - known slug           → {slug: [TodoOut, ...]}
  - partial hit          → correct data for known, [] for unknown
  - multiple known slugs → all returned correctly
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


def _make_engine(db_path: Path):
    engine = create_engine(f"sqlite:///{db_path}")
    ProjectTodo.__table__.create(bind=engine, checkfirst=True)
    return engine


@pytest.fixture()
def db_file(tmp_path):
    return tmp_path / "todos.db"


@pytest.fixture()
def bound_repo(db_file, tmp_path, monkeypatch):
    # Patch the JSON fallback store to a temp file so tests are isolated even
    # when COMMANDER_DISABLE_NEON=1 (which conftest sets, forcing the fallback path).
    fallback = tmp_path / "project_todos_store.json"
    monkeypatch.setattr(todo_repo, "_fallback_store_path", lambda: fallback)
    engine = _make_engine(db_file)
    monkeypatch.setattr(todo_repo, "_session_factory", sessionmaker(bind=engine))
    yield engine


@pytest.fixture()
def client(bound_repo):
    app = FastAPI()
    app.include_router(todos_router_mod.batch_router)
    return TestClient(app, raise_server_exceptions=False)


# ── AC3 / AC8 — batch todos endpoint ─────────────────────────────────────────

def test_batch_empty_projects_param_returns_empty_dict(client):
    r = client.get("/api/todos?projects=")
    assert r.status_code == 200
    assert r.json() == {}


def test_batch_no_projects_param_returns_empty_dict(client):
    r = client.get("/api/todos")
    assert r.status_code == 200
    assert r.json() == {}


def test_batch_unknown_slug_returns_slug_with_empty_list(client):
    r = client.get("/api/todos?projects=ghost-project")
    assert r.status_code == 200
    data = r.json()
    assert "ghost-project" in data
    assert data["ghost-project"] == []


def test_batch_known_slug_returns_todos(client):
    todo_repo.create_todo("alpha", "first task")
    r = client.get("/api/todos?projects=alpha")
    assert r.status_code == 200
    data = r.json()
    assert "alpha" in data
    assert len(data["alpha"]) == 1
    assert data["alpha"][0]["text"] == "first task"
    assert data["alpha"][0]["project"] == "alpha"


def test_batch_partial_hit_known_and_unknown(client):
    todo_repo.create_todo("beta", "do something")
    r = client.get("/api/todos?projects=beta,unknown-slug")
    assert r.status_code == 200
    data = r.json()
    assert "beta" in data
    assert len(data["beta"]) == 1
    assert data["beta"][0]["text"] == "do something"
    assert "unknown-slug" in data
    assert data["unknown-slug"] == []


def test_batch_multiple_known_slugs_all_returned(client):
    todo_repo.create_todo("p1", "task for p1")
    todo_repo.create_todo("p2", "task for p2")
    r = client.get("/api/todos?projects=p1,p2")
    assert r.status_code == 200
    data = r.json()
    assert len(data["p1"]) == 1
    assert data["p1"][0]["text"] == "task for p1"
    assert len(data["p2"]) == 1
    assert data["p2"][0]["text"] == "task for p2"


def test_batch_response_includes_attachments_field(client):
    todo_repo.create_todo("p3", "attach me")
    r = client.get("/api/todos?projects=p3")
    assert r.status_code == 200
    items = r.json()["p3"]
    assert len(items) == 1
    assert "attachments" in items[0]
    assert items[0]["attachments"] == []


def test_batch_result_has_standard_todo_fields(client):
    todo_repo.create_todo("p4", "check fields")
    r = client.get("/api/todos?projects=p4")
    assert r.status_code == 200
    item = r.json()["p4"][0]
    for field in ("id", "project", "text", "done", "position", "created_at", "updated_at", "attachments"):
        assert field in item, f"missing field: {field}"
    assert item["done"] is False
