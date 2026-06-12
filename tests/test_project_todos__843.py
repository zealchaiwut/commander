"""Tester verification for issue #843 — per-project to-do list table + API
(runs against a LIVE server).

The shared UAT instance (port 8001) is pinned to a stale branch and 405/404s on
the new ``/api/projects/:project/todos`` routes, so this suite boots the
*feature branch* code as a real uvicorn subprocess and drives it over HTTP with
httpx — exactly as a deployed UAT box would be exercised. This proves the new
CRUD routes are actually mounted/reachable at runtime (the include_router wiring
in routers/sprints.py works, no startup errors), which an in-process TestClient
cannot prove. It complements the coder's in-process suite (test_843_*), which
drives the repo layer against a temp SQLite engine.

The subprocess runs with ``COMMANDER_DISABLE_NEON=1`` — the *actual* UAT runtime
config (see uat/apps/dashboard/.env) — so todos persist to the local JSON
fallback store. Testing that path is therefore testing real UAT behaviour, and
it also lets the durability test (AC7) prove on-disk persistence survives a true
process restart: a second uvicorn process started against the same store reads
back the toggled rows.

Isolation: every test uses a unique ``todo843*`` project slug (never a real
tracked project), and the session fixture backs up / restores the fallback
store file so the run leaves no trace in the repo.

Risk: HIGH — introduces a DB schema migration + new table (0011_add_project_todos)
and project-scoped mutation/delete endpoints. Additive and non-destructive, but
schema + delete paths warrant happy-path + edge + error coverage per criterion.
"""
from __future__ import annotations

import os
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

# The set of keys the public API is allowed to expose. Anything outside this set
# (labels, assignees, due dates, promoted_issue_number) violates AC9 / AC10.
ALLOWED_KEYS = {
    "id", "project", "text", "done", "position", "created_at", "updated_at",
}


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _boot_server() -> tuple[subprocess.Popen, str]:
    """Start a real uvicorn subprocess on the feature code; return (proc, url).

    Waits until a todos route answers (200 on an empty project list) so callers
    know the router is mounted before driving it.
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
    deadline = time.time() + 40
    while time.time() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
            raise RuntimeError(
                f"server exited early (code {proc.returncode}):\n{out[-3000:]}"
            )
        try:
            r = httpx.get(f"{url}/api/projects/todo843boot/todos", timeout=2.0)
            if r.status_code == 200:
                return proc, url
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    _stop_server(proc)
    raise RuntimeError("server did not become ready within 40s")


def _stop_server(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope="session")
def _store_guard():
    """Back up the fallback store before the run, restore it after.

    Leaves the repo exactly as found whether or not a store existed.
    """
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
    with httpx.Client(base_url=server, timeout=30.0) as c:
        yield c


# ── AC1 / AC9 / AC10 — table shape (static, mirrors coder's schema test) ──────

def test_project_todos__table_has_required_columns_incl_nullable_reserved():
    # AC1: project_todos has id, project, text, done, position, created_at,
    # updated_at, and a nullable promoted_issue_number. AC9: no ticket-like
    # columns. Proven by inspecting the ORM table the live server actually maps.
    if str(DASHBOARD_DIR) not in sys.path:
        sys.path.insert(0, str(DASHBOARD_DIR))
    from services.sprint_manager.models import ProjectTodo  # noqa: PLC0415

    cols = {c.name: c for c in ProjectTodo.__table__.columns}
    for required in (
        "id", "project", "text", "done", "position",
        "created_at", "updated_at", "promoted_issue_number",
    ):
        assert required in cols, f"missing column: {required}"
    # promoted_issue_number exists but is nullable (reserved, unused).
    assert cols["promoted_issue_number"].nullable is True
    # No ticket-like fields leaked into the schema.
    for forbidden in ("label", "labels", "assignee", "assignees", "due_date", "due"):
        assert forbidden not in cols, f"ticket-like column present: {forbidden}"


# ── AC3 / UAT1 — create ───────────────────────────────────────────────────────

def test_project_todos__create_returns_201_done_false_position_set(client):
    # AC3: POST creates a todo; done defaults False; position is set; 201.
    r = client.post("/api/projects/todo843create/todos",
                    json={"text": "Write release notes"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["text"] == "Write release notes"
    assert body["done"] is False
    assert isinstance(body["position"], int)
    assert isinstance(body["id"], int)
    assert body["project"] == "todo843create"


# ── AC2 / AC8 / UAT2 — list ordered by position ───────────────────────────────

def test_project_todos__list_returns_all_ordered_by_position(client):
    proj = "todo843list"
    for text in ("first", "second", "third"):
        assert client.post(f"/api/projects/{proj}/todos",
                           json={"text": text}).status_code == 201
    r = client.get(f"/api/projects/{proj}/todos")
    assert r.status_code == 200, r.text
    items = r.json()
    assert [t["text"] for t in items] == ["first", "second", "third"]
    positions = [t["position"] for t in items]
    assert positions == sorted(positions), f"not ascending by position: {positions}"


# ── AC4 / UAT3 — toggle done ───────────────────────────────────────────────────

def test_project_todos__patch_toggles_done_and_persists_in_list(client):
    proj = "todo843toggle"
    todo = client.post(f"/api/projects/{proj}/todos",
                       json={"text": "ship it"}).json()
    r = client.patch(f"/api/projects/{proj}/todos/{todo['id']}",
                     json={"done": True})
    assert r.status_code == 200, r.text
    assert r.json()["done"] is True
    # subsequent GET still returns the item, now done
    listed = client.get(f"/api/projects/{proj}/todos").json()
    match = [t for t in listed if t["id"] == todo["id"]]
    assert match and match[0]["done"] is True


# ── AC4 / UAT4 — edit text, leave done + position unchanged ────────────────────

def test_project_todos__patch_edits_text_without_touching_done_or_position(client):
    proj = "todo843edit"
    todo = client.post(f"/api/projects/{proj}/todos",
                       json={"text": "Write release notes"}).json()
    r = client.patch(f"/api/projects/{proj}/todos/{todo['id']}",
                     json={"text": "Write and publish release notes"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["text"] == "Write and publish release notes"
    assert body["done"] == todo["done"]
    assert body["position"] == todo["position"]


# ── AC4 — combined fields in one PATCH ────────────────────────────────────────

def test_project_todos__patch_updates_multiple_fields_together(client):
    proj = "todo843multi"
    todo = client.post(f"/api/projects/{proj}/todos",
                       json={"text": "draft"}).json()
    r = client.patch(f"/api/projects/{proj}/todos/{todo['id']}",
                     json={"text": "final", "done": True, "position": 7})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["text"] == "final"
    assert body["done"] is True
    assert body["position"] == 7


# ── AC4 / AC8 / UAT5 — reorder changes list order ─────────────────────────────

def test_project_todos__reorder_via_position_changes_list_order(client):
    proj = "todo843reorder"
    a = client.post(f"/api/projects/{proj}/todos", json={"text": "A"}).json()
    b = client.post(f"/api/projects/{proj}/todos", json={"text": "B"}).json()
    c = client.post(f"/api/projects/{proj}/todos", json={"text": "C"}).json()
    # push A to the end by giving it a higher position than C
    bumped = client.patch(f"/api/projects/{proj}/todos/{a['id']}",
                          json={"position": c["position"] + 1})
    assert bumped.status_code == 200, bumped.text
    order = [t["text"] for t in client.get(f"/api/projects/{proj}/todos").json()]
    assert order == ["B", "C", "A"], order


# ── AC6 / UAT6 — project isolation (read, mutate, delete) ──────────────────────

def test_project_todos__are_scoped_per_project(client):
    mine = client.post("/api/projects/todo843mine/todos",
                       json={"text": "secret"}).json()
    # another project never sees my todos
    other = client.get("/api/projects/todo843other/todos")
    assert other.status_code == 200
    assert all(t["id"] != mine["id"] for t in other.json())
    assert "secret" not in [t["text"] for t in other.json()]
    # another project cannot mutate or delete my todo (404, not silent success)
    assert client.patch(f"/api/projects/todo843other/todos/{mine['id']}",
                        json={"done": True}).status_code == 404
    assert client.delete(
        f"/api/projects/todo843other/todos/{mine['id']}").status_code == 404
    # and my todo is untouched
    still = client.get("/api/projects/todo843mine/todos").json()
    assert any(t["id"] == mine["id"] and t["done"] is False for t in still)


# ── AC5 / UAT7 — delete (happy path + missing → 404) ──────────────────────────

def test_project_todos__delete_removes_item_and_404s_when_absent(client):
    proj = "todo843delete"
    todo = client.post(f"/api/projects/{proj}/todos",
                       json={"text": "remove me"}).json()
    r = client.delete(f"/api/projects/{proj}/todos/{todo['id']}")
    assert r.status_code == 204, r.text
    # gone from the list
    remaining = client.get(f"/api/projects/{proj}/todos").json()
    assert all(t["id"] != todo["id"] for t in remaining)
    # deleting again is a clean 404, not a 500
    assert client.delete(
        f"/api/projects/{proj}/todos/{todo['id']}").status_code == 404


# ── AC9 / AC10 — response shape is not ticket-like, no reserved field ─────────

def test_project_todos__response_has_no_ticket_like_or_reserved_fields(client):
    proj = "todo843shape"
    created = client.post(f"/api/projects/{proj}/todos",
                          json={"text": "scratch"}).json()
    listed = client.get(f"/api/projects/{proj}/todos").json()[0]
    for body in (created, listed):
        assert set(body.keys()) == ALLOWED_KEYS, set(body.keys())
        # explicit guard on the reserved column (AC10) and ticket metadata (AC9)
        assert "promoted_issue_number" not in body
        for forbidden in ("labels", "assignees", "assignee", "due_date", "due", "labels"):
            assert forbidden not in body


# ── AC7 / UAT8 — durability across a real process restart ─────────────────────

def test_project_todos__survive_a_server_restart(server, client):
    proj = "todo843durable"
    todo = client.post(f"/api/projects/{proj}/todos",
                       json={"text": "persist me"}).json()
    client.patch(f"/api/projects/{proj}/todos/{todo['id']}", json={"done": True})

    # Boot a SECOND, independent uvicorn process against the same on-disk store.
    proc2, url2 = _boot_server()
    try:
        with httpx.Client(base_url=url2, timeout=30.0) as c2:
            after = c2.get(f"/api/projects/{proj}/todos").json()
    finally:
        _stop_server(proc2)

    match = [t for t in after if t["id"] == todo["id"]]
    assert match, "todo did not survive restart"
    assert match[0]["done"] is True, "toggled done state did not persist"
    assert match[0]["text"] == "persist me"
