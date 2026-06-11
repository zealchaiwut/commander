"""Tests for issue #773 — Pre-fill the `.env` setting field with its current value.

Each test is anchored to a specific acceptance criterion from the issue.

AC coverage:
  AC1 — When the `.env` setting field is opened for editing it is pre-filled
        with the current saved value (the editable VALUE input carries the
        stored value; the GET endpoint returns the plaintext value so the
        client can seed the input).
  AC2 — The pre-filled value is editable (rendered as a plain <input>, not a
        read-only/disabled control).
  AC3 — When no value has been previously saved the field stays empty and no
        placeholder/mask is treated as a value (an empty `.env` value is shown
        as an explicit "(empty)" hint in the read view, never as mask bullets,
        and seeds an empty edit input).
  AC4 — Saving without changes leaves the value unchanged (a no-op PUT round
        trips byte-for-byte, preserving inline comments).
  AC5 — Saving after clearing the field removes/resets the value (a PUT with an
        empty value rewrites `KEY=` and reading back returns "").
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
STATIC_DIR = DASHBOARD_DIR / "static"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))


# ── API fixture (real .env on disk) ──────────────────────────────────────────

@pytest.fixture()
def client_ctx(tmp_path):
    """Yield (client, env_dir) with a real .env containing a populated key, an
    empty-valued key, and an inline comment."""
    env_dir = tmp_path / "prd"
    env_dir.mkdir()
    (env_dir / ".env").write_text(
        "DB_HOST=localhost\n"
        "DB_PORT=5432  # default\n"
        "EMPTY_KEY=\n"
    )

    projects_file = tmp_path / "projects.json"
    projects_file.write_text(json.dumps([
        {"repo": "owner/test-proj", "name": "Test Proj",
         "environments": {"prd": str(env_dir)}}
    ]))

    for mod in list(sys.modules.keys()):
        if mod in ("server", "projects", "env_file") or mod.startswith("services."):
            sys.modules.pop(mod, None)

    import server as srv
    import projects as projects_module
    projects_module.PROJECTS_FILE = projects_file

    from fastapi.testclient import TestClient
    with patch.object(srv, "projects_module", projects_module):
        client = TestClient(srv.app, raise_server_exceptions=False)
        yield client, env_dir


# ── AC1: edit field pre-filled with the current saved value ──────────────────

def test_get_returns_saved_value_for_prefill(client_ctx):
    """AC1: the GET endpoint returns the plaintext saved value so the edit
    field can be pre-filled (not blank)."""
    client, _ = client_ctx
    resp = client.get("/api/projects/test-proj/environments/prd/env-vars")
    assert resp.status_code == 200, resp.text
    vars_by_key = {v["key"]: v["value"] for v in resp.json()["vars"]}
    assert vars_by_key["DB_HOST"] == "localhost"
    assert vars_by_key["DB_PORT"] == "5432"


def test_edit_row_prefills_value_attribute():
    """AC1: the edit VALUE input is seeded with the saved value (value="…"),
    so opening the editor shows the current value rather than a blank box."""
    src = (STATIC_DIR / "project.html").read_text()
    # The edit-row builder must place the value into the input's value attr.
    assert "function _envVarEditRow(key, value)" in src
    assert "ps-envvar-val-inp" in src
    assert "value=\"' + _escAttr(value || '') + '\"" in src


# ── AC2: pre-filled value is editable ────────────────────────────────────────

def test_edit_value_is_plain_editable_input():
    """AC2: the pre-filled value lives in a plain <input> — not readonly or
    disabled — so it can be cleared, modified, or replaced."""
    src = (STATIC_DIR / "project.html").read_text()
    # Locate the edit-row builder body and assert the value input is editable.
    start = src.index("function _envVarEditRow(key, value)")
    body = src[start:start + 600]
    assert "ps-envvar-val-inp" in body
    assert "readonly" not in body.lower()
    assert "disabled" not in body.lower()


# ── AC3: no saved value → empty, no placeholder/mask treated as a value ──────

def test_empty_value_returned_as_empty_string(client_ctx):
    """AC3: a key with no saved value (`KEY=`) is returned as an empty string,
    not bullets or a placeholder, so the edit field seeds empty."""
    client, _ = client_ctx
    resp = client.get("/api/projects/test-proj/environments/prd/env-vars")
    vars_by_key = {v["key"]: v["value"] for v in resp.json()["vars"]}
    assert "EMPTY_KEY" in vars_by_key
    assert vars_by_key["EMPTY_KEY"] == ""


def test_read_view_does_not_mask_empty_value():
    """AC3: the read view must not render mask bullets for an empty value —
    an unset variable is shown as an explicit "(empty)" hint instead, so the
    placeholder is never mistaken for a saved value."""
    src = (STATIC_DIR / "project.html").read_text()
    # Isolate the read-view row builder inside _envVarsRender.
    start = src.index("function _envVarsRender(env)")
    body = src[start:start + 1600]
    # An emptiness branch must exist and emit the (empty) hint with its own class.
    assert "is-empty" in body, "read view should special-case empty values"
    assert "(empty)" in body, "empty values should show an explicit (empty) hint"
    # The masking expression must be guarded so empty values are not bulleted.
    assert "isEmpty" in body, "mask path should be gated on an emptiness check"


# ── AC4: saving without changes leaves the value unchanged ───────────────────

def test_put_no_changes_leaves_file_unchanged(client_ctx):
    """AC4: re-saving the existing values verbatim leaves the .env byte-for-byte
    identical (inline comments preserved, values unchanged)."""
    client, env_dir = client_ctx
    before = (env_dir / ".env").read_text()
    resp = client.put(
        "/api/projects/test-proj/environments/prd/env-vars",
        json={"vars": [
            {"key": "DB_HOST", "value": "localhost"},
            {"key": "DB_PORT", "value": "5432"},
            {"key": "EMPTY_KEY", "value": ""},
        ]},
    )
    assert resp.status_code == 200, resp.text
    assert (env_dir / ".env").read_text() == before


# ── AC5: saving after clearing the field removes/resets the value ────────────

def test_put_cleared_value_resets_to_empty(client_ctx):
    """AC5: clearing a populated field and saving rewrites `KEY=` and reading
    back returns an empty value."""
    client, env_dir = client_ctx
    resp = client.put(
        "/api/projects/test-proj/environments/prd/env-vars",
        json={"vars": [
            {"key": "DB_HOST", "value": ""},
            {"key": "DB_PORT", "value": "5432"},
            {"key": "EMPTY_KEY", "value": ""},
        ]},
    )
    assert resp.status_code == 200, resp.text
    on_disk = (env_dir / ".env").read_text()
    assert "DB_HOST=\n" in on_disk

    # Reopening shows the cleared field as empty, not its old value.
    resp2 = client.get("/api/projects/test-proj/environments/prd/env-vars")
    vars_by_key = {v["key"]: v["value"] for v in resp2.json()["vars"]}
    assert vars_by_key["DB_HOST"] == ""
