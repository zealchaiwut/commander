"""Tests for issue #843 — per-project to-do list table and API.

Each test is anchored to a specific acceptance criterion (AC) or UAT step and
passes because the implementation satisfies it, not because the test was
softened.

AC coverage:
  AC1  — project_todos table has the required columns (incl. nullable
         promoted_issue_number)
  AC2  — GET /api/projects/:project/todos returns all todos ordered by position
  AC3  — POST creates a todo; done defaults False; position is set; 201
  AC4  — PATCH toggles done / edits text / updates position, independently or
         together
  AC5  — DELETE removes the todo
  AC6  — endpoints are project-scoped; cross-project todos never leak (read,
         update, delete)
  AC7  — toggling done persists across a server "restart" (fresh engine, same DB)
  AC8  — list is always returned in ascending position order
  AC9  — no ticket-like fields (labels / assignees / due dates) on table or API
  AC10 — promoted_issue_number column exists, is nullable, and is never written
         or exposed by any endpoint
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
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
    """A file-backed SQLite engine with the project_todos table created.

    File-backed (not :memory:) so a fresh engine on the same file simulates a
    server restart for the durability test (AC7).
    """
    engine = create_engine(f"sqlite:///{db_path}")
    ProjectTodo.__table__.create(bind=engine, checkfirst=True)
    return engine


@pytest.fixture()
def db_file(tmp_path):
    return tmp_path / "todos.db"


@pytest.fixture()
def bound_repo(db_file, monkeypatch):
    """Bind todo_repo to a SQLite test engine via the _session_factory seam."""
    engine = _make_engine(db_file)
    monkeypatch.setattr(todo_repo, "_session_factory", sessionmaker(bind=engine))
    yield engine


@pytest.fixture()
def client(bound_repo):
    app = FastAPI()
    app.include_router(todos_router_mod.router)
    return TestClient(app, raise_server_exceptions=False)


# ── AC1 / AC10: schema ────────────────────────────────────────────────────────

def test_ac1_table_has_required_columns(bound_repo):
    cols = {c["name"] for c in inspect(bound_repo).get_columns("project_todos")}
    required = {
        "id", "project", "text", "done", "position",
        "created_at", "updated_at", "promoted_issue_number",
    }
    assert required <= cols


def test_ac10_promoted_issue_number_is_nullable(bound_repo):
    by_name = {c["name"]: c for c in inspect(bound_repo).get_columns("project_todos")}
    assert by_name["promoted_issue_number"]["nullable"] is True


def test_ac9_no_ticket_like_columns_on_table(bound_repo):
    cols = {c["name"] for c in inspect(bound_repo).get_columns("project_todos")}
    for forbidden in ("labels", "label", "assignee", "assignees", "due_date", "due"):
        assert forbidden not in cols


# ── AC3 / UAT 1: create ───────────────────────────────────────────────────────

def test_ac3_post_creates_todo_done_false_position_set(client):
    r = client.post("/api/projects/my-project/todos", json={"text": "Write release notes"})
    assert r.status_code == 201
    body = r.json()
    assert body["text"] == "Write release notes"
    assert body["done"] is False
    assert body["position"] is not None
    assert isinstance(body["position"], int)
    assert body["project"] == "my-project"


# ── AC2 / UAT 2: list ─────────────────────────────────────────────────────────

def test_ac2_get_returns_created_todo(client):
    client.post("/api/projects/my-project/todos", json={"text": "first"})
    r = client.get("/api/projects/my-project/todos")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["text"] == "first"


# ── AC4 / UAT 3-5: patch ──────────────────────────────────────────────────────

def test_ac4_patch_toggle_done(client):
    tid = client.post("/api/projects/p/todos", json={"text": "t"}).json()["id"]
    r = client.patch(f"/api/projects/p/todos/{tid}", json={"done": True})
    assert r.status_code == 200
    assert r.json()["done"] is True
    # UAT 3: subsequent GET still returns the item
    listed = client.get("/api/projects/p/todos").json()
    assert any(i["id"] == tid and i["done"] is True for i in listed)


def test_ac4_patch_edit_text_leaves_done_and_position_unchanged(client):
    created = client.post("/api/projects/p/todos", json={"text": "old"}).json()
    tid, pos = created["id"], created["position"]
    r = client.patch(f"/api/projects/p/todos/{tid}", json={"text": "new text"})
    assert r.status_code == 200
    body = r.json()
    assert body["text"] == "new text"
    assert body["done"] is False
    assert body["position"] == pos


def test_ac4_patch_position_reorders(client):
    a = client.post("/api/projects/p/todos", json={"text": "A"}).json()
    b = client.post("/api/projects/p/todos", json={"text": "B"}).json()
    c = client.post("/api/projects/p/todos", json={"text": "C"}).json()
    # Move A after C by giving it a higher position.
    client.patch(f"/api/projects/p/todos/{a['id']}", json={"position": c["position"] + 1})
    order = [i["text"] for i in client.get("/api/projects/p/todos").json()]
    assert order == ["B", "C", "A"]


def test_ac4_patch_multiple_fields_together(client):
    tid = client.post("/api/projects/p/todos", json={"text": "x"}).json()["id"]
    r = client.patch(
        f"/api/projects/p/todos/{tid}",
        json={"text": "y", "done": True, "position": 9},
    )
    body = r.json()
    assert body["text"] == "y"
    assert body["done"] is True
    assert body["position"] == 9


def test_patch_unknown_todo_returns_404(client):
    r = client.patch("/api/projects/p/todos/9999", json={"done": True})
    assert r.status_code == 404


# ── AC5 / UAT 7: delete ───────────────────────────────────────────────────────

def test_ac5_delete_removes_todo(client):
    tid = client.post("/api/projects/p/todos", json={"text": "del me"}).json()["id"]
    r = client.delete(f"/api/projects/p/todos/{tid}")
    assert r.status_code == 204
    listed = client.get("/api/projects/p/todos").json()
    assert all(i["id"] != tid for i in listed)


def test_delete_unknown_todo_returns_404(client):
    r = client.delete("/api/projects/p/todos/9999")
    assert r.status_code == 404


def test_clear_done_removes_only_completed_todos(client):
    proj = "p-clear-done"
    active = client.post(f"/api/projects/{proj}/todos", json={"text": "keep"}).json()
    done_a = client.post(f"/api/projects/{proj}/todos", json={"text": "gone A"}).json()
    done_b = client.post(f"/api/projects/{proj}/todos", json={"text": "gone B"}).json()
    client.patch(f"/api/projects/{proj}/todos/{done_a['id']}", json={"done": True})
    client.patch(f"/api/projects/{proj}/todos/{done_b['id']}", json={"done": True})

    r = client.delete(f"/api/projects/{proj}/todos/done")
    assert r.status_code == 200
    assert r.json()["deleted"] == 2

    listed = client.get(f"/api/projects/{proj}/todos").json()
    assert len(listed) == 1
    assert listed[0]["id"] == active["id"]
    assert listed[0]["done"] is False


def test_clear_done_is_project_scoped(client):
    done = client.post("/api/projects/mine/todos", json={"text": "mine"}).json()
    client.patch(f"/api/projects/mine/todos/{done['id']}", json={"done": True})
    other = client.post("/api/projects/other/todos", json={"text": "theirs"}).json()
    client.patch(f"/api/projects/other/todos/{other['id']}", json={"done": True})

    r = client.delete("/api/projects/mine/todos/done")
    assert r.status_code == 200
    assert r.json()["deleted"] == 1

    assert len(client.get("/api/projects/mine/todos").json()) == 0
    assert len(client.get("/api/projects/other/todos").json()) == 1


def test_clear_done_noop_when_none_completed(client):
    client.post("/api/projects/p/todos", json={"text": "active only"})
    r = client.delete("/api/projects/p/todos/done")
    assert r.status_code == 200
    assert r.json()["deleted"] == 0
    assert len(client.get("/api/projects/p/todos").json()) == 1


# ── AC6 / UAT 6: project isolation ────────────────────────────────────────────

def test_ac6_list_is_project_scoped(client):
    client.post("/api/projects/my-project/todos", json={"text": "mine"})
    other = client.get("/api/projects/other-project/todos")
    assert other.status_code == 200
    assert other.json() == []


def test_ac6_cannot_patch_another_projects_todo(client):
    tid = client.post("/api/projects/my-project/todos", json={"text": "mine"}).json()["id"]
    r = client.patch(f"/api/projects/other-project/todos/{tid}", json={"done": True})
    assert r.status_code == 404
    # Original is untouched.
    mine = client.get("/api/projects/my-project/todos").json()
    assert mine[0]["done"] is False


def test_ac6_cannot_delete_another_projects_todo(client):
    tid = client.post("/api/projects/my-project/todos", json={"text": "mine"}).json()["id"]
    r = client.delete(f"/api/projects/other-project/todos/{tid}")
    assert r.status_code == 404
    assert len(client.get("/api/projects/my-project/todos").json()) == 1


# ── AC7 / UAT 8: durability across restart ────────────────────────────────────

def test_ac7_toggle_done_persists_across_restart(client, db_file, monkeypatch):
    tid = client.post("/api/projects/p/todos", json={"text": "persist"}).json()["id"]
    client.patch(f"/api/projects/p/todos/{tid}", json={"done": True})

    # Simulate a server restart: brand-new engine + session bound to the same DB.
    fresh = create_engine(f"sqlite:///{db_file}")
    monkeypatch.setattr(todo_repo, "_session_factory", sessionmaker(bind=fresh))

    app = FastAPI()
    app.include_router(todos_router_mod.router)
    client2 = TestClient(app, raise_server_exceptions=False)

    listed = client2.get("/api/projects/p/todos").json()
    match = [i for i in listed if i["id"] == tid]
    assert match and match[0]["done"] is True


# ── AC8: ascending position order ─────────────────────────────────────────────

def test_ac8_list_always_ascending_by_position(client):
    a = client.post("/api/projects/p/todos", json={"text": "A"}).json()
    b = client.post("/api/projects/p/todos", json={"text": "B"}).json()
    c = client.post("/api/projects/p/todos", json={"text": "C"}).json()
    # Scramble positions.
    client.patch(f"/api/projects/p/todos/{a['id']}", json={"position": 50})
    client.patch(f"/api/projects/p/todos/{b['id']}", json={"position": 10})
    client.patch(f"/api/projects/p/todos/{c['id']}", json={"position": 30})
    positions = [i["position"] for i in client.get("/api/projects/p/todos").json()]
    assert positions == sorted(positions)
    assert positions == [10, 30, 50]


# ── AC9 / AC10: API never exposes ticket-like or reserved fields ───────────────

def test_ac9_ac10_api_response_has_no_ticket_or_reserved_fields(client):
    body = client.post("/api/projects/p/todos", json={"text": "t"}).json()
    assert set(body.keys()) == {
        "id", "project", "text", "done", "position", "created_at", "updated_at",
        "attachments",
    }
    assert body["attachments"] == []
    for forbidden in ("labels", "label", "assignee", "assignees",
                      "due_date", "due", "promoted_issue_number"):
        assert forbidden not in body


def test_ac10_promoted_issue_number_left_null_on_create(client, bound_repo):
    client.post("/api/projects/p/todos", json={"text": "t"})
    with bound_repo.connect() as conn:
        from sqlalchemy import text as sa_text
        row = conn.execute(
            sa_text("SELECT promoted_issue_number FROM project_todos")
        ).fetchone()
    assert row[0] is None


# ── repo lives in sprint_manager, not the SQLite dashboard db module ──────────

def test_repo_helpers_in_sprint_manager_module():
    assert hasattr(todo_repo, "list_todos")
    assert hasattr(todo_repo, "create_todo")
    assert hasattr(todo_repo, "update_todo")
    assert hasattr(todo_repo, "delete_todo")
    assert hasattr(todo_repo, "clear_done_todos")
