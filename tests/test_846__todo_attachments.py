"""Tests for to-do screenshot attachments and fullscreen UI markers."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
TODO_JS = REPO_ROOT / "apps" / "dashboard" / "static" / "project-todo.js"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    import sys

    dash = REPO_ROOT / "apps" / "dashboard"
    if str(dash) not in sys.path:
        sys.path.insert(0, str(dash))

    from fastapi import FastAPI
    from routers import todos as todos_router_mod

    app = FastAPI()
    app.include_router(todos_router_mod.router)
    from routers import sprints as sprints_mod
    app.include_router(sprints_mod.router)
    return TestClient(app)


def _png_bytes() -> bytes:
    # Minimal valid 1×1 PNG (no Pillow dependency).
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc"
        b"\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def test_attachment_upload_list_and_delete(client):
    import uuid

    proj = "attach-" + uuid.uuid4().hex[:8]
    todo = client.post(f"/api/projects/{proj}/todos", json={"text": "with shot"}).json()
    tid = todo["id"]

    r = client.post(
        f"/api/projects/{proj}/todos/{tid}/attachments",
        files={"file": ("shot.png", _png_bytes(), "image/png")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "shot.png"
    assert "/attachments/shot.png" in body["url"]

    listed = client.get(f"/api/projects/{proj}/todos").json()
    row = next(t for t in listed if t["id"] == tid)
    assert len(row["attachments"]) == 1

    get = client.get(f"/api/projects/{proj}/todos/{tid}/attachments/shot.png")
    assert get.status_code == 200
    assert get.headers["content-type"].startswith("image/")

    dr = client.delete(f"/api/projects/{proj}/todos/{tid}/attachments/shot.png")
    assert dr.status_code == 204
    listed2 = client.get(f"/api/projects/{proj}/todos").json()
    row2 = next(t for t in listed2 if t["id"] == tid)
    assert row2["attachments"] == []


def test_fullscreen_and_attachment_ui_wired():
    js = TODO_JS.read_text(encoding="utf-8")
    assert "todo-fs-root" in js
    assert "openFullscreen" in js
    assert "apiUploadAttachment" in js or "/attachments" in js
    assert "todo-thumb" in js
    assert "onpaste" in js
    assert "addFromScreenshot" in js
    assert "ti-pencil" in js
