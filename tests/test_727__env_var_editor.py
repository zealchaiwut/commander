"""Tests for issue #727 — Render-style env-var editor in project settings.

Each test is anchored to a specific acceptance criterion from the issue.

Backend AC coverage:
  AC-GET      — GET .../env-vars reads the .env file and returns parsed
                key/value pairs in file order
  AC-PUT-WRITE— PUT .../env-vars writes changes back to the env's .env file
  AC-ORDER    — PUT preserves original line order + inline comments where the
                key is unchanged; rewritten keys stay in position
  AC-APPEND   — new keys append at the end
  AC-DELETE   — removed rows are deleted from the .env file
  AC-PLAINTEXT— values stored/returned as plaintext; no server-side masking
  AC-EMPTYKEY — empty KEY blocks Save (PUT) with a validation error (400)
  AC-WRITEFAIL— a write failure returns a non-2xx error (edits not persisted)

Frontend AC coverage (project.html structure/JS):
  AC-SECTION  — Environment Variables section per environment
  AC-TABLE    — read view: KEY (plaintext) + VALUE (masked ••••) columns
  AC-EYE      — per-row eye icon toggles mask/reveal for that row only
  AC-EDIT     — Edit button switches table to editable inputs (unmasked)
  AC-ADDROW   — Add row control in edit mode
  AC-DELROW   — per-row Delete (trash) icon in edit mode
  AC-SAVE     — Save button calls PUT .../env-vars
  AC-TOAST    — success toast on 200, inline error on failure
  AC-CANCEL   — Cancel button discards edits and returns to read view
  AC-VALIDATE — empty KEY blocks Save client-side with validation error
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


# ── env_file module (parse/write helpers) ────────────────────────────────────

def _env_file_module():
    for mod in ("env_file",):
        sys.modules.pop(mod, None)
    import env_file
    return env_file


def test_parse_returns_pairs_in_file_order():
    """AC-GET: parsing returns key/value pairs in file order."""
    ef = _env_file_module()
    text = "B=2\nA=1\n# comment\nC=3\n"
    assert ef.parse_env_text(text) == [("B", "2"), ("A", "1"), ("C", "3")]


def test_read_env_vars_strips_inline_comments(tmp_path):
    """AC-GET: values are returned clean.

    Behaviour after issue #750:
    - Quoted values have their surrounding quotes stripped; inner
      content is returned.
    - Unquoted values are returned verbatim (the full raw value, including any
      trailing ' # ...' sequence, is preserved so legitimate values such as
      'secret #1' are not silently truncated).
    """
    ef = _env_file_module()
    env = tmp_path / ".env"
    env.write_text("FOO=bar  # inline note\nQUOTED=\"a b\" # c\n")
    got = ef.read_env_vars(env)
    assert got == [
        {"key": "FOO", "value": "bar  # inline note"},  # full value
        {"key": "QUOTED", "value": "a b"},  # outer quotes stripped
    ]


def test_read_env_vars_missing_file_returns_empty(tmp_path):
    """AC-GET: a missing .env yields an empty list, not an error."""
    ef = _env_file_module()
    assert ef.read_env_vars(tmp_path / "nope.env") == []


def test_write_preserves_order_and_inline_comment_when_unchanged(tmp_path):
    """AC-ORDER: unchanged keys keep their position and inline comment;
    a changed key is rewritten in place."""
    ef = _env_file_module()
    env = tmp_path / ".env"
    env.write_text(
        "# header comment\n"
        "FIRST=one  # keep me\n"
        "SECOND=two\n"
        "\n"
        "THIRD=three\n"
    )
    # FIRST unchanged, SECOND changed, THIRD unchanged.
    ef.write_env_vars(
        env,
        [("FIRST", "one"), ("SECOND", "TWO"), ("THIRD", "three")],
    )
    out = env.read_text()
    lines = out.splitlines()
    assert lines[0] == "# header comment"          # standalone comment kept
    assert lines[1] == "FIRST=one  # keep me"   # unchanged: verbatim
    assert lines[2] == "SECOND=TWO"              # changed: rewritten
    assert lines[3] == ""                            # blank line preserved
    assert lines[4] == "THIRD=three"                 # position preserved


def test_write_appends_new_keys_at_end(tmp_path):
    """AC-APPEND: new keys are appended after existing content."""
    ef = _env_file_module()
    env = tmp_path / ".env"
    env.write_text("A=1\nB=2\n")
    ef.write_env_vars(env, [("A", "1"), ("B", "2"), ("NEW", "9")])
    lines = env.read_text().splitlines()
    assert lines == ["A=1", "B=2", "NEW=9"]


def test_write_deletes_removed_keys(tmp_path):
    """AC-DELETE: keys absent from the submitted set are removed."""
    ef = _env_file_module()
    env = tmp_path / ".env"
    env.write_text("A=1\nB=2\nC=3\n")
    ef.write_env_vars(env, [("A", "1"), ("C", "3")])
    lines = env.read_text().splitlines()
    assert lines == ["A=1", "C=3"]
    assert "B=2" not in env.read_text()


# ── API endpoints ────────────────────────────────────────────────────────────

@pytest.fixture()
def client_ctx(tmp_path):
    """Yield (client, srv, projects_module, env_dir) with .env on disk."""
    env_dir = tmp_path / "prd"
    env_dir.mkdir()
    (env_dir / ".env").write_text(
        "DB_HOST=localhost\nDB_PORT=5432  # default\n"
    )

    projects_file = tmp_path / "projects.json"
    projects_file.write_text(json.dumps([
        {"repo": "owner/test-proj", "name": "Test Proj",
         "environments": {"prd": str(env_dir)}}
    ]))

    from _sys_modules_guard import temporary_module_purge

    with temporary_module_purge(
        "server", "projects", "env_file", "routers",
        prefixes=("services.", "routers."),
    ):
        import server as srv
        import projects as projects_module
        projects_module.PROJECTS_FILE = projects_file

        from fastapi.testclient import TestClient
        with patch.object(srv, "projects_module", projects_module):
            client = TestClient(srv.app, raise_server_exceptions=False)
            yield client, srv, projects_module, env_dir


def test_get_env_vars_returns_pairs_in_order(client_ctx):
    """AC-GET: GET endpoint reads the .env and returns pairs in file order.

    After issue #750: unquoted values are returned verbatim so
    'DB_PORT=5432  # default' yields '5432  # default', not '5432'.
    The editor's change-detection still treats
    them as equal when the user submits '5432' (see _comparison_value).
    """
    client, _, _, _ = client_ctx
    resp = client.get("/api/projects/test-proj/environments/prd/env-vars")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["vars"] == [
        {"key": "DB_HOST", "value": "localhost"},
        {"key": "DB_PORT", "value": "5432  # default"},  # full value
    ]


def test_put_env_vars_writes_back_to_file(client_ctx):
    """AC-PUT-WRITE: PUT writes the changes back to the env's .env file."""
    client, _, _, env_dir = client_ctx
    resp = client.put(
        "/api/projects/test-proj/environments/prd/env-vars",
        json={"vars": [
            {"key": "DB_HOST", "value": "db.internal"},
            {"key": "DB_PORT", "value": "5432"},
        ]},
    )
    assert resp.status_code == 200, resp.text
    on_disk = (env_dir / ".env").read_text()
    assert "DB_HOST=db.internal" in on_disk
    # Unchanged key keeps its inline comment.
    assert "DB_PORT=5432  # default" in on_disk


def test_put_env_vars_appends_new_key(client_ctx):
    """AC-APPEND: a new var via PUT is appended at the end of the .env file."""
    client, _, _, env_dir = client_ctx
    resp = client.put(
        "/api/projects/test-proj/environments/prd/env-vars",
        json={"vars": [
            {"key": "DB_HOST", "value": "localhost"},
            {"key": "DB_PORT", "value": "5432"},
            {"key": "NEW_KEY", "value": "newval"},
        ]},
    )
    assert resp.status_code == 200, resp.text
    lines = (env_dir / ".env").read_text().splitlines()
    assert lines[-1] == "NEW_KEY=newval"


def test_put_env_vars_deletes_removed_key(client_ctx):
    """AC-DELETE: omitting a row removes it from the .env file."""
    client, _, _, env_dir = client_ctx
    resp = client.put(
        "/api/projects/test-proj/environments/prd/env-vars",
        json={"vars": [{"key": "DB_HOST", "value": "localhost"}]},
    )
    assert resp.status_code == 200, resp.text
    assert "DB_PORT" not in (env_dir / ".env").read_text()


def test_values_returned_plaintext(client_ctx):
    """AC-PLAINTEXT: server never masks values — bullets are display-only."""
    client, _, _, _ = client_ctx
    resp = client.get("/api/projects/test-proj/environments/prd/env-vars")
    body = resp.text
    assert "localhost" in body
    assert "•" not in body  # no bullet masking server-side


def test_put_empty_key_blocked(client_ctx):
    """AC-EMPTYKEY: an empty KEY is rejected with a validation error."""
    client, _, _, env_dir = client_ctx
    before = (env_dir / ".env").read_text()
    resp = client.put(
        "/api/projects/test-proj/environments/prd/env-vars",
        json={"vars": [
            {"key": "", "value": "orphan"},
            {"key": "DB_HOST", "value": "localhost"},
        ]},
    )
    assert resp.status_code == 400, resp.text
    # File must be untouched on a rejected save.
    assert (env_dir / ".env").read_text() == before


def test_put_write_failure_returns_error(client_ctx):
    """AC-WRITEFAIL: a write failure surfaces a non-2xx error."""
    client, srv, _, _ = client_ctx
    import env_file as ef

    def boom(*a, **k):
        raise OSError("Permission denied")

    with patch.object(ef, "write_env_vars", boom):
        resp = client.put(
            "/api/projects/test-proj/environments/prd/env-vars",
            json={"vars": [{"key": "DB_HOST", "value": "x"}]},
        )
    assert resp.status_code >= 500, resp.text


# ── Frontend structure (project.html) ────────────────────────────────────────

def _html():
    return (STATIC_DIR / "project.html").read_text()


def test_env_vars_section_present():
    """AC-SECTION: an Environment Variables section exists in settings."""
    html = _html()
    assert "Environment Variables" in html
    assert "ps-envvars-card" in html or "ps-env-vars-card" in html


def test_read_view_masks_values():
    """AC-TABLE: read view masks values with bullets by default."""
    html = _html()
    assert "••••" in html or "••••" in html


def test_eye_toggle_present():
    """AC-EYE: a per-row eye toggle reveals/masks a single row."""
    html = _html()
    assert "ti-eye" in html
    assert "envVarToggleReveal" in html or "toggleEnvVarReveal" in html


def test_edit_button_present():
    """AC-EDIT: an Edit button switches the table to edit mode."""
    html = _html()
    assert "envVarsEdit" in html or "envVarEdit" in html


def test_add_row_control_present():
    """AC-ADDROW: edit mode offers an Add row control."""
    html = _html()
    assert "envVarAddRow" in html or "addEnvVarRow" in html


def test_delete_row_icon_present():
    """AC-DELROW: edit mode shows a per-row Delete (trash) icon."""
    html = _html()
    assert "ti-trash" in html
    assert "envVarDeleteRow" in html or "deleteEnvVarRow" in html


def test_save_calls_put_endpoint():
    """AC-SAVE: Save posts to the PUT env-vars endpoint."""
    html = _html()
    assert "envVarsSave" in html or "saveEnvVars" in html
    assert "/env-vars" in html
    assert "method: 'PUT'" in html or 'method: "PUT"' in html


def test_success_toast_and_error_handling():
    """AC-TOAST: success shows a toast; failure shows an inline error."""
    html = _html()
    assert "showToast" in html
    assert "envVarsLoad" in html or "loadEnvVars" in html


def test_cancel_button_present():
    """AC-CANCEL: a Cancel button discards edits and returns to read view."""
    html = _html()
    assert "envVarsCancel" in html or "cancelEnvVars" in html


def test_client_empty_key_validation_present():
    """AC-VALIDATE: client blocks Save on empty KEY (validation hint)."""
    html = _html()
    assert "envvar-key-error" in html or "ps-envvar-error" in html
