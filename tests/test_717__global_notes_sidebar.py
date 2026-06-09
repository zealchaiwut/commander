"""Tests for issue #717 — Move Notes to global left sidebar with local persistence.

AC coverage:
  AC1  — Notes item appears in the left sidebar, positioned directly above Settings
  AC2  — Notes item is global (not scoped to a project) — single /api/notes endpoint, no project segment
  AC3  — Clicking Notes opens a Notes view/panel in the main content area (openNotesPanel wired)
  AC4  — GET /api/notes reads body from .commander/notes.json; empty string if file missing
  AC5  — PUT /api/notes writes the full body to .commander/notes.json, creating the file if absent
  AC6  — Backend mirrors settings_repo JSON-fallback pattern; works when Neon is disabled
  AC7  — Notes panel loads existing content from GET /api/notes on open (gnotesLoad calls GET)
  AC8  — Edits autosave via a debounced PUT /api/notes (no manual save button)
  AC9  — Notes content persists across reloads (round-trip through file on disk)
  AC10 — Notes content persists across server restarts (file-backed, not in-memory)
  AC11 — Notes content is identical regardless of project (global scope)
  AC12 — Notes removed from the project More dropdown menu
  AC13 — No regression to the Settings sidebar item or other left-nav items
"""

import importlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
STATIC = DASHBOARD_DIR / "static" / "project.html"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))


# ── Backend fixtures ──────────────────────────────────────────────────────────

@pytest.fixture()
def notes_repo(tmp_path):
    """notes_repo module with its store path redirected to a temp file."""
    import services.sprint_manager.notes_repo as nr
    importlib.reload(nr)
    nr._store_path_override = str(tmp_path / ".commander" / "notes.json")
    yield nr
    nr._store_path_override = None


@pytest.fixture()
def client_ctx(tmp_path):
    """Yield (client, notes_repo) with the notes store redirected to a temp file."""
    for mod in ("server", "services.sprint_manager.notes_repo"):
        sys.modules.pop(mod, None)

    import server as srv
    import services.sprint_manager.notes_repo as nr
    nr._store_path_override = str(tmp_path / ".commander" / "notes.json")

    from fastapi.testclient import TestClient
    client = TestClient(srv.app, raise_server_exceptions=False)
    yield client, nr
    nr._store_path_override = None


# ── AC4: GET /api/notes empty string when file missing ───────────────────────

def test_get_notes_empty_when_no_file(client_ctx):
    client, nr = client_ctx
    resp = client.get("/api/notes")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    assert resp.json().get("body") == "", f"Expected empty body, got {resp.json()}"


def test_repo_get_notes_empty_when_no_file(notes_repo):
    assert notes_repo.get_notes() == ""


# ── AC5: PUT /api/notes writes body, creating the file if absent ──────────────

def test_put_notes_returns_200(client_ctx):
    client, nr = client_ctx
    resp = client.put("/api/notes", json={"body": "# hello"})
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"


def test_put_notes_creates_file_on_disk(client_ctx):
    client, nr = client_ctx
    client.put("/api/notes", json={"body": "line one\nline two"})
    store = Path(nr._store_path_override)
    assert store.exists(), "PUT /api/notes must create .commander/notes.json"
    assert "line one" in store.read_text(), "Stored file must contain the written body"


def test_put_notes_stores_under_commander_notes_json(client_ctx):
    """File must be named notes.json under a .commander directory."""
    client, nr = client_ctx
    client.put("/api/notes", json={"body": "x"})
    store = Path(nr._store_path_override)
    assert store.name == "notes.json"
    assert store.parent.name == ".commander"


# ── AC9 / AC7: round-trip — PUT then GET returns the same body ────────────────

def test_put_then_get_roundtrip(client_ctx):
    client, nr = client_ctx
    body = "# Notes\n\n- todo 1\n- todo 2"
    client.put("/api/notes", json={"body": body})
    resp = client.get("/api/notes")
    assert resp.json().get("body") == body, f"Round-trip mismatch: {resp.json()}"


def test_put_overwrites_full_body(client_ctx):
    client, nr = client_ctx
    client.put("/api/notes", json={"body": "first version"})
    client.put("/api/notes", json={"body": "second version"})
    resp = client.get("/api/notes")
    assert resp.json().get("body") == "second version", "PUT must overwrite the full body"


# ── AC10: persistence across server restarts (file-backed) ────────────────────

def test_persists_across_restart(client_ctx, tmp_path):
    """A fresh server import (simulating a restart) reads the body from disk."""
    client, nr = client_ctx
    client.put("/api/notes", json={"body": "survives restart"})

    store_path = nr._store_path_override
    sys.modules.pop("server", None)
    sys.modules.pop("services.sprint_manager.notes_repo", None)
    import server as srv2
    import services.sprint_manager.notes_repo as nr2
    nr2._store_path_override = store_path

    from fastapi.testclient import TestClient
    client2 = TestClient(srv2.app, raise_server_exceptions=False)
    resp = client2.get("/api/notes")
    nr2._store_path_override = None
    assert resp.json().get("body") == "survives restart", "Body must survive a restart"


# ── AC6: works when Neon is disabled (repo never touches Neon) ────────────────

def test_repo_works_with_neon_disabled(notes_repo, monkeypatch):
    """notes_repo must not require Neon — set/get works with Neon disabled."""
    monkeypatch.setenv("COMMANDER_DISABLE_NEON", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    notes_repo.set_notes("neon-off content")
    assert notes_repo.get_notes() == "neon-off content"


def test_repo_set_creates_file(notes_repo):
    notes_repo.set_notes("hi")
    assert Path(notes_repo._store_path_override).exists()


# ── AC2 / AC11: global scope — single endpoint, no project segment ────────────

def test_notes_endpoint_is_global_not_project_scoped(client_ctx):
    """The same /api/notes returns the same content irrespective of any project."""
    client, nr = client_ctx
    client.put("/api/notes", json={"body": "global content"})
    # No project path segment exists; repeated GETs return identical global content.
    first = client.get("/api/notes").json().get("body")
    second = client.get("/api/notes").json().get("body")
    assert first == second == "global content", "Notes must be global, not project-scoped"


# ── Frontend structure assertions ─────────────────────────────────────────────

@pytest.fixture(scope="module")
def html():
    return STATIC.read_text(encoding="utf-8")


# AC1: Notes sidebar item above Settings
def test_notes_sidebar_link_present(html):
    assert 'id="sb-notes-link"' in html, "Sidebar Notes link (sb-notes-link) must exist"


def test_notes_sidebar_link_above_settings(html):
    notes_idx = html.find('id="sb-notes-link"')
    settings_idx = html.find('id="sb-settings-link"')
    assert notes_idx != -1 and settings_idx != -1
    assert notes_idx < settings_idx, "Notes sidebar link must appear ABOVE the Settings link"


# AC13: Settings sidebar item still present (no regression)
def test_settings_sidebar_link_preserved(html):
    assert 'id="sb-settings-link"' in html, "Settings sidebar link must remain (no regression)"


# AC3 / AC12: Notes removed from the More dropdown
def test_notes_removed_from_more_dropdown(html):
    assert 'id="stab-notes"' not in html, "Notes tab must be removed from the More dropdown"
    assert 'switchTab(\'notes\')' not in html, "No switchTab('notes') trigger should remain"


# AC3: clicking sidebar Notes opens a panel in the main content area
def test_notes_panel_and_opener_wired(html):
    assert 'openNotesPanel' in html, "openNotesPanel handler must be wired"
    assert 'id="gnotes-panel"' in html, "Global notes panel element must exist"
    assert 'id="gnotes-textarea"' in html, "Notes editor textarea must exist"


# AC7: panel loads from GET /api/notes on open
def test_notes_panel_loads_from_get(html):
    assert "fetch('/api/notes')" in html or 'fetch("/api/notes")' in html, \
        "Notes panel must load content from GET /api/notes"


# AC8: debounced autosave via PUT /api/notes, no manual save button
def test_notes_autosaves_via_put(html):
    assert "method:'PUT'" in html or 'method: \'PUT\'' in html or 'method:"PUT"' in html, \
        "Notes must autosave via a PUT request"
    # debounce: a setTimeout-based debounce should be referenced in the notes autosave path
    assert 'gnotesAutosave' in html, "A debounced autosave handler (gnotesAutosave) must exist"
