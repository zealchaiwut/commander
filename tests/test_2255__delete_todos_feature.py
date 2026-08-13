"""Tests for issue #2255: Delete the Todos feature in favour of docs/todo.md.

AC1: routers/todos.py, todo_repo.py, todo_attachment_repo.py,
     static/project-todo.js deleted; routes removed from sprints.py
AC2: The todo dock removed from project.html; project-todo.js script tag gone
AC4: ProjectTodo removed from models.py (table documented as orphaned)
AC5: No remaining frontend or backend reference to the todos endpoints
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
APPS_DASHBOARD = REPO_ROOT / "apps" / "dashboard"
SERVICES_DIR = REPO_ROOT / "services" / "sprint_manager"

sys.path.insert(0, str(APPS_DASHBOARD))
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(scope="module")
def client():
    """In-process TestClient (issue #1746 pattern)."""
    from fastapi.testclient import TestClient  # noqa: PLC0415
    import server as srv  # noqa: PLC0415

    return TestClient(srv.app)


# ── AC1: Files deleted ────────────────────────────────────────────────────────

def test_ac1_todos_router_deleted():
    """routers/todos.py must not exist."""
    assert not (APPS_DASHBOARD / "routers" / "todos.py").exists(), \
        "routers/todos.py still exists — must be deleted"


def test_ac1_todo_repo_deleted():
    """services/sprint_manager/todo_repo.py must not exist."""
    assert not (SERVICES_DIR / "todo_repo.py").exists(), \
        "todo_repo.py still exists — must be deleted"


def test_ac1_todo_attachment_repo_deleted():
    """services/sprint_manager/todo_attachment_repo.py must not exist."""
    assert not (SERVICES_DIR / "todo_attachment_repo.py").exists(), \
        "todo_attachment_repo.py still exists — must be deleted"


def test_ac1_project_todo_js_deleted():
    """static/project-todo.js must not exist."""
    assert not (APPS_DASHBOARD / "static" / "project-todo.js").exists(), \
        "project-todo.js still exists — must be deleted"


def test_ac1_todos_not_imported_in_sprints():
    """routers/sprints.py must not import or include the todos module."""
    sprints_text = (APPS_DASHBOARD / "routers" / "sprints.py").read_text(encoding="utf-8")
    assert "todos" not in sprints_text, \
        "sprints.py still references the todos module"


# ── AC1: Todos routes not registered in the app (behavioral route check) ──────

def test_ac1_no_todos_route_in_app(client):
    """The app must have no route with '/todos' in its path (todos router removed).

    Walks all routes including mounted sub-routers and verifies that no path
    ends with /todos or contains /todos/ — confirming the todos APIRouter
    is not included anywhere in the app.
    """
    import server as srv  # noqa: PLC0415

    def _collect_paths(routes) -> list[str]:
        paths = []
        for route in routes:
            path = getattr(route, 'path', '') or ''
            if path:
                paths.append(path)
            sub = getattr(route, 'app', None)
            if sub and hasattr(sub, 'routes'):
                paths.extend(_collect_paths(sub.routes))
        return paths

    all_paths = _collect_paths(srv.app.routes)
    todos_paths = [p for p in all_paths if '/todos' in p]
    assert not todos_paths, \
        f"Todos routes still registered in the app: {todos_paths}"


def test_ac1_batch_todos_endpoint_returns_404(client):
    """GET /api/todos must return 404 — batch route is gone."""
    r = client.get("/api/todos", params={"projects": "commander"})
    assert r.status_code == 404, \
        f"GET /api/todos returned {r.status_code}, expected 404"


# ── AC2: Todo dock removed from project.html ─────────────────────────────────

def test_ac2_todo_dock_removed_from_project_html():
    """project.html must not contain the todo-dock element."""
    project_html = (APPS_DASHBOARD / "static" / "project.html").read_text(encoding="utf-8")
    assert "todo-dock" not in project_html, \
        "todo-dock element still present in project.html"


def test_ac2_project_todo_js_script_tag_removed():
    """project.html must not load project-todo.js."""
    project_html = (APPS_DASHBOARD / "static" / "project.html").read_text(encoding="utf-8")
    assert "project-todo.js" not in project_html, \
        "project-todo.js script tag still present in project.html"


def test_ac2_toggle_todo_dock_function_removed():
    """project.html must not define toggleTodoDock (the dock controller)."""
    project_html = (APPS_DASHBOARD / "static" / "project.html").read_text(encoding="utf-8")
    assert "toggleTodoDock" not in project_html, \
        "toggleTodoDock function still present in project.html"


def test_ac2_commander_todo_mount_removed_from_project_html():
    """project.html must not mount the CommanderTodo panel."""
    project_html = (APPS_DASHBOARD / "static" / "project.html").read_text(encoding="utf-8")
    assert "CommanderTodo" not in project_html, \
        "CommanderTodo.mount still referenced in project.html"


# ── AC2: Page still renders (no orphaned references crashing init) ────────────

def test_ac2_project_page_still_served(client):
    """GET /project/<slug>/ must still return 200 — page renders without todo dock."""
    r = client.get("/project/commander/")
    assert r.status_code == 200, \
        f"GET /project/commander/ returned {r.status_code} after todo dock removal"


# ── AC4: ProjectTodo removed from models.py ───────────────────────────────────

def test_ac4_project_todo_class_removed_from_models():
    """models.py must not define ProjectTodo — the ORM class is gone."""
    models_text = (SERVICES_DIR / "models.py").read_text(encoding="utf-8")
    assert "ProjectTodo" not in models_text, \
        "ProjectTodo class still defined in models.py — must be removed"


def test_ac4_project_todos_table_not_in_models():
    """models.py must not declare the project_todos table name."""
    models_text = (SERVICES_DIR / "models.py").read_text(encoding="utf-8")
    assert "project_todos" not in models_text, \
        "project_todos table still declared in models.py"


# ── AC5: No remaining references to todos endpoints ──────────────────────────

def test_ac5_no_python_imports_of_todo_repo():
    """No Python source file outside deleted files should import todo_repo."""
    for py_file in REPO_ROOT.rglob("*.py"):
        # Skip the deleted source files themselves (they'll be gone)
        rel = py_file.relative_to(REPO_ROOT)
        parts = [p.lower() for p in rel.parts]
        if "todo_repo" in parts or "todo_attachment_repo" in parts:
            continue
        # Skip the test files for the old feature (they will be deleted too)
        if py_file.name.startswith("test_") and "todo" in py_file.name.lower():
            continue
        text = py_file.read_text(encoding="utf-8", errors="ignore")
        assert "todo_repo" not in text, \
            f"{rel} still imports todo_repo"
        assert "todo_attachment_repo" not in text, \
            f"{rel} still imports todo_attachment_repo"


def test_ac5_no_python_imports_of_todos_router():
    """No Python source file should import the todos router module."""
    for py_file in REPO_ROOT.rglob("*.py"):
        rel = py_file.relative_to(REPO_ROOT)
        if py_file.name == "todos.py" and "routers" in str(rel):
            continue
        if py_file.name.startswith("test_") and "todo" in py_file.name.lower():
            continue
        text = py_file.read_text(encoding="utf-8", errors="ignore")
        # Must not import the deleted todos module from routers
        assert "from . import todos" not in text and "from routers import todos" not in text \
            and "routers.todos" not in text, \
            f"{rel} still references the todos router"
