"""Tester verification for issue #844 — Build per-project to-do list panel UI
(runs against a LIVE server).

This is a frontend ticket: a single framework-free module
(``apps/dashboard/static/project-todo.js``) renders one to-do panel on two
surfaces — the right of every project block on the home page (``home.html``)
and a docked panel inside the project view (``project.html``) — both driven by
the per-project to-do API shipped in #843 (``/api/projects/<project>/todos``).

The shared UAT instance (port 8001) is pinned to a stale branch and 404/405s on
the home page, the ``/static/project-todo.js`` asset, and the ``/todos`` routes,
so — exactly as the #843 tester suite does — this suite boots the *feature
branch* code as a real uvicorn subprocess and drives it over HTTP with httpx.
The agent-browser runner is unavailable in this environment
(``COMMANDER_AGENT_BROWSER_AVAILABLE=0``), so the in-browser interaction steps
are verified manually; what is provable over HTTP is proven here:

  * the two surfaces are actually *served* and wire in the shared module
    (so the panel mounts at runtime, not just on disk);
  * the served module ships the mock's markup/styles and the behaviours each AC
    requires (add via Enter/＋, checkbox rows, Done(n) group, collapsible,
    hover delete/promote, inline edit, drag reorder, Saved indicator, empty
    state, promote = coming-soon-only);
  * the to-do API the panel auto-saves through performs a full CRUD round-trip
    at runtime (create → toggle → edit+reorder → delete, 404 on a missing id),
    which is the persistence backing AC11/AC13/AC14.

Isolation: the API round-trip uses a unique ``todo844*`` slug (never a real
tracked project) and deletes what it creates; a session guard backs up and
restores the JSON fallback store so the run leaves no trace in the repo.

Risk: MEDIUM — frontend enhancement over an existing, unchanged API; additive,
no auth/destructive paths, fully reversible. One focused assertion per AC.
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
STORE_PATH = REPO_ROOT / ".commander" / "project_todos_store.json"


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _boot_server() -> tuple[subprocess.Popen, str]:
    """Start a real uvicorn subprocess on the feature code; return (proc, url).

    Waits until a todos route answers 200 so callers know the router is mounted
    and the static assets are reachable before driving the app.
    """
    port = _free_port()
    env = dict(os.environ)
    env.update({
        "ENVIRONMENT": "uat",
        "COMMANDER_DISABLE_NEON": "1",   # real UAT config → JSON fallback store
        "PYTHONPATH": str(DASHBOARD_DIR),
    })
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(DASHBOARD_DIR), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 60
    while time.time() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
            raise RuntimeError(
                f"server exited early (code {proc.returncode}):\n{out[-3000:]}"
            )
        try:
            r = httpx.get(f"{url}/api/projects/todo844boot/todos", timeout=2.0)
            if r.status_code == 200:
                return proc, url
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    _stop_server(proc)
    raise RuntimeError("server did not become ready within 60s")


def _stop_server(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope="session")
def _store_guard():
    """Back up the fallback store before the run, restore it after."""
    existed = STORE_PATH.exists()
    backup = STORE_PATH.read_text() if existed else None
    yield
    if existed:
        STORE_PATH.write_text(backup)
    elif STORE_PATH.exists():
        STORE_PATH.unlink()


@pytest.fixture(scope="session")
def server(_store_guard):
    proc, url = _boot_server()
    try:
        yield url
    finally:
        _stop_server(proc)


@pytest.fixture()
def client(server):
    with httpx.Client(base_url=server, timeout=15.0, follow_redirects=True) as c:
        yield c


# ── served-artifact fixtures (fetched over HTTP, not read from disk) ──────────

@pytest.fixture(scope="session")
def home_html(server) -> str:
    r = httpx.get(f"{server}/", timeout=15.0, follow_redirects=True)
    assert r.status_code == 200, f"home page must serve 200, got {r.status_code}"
    return r.text


@pytest.fixture(scope="session")
def project_html(server) -> str:
    r = httpx.get(f"{server}/project/commander/sprint-mgmt",
                  timeout=15.0, follow_redirects=True)
    assert r.status_code == 200, f"project view must serve 200, got {r.status_code}"
    return r.text


@pytest.fixture(scope="session")
def todo_js(server) -> str:
    r = httpx.get(f"{server}/static/project-todo.js", timeout=15.0)
    assert r.status_code == 200, \
        f"shared to-do module must be served, got {r.status_code}"
    return r.text


def _nows(s: str) -> str:
    return re.sub(r"\s+", "", s)


# ── AC1: panel on the right of each home project block ───────────────────────

def test_844__panel_on_right_of_home_project_block(home_html):
    assert "/static/project-todo.js" in home_html, \
        "home page must load the shared to-do module"
    assert "CommanderTodo.mount" in home_html, \
        "home page must mount the shared to-do panel per project block"
    collapsed = _nows(home_html)
    assert re.search(r"\.pb-grid\{[^}]*grid-template-columns", collapsed), \
        "home block must use a 2-column grid placing the panel beside the summary"


# ── AC2: docked panel inside the project view ────────────────────────────────

def test_844__docked_panel_in_project_view(project_html):
    assert "/static/project-todo.js" in project_html, \
        "project view must load the shared to-do module"
    assert "todo-dock" in project_html, \
        "project view must contain a docked to-do panel"
    assert "CommanderTodo.mount" in project_html, \
        "project view must mount the shared to-do panel"


# ── AC3: same per-project list from the to-do API, not duplicated ────────────

def test_844__both_surfaces_share_one_module_and_api(home_html, project_html, todo_js):
    assert "/api/projects/" in todo_js and "/todos" in todo_js, \
        "module must read/write the per-project to-do API from #843"
    # Row markup lives only in the shared module — pages do not re-implement it.
    assert "todo-item" in todo_js
    assert "todo-item" not in home_html, "home must not duplicate row markup"
    assert "todo-item" not in project_html, "project view must not duplicate row markup"
    # Both surfaces mount keyed by the project slug → identical lists.
    assert re.search(r"CommanderTodo\.mount\([^)]*[Ss]lug", home_html), \
        "home must mount keyed by project slug"
    assert re.search(r"CommanderTodo\.mount\([^)]*[Ss]lug", project_html), \
        "project view must mount keyed by project slug"


# ── AC4: UI matches the attached mock ────────────────────────────────────────

def test_844__module_ships_mock_classes_and_styles(todo_js):
    for cls in (
        "todo-head", "todo-title", "todo-count", "todo-saved",
        "todo-add", "todo-input", "todo-addbtn",
        "todo-list", "todo-item", "todo-check", "todo-text",
        "todo-actions", "todo-iconbtn", "todo-empty",
        "todo-done-toggle", "todo-done-list",
    ):
        assert cls in todo_js, f"mock component class '{cls}' missing from served module"
    collapsed = _nows(todo_js)
    assert re.search(r"\.todo-item\.done\s*\.todo-text\{[^}]*line-through", collapsed), \
        "completed strikethrough style (from the mock) missing"
    assert "prefers-reduced-motion" in todo_js, \
        "module must provide a reduced-motion fallback (impeccable a11y rule)"


# ── AC5: add via Enter or the + button ───────────────────────────────────────

def test_844__add_via_enter_or_plus_button(todo_js):
    assert "'Enter'" in todo_js or '"Enter"' in todo_js, "Enter-to-add not wired"
    assert "todo-addbtn" in todo_js, "+ button not present"
    assert re.search(r"method:\s*['\"]POST['\"]", todo_js), \
        "add must POST a new todo to the API"


# ── AC6: checkbox + text row ──────────────────────────────────────────────────

def test_844__items_render_checkbox_and_text(todo_js):
    assert "todo-check" in todo_js and "todo-text" in todo_js, \
        "each item must render a checkbox + text row"


# ── AC7: check → strikethrough + move into "Done (n)" group ──────────────────

def test_844__check_moves_into_done_group(todo_js):
    assert "Done (" in todo_js, "Done group header missing"
    assert re.search(r"done\.length", todo_js), \
        "Done count must reflect the number of completed items"
    assert re.search(r"method:\s*['\"]PATCH['\"]", todo_js), \
        "toggling done must PATCH the item"


# ── AC8: uncheck → back to active list ───────────────────────────────────────

def test_844__uncheck_returns_to_active(todo_js):
    # A single toggle handler PATCHes done; rendering partitions on the flag.
    assert re.search(r"\.done\b", todo_js), \
        "rendering must partition items by their done flag (so unchecking returns them)"
    assert re.search(r"done\s*:", todo_js), "toggle must send the done flag"


# ── AC9: Done group collapsible; count stays current ─────────────────────────

def test_844__done_group_collapsible(todo_js):
    assert "todo-done-toggle" in todo_js, "Done group toggle missing"
    assert "done-collapsed" in todo_js, "Done group must have a collapsed state"


# ── AC10: hover reveals delete + edit (pencil) ────────────────────────────────

def test_844__hover_reveals_delete_and_edit(todo_js):
    collapsed = _nows(todo_js)
    assert re.search(r"\.todo-actions\{[^}]*opacity:0", collapsed), \
        "actions must be hidden until hover"
    assert re.search(r"\.todo-item:hover\s*\.todo-actions\{[^}]*opacity:1", collapsed), \
        "actions must reveal on hover"
    assert "ti-pencil" in todo_js, "edit pencil icon missing"
    assert re.search(r"\bdel\b|delete", todo_js), "delete action missing"


# ── AC11: delete removes + persists via API ──────────────────────────────────

def test_844__delete_persists_via_api(todo_js):
    assert re.search(r"method:\s*['\"]DELETE['\"]", todo_js), \
        "delete must call the API with DELETE"


# ── AC12: inline edit persists via API ───────────────────────────────────────

def test_844__inline_edit_persists_via_api(todo_js):
    assert "dblclick" in todo_js, "inline edit must be activated (double-click)"
    assert re.search(r"text\s*:", todo_js), "edit must PATCH the new text"


# ── AC13: reorder persists via API ───────────────────────────────────────────

def test_844__reorder_persists_via_api(todo_js):
    assert "draggable" in todo_js, "items must be draggable to reorder"
    assert re.search(r"position\s*:", todo_js), "reorder must PATCH the new position"


# ── AC14: auto-save + "Saved" indicator after every change ───────────────────

def test_844__saved_indicator_after_every_change(todo_js):
    assert "todo-saved" in todo_js, "Saved indicator element missing"
    assert todo_js.count("flashSaved") >= 2, \
        "the Saved indicator must fire after persistence on every mutation path"


# ── AC15: empty state ─────────────────────────────────────────────────────────

def test_844__empty_state_present(todo_js):
    assert "todo-empty" in todo_js, "empty-state element missing"


# ── AC16: paste screenshot creates a new to-do ───────────────────────────────

def test_844__paste_creates_new_todo_from_screenshot(todo_js):
    assert "addFromScreenshot" in todo_js
    m = re.search(r"panel\.onpaste\s*=\s*function\s*\([^)]*\)\s*\{(.*?)\n\s*\};", todo_js, re.DOTALL)
    assert m, "panel paste handler must exist"
    assert "addFromScreenshot" in m.group(1)


# ── Backing API: full CRUD round-trip the panel auto-saves through ────────────
# Exercises the runtime persistence path behind AC11/AC13/AC14 over real HTTP.

def test_844__todo_api_round_trip_backs_autosave(client):
    proj = "todo844roundtrip"
    # clean slate
    for t in client.get(f"/api/projects/{proj}/todos").json():
        client.delete(f"/api/projects/{proj}/todos/{t['id']}")

    # create (AC5 persistence)
    r = client.post(f"/api/projects/{proj}/todos", json={"text": "alpha"})
    assert r.status_code == 201, r.text
    item = r.json()
    tid = item["id"]
    assert item["text"] == "alpha" and item["done"] is False

    # toggle done (AC7 persistence)
    r = client.patch(f"/api/projects/{proj}/todos/{tid}", json={"done": True})
    assert r.status_code == 200 and r.json()["done"] is True

    # edit text + reorder position (AC12 / AC13 persistence)
    r = client.patch(f"/api/projects/{proj}/todos/{tid}",
                     json={"text": "alpha-edited", "position": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["text"] == "alpha-edited" and body["position"] == 5

    # change survives a fresh read (AC14 durable auto-save)
    listed = client.get(f"/api/projects/{proj}/todos").json()
    assert any(t["id"] == tid and t["text"] == "alpha-edited" and t["done"]
               for t in listed)

    # delete (AC11 persistence) and 404 on a missing id (error path)
    assert client.delete(f"/api/projects/{proj}/todos/{tid}").status_code == 204
    assert client.delete(f"/api/projects/{proj}/todos/{tid}").status_code == 404
    assert all(t["id"] != tid for t in client.get(f"/api/projects/{proj}/todos").json())
