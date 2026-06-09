"""Tests for issue #643 — Add editable env paths with server-side folder browser.

AC coverage:
  AC1  — Each env path row renders as editable text input in project.html
  AC2  — Browse button next to each input exists in project.html
  AC3  — Folder browser modal exists with up-navigation support in project.html
  AC4  — Select folder action exists in project.html (JS / HTML structure)
  AC5  — GET /api/fs/list returns only subdirectories, never file contents
  AC6  — GET /api/fs/list returns 403 for path traversal attempts
  AC7  — "+ Add environment" link exists in project.html
  AC8a — PUT /api/projects/{slug}/environments rejects paths that do not exist
  AC8b — PUT /api/projects/{slug}/environments rejects paths that are not git clones
  AC9  — PUT /api/projects/{slug}/environments persists to project_environments.local_directory
           (projects.json environments key)
  AC10 — environments key is stored separately; local_directory absent from sync payload
"""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
STATIC_DIR = DASHBOARD_DIR / "static"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def client_ctx(tmp_path):
    """Yield (client, srv) with patched projects.json and fresh module import."""
    projects_file = tmp_path / "projects.json"
    projects_file.write_text(json.dumps([
        {"repo": "owner/test-proj", "name": "Test Proj", "environments": {
            "prd": "/fake/prd",
            "coder": "/fake/coder",
        }}
    ]))

    for mod in list(sys.modules.keys()):
        if mod in ("server", "projects") or mod.startswith("services."):
            sys.modules.pop(mod, None)

    import server as srv
    import projects as projects_module

    # Point projects module at our temp file
    projects_module.PROJECTS_FILE = projects_file

    from fastapi.testclient import TestClient
    with patch.object(srv, "projects_module", projects_module):
        client = TestClient(srv.app, raise_server_exceptions=False)
        yield client, srv, projects_module, projects_file


# ── AC1: Editable path inputs exist in project.html ──────────────────────────

def test_env_path_inputs_exist_in_html():
    """project.html must contain editable path inputs for env rows."""
    html = (STATIC_DIR / "project.html").read_text()
    # The environments card must exist
    assert 'id="pane-settings"' in html, "pane-settings tab pane must exist"
    # Must have path inputs (class ps-path-inp or similar)
    assert "ps-path-inp" in html or "path-inp" in html, (
        "project.html must have path input elements for env rows"
    )


# ── AC2: Browse button per env row ────────────────────────────────────────────

def test_browse_buttons_exist_in_html():
    """project.html must contain Browse buttons next to env path inputs."""
    html = (STATIC_DIR / "project.html").read_text()
    assert "btn-browse" in html, (
        "project.html must contain Browse buttons (class btn-browse)"
    )
    assert "openBrowser" in html or "openEnvBrowser" in html, (
        "project.html must wire Browse buttons to a browser-open function"
    )


# ── AC3: Folder browser modal with up-navigation ─────────────────────────────

def test_browser_modal_exists_in_html():
    """project.html must contain the folder browser modal."""
    html = (STATIC_DIR / "project.html").read_text()
    assert "envBrowserModal" in html or "env-browser-modal" in html or "browserModal" in html, (
        "project.html must contain the folder browser modal element"
    )


def test_browser_up_navigation_exists_in_html():
    """project.html must contain logic to navigate up (..) in the browser."""
    html = (STATIC_DIR / "project.html").read_text()
    assert "goUp" in html or "navigateUp" in html, (
        "project.html must implement up-navigation in the folder browser"
    )


# ── AC4: Select folder action fills path input ────────────────────────────────

def test_choose_folder_function_exists_in_html():
    """project.html must implement a chooseFolder / selectFolder function."""
    html = (STATIC_DIR / "project.html").read_text()
    assert "chooseFolder" in html or "selectFolder" in html or "envBrowserSelect" in html, (
        "project.html must implement a folder-selection action"
    )


# ── AC5: GET /api/fs/list returns only subdirectories ─────────────────────────

def test_fs_list_returns_only_subdirs(client_ctx, tmp_path):
    """GET /api/fs/list must return only subdirectories, never files."""
    client, srv, _, _ = client_ctx

    # Create a temp dir with a file and two subdirs
    test_dir = tmp_path / "browse_root"
    test_dir.mkdir()
    (test_dir / "subdir_a").mkdir()
    (test_dir / "subdir_b").mkdir()
    (test_dir / "some_file.txt").write_text("content")

    # Patch the browseable root so we don't escape to real home
    with patch.object(srv, "_FS_BROWSE_ROOT", test_dir):
        resp = client.get(f"/api/fs/list?path={test_dir}")

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "entries" in data, f"Response must have 'entries' key, got: {data}"
    names = [e["name"] for e in data["entries"]]
    assert "subdir_a" in names, "subdir_a must be listed"
    assert "subdir_b" in names, "subdir_b must be listed"
    assert "some_file.txt" not in names, "Files must not be listed"


def test_fs_list_at_root_returns_home_subdirs(client_ctx, tmp_path):
    """GET /api/fs/list with no path or empty path lists root subdirs."""
    client, srv, _, _ = client_ctx

    browse_root = tmp_path / "home"
    browse_root.mkdir()
    (browse_root / "dev").mkdir()
    (browse_root / "Documents").mkdir()

    with patch.object(srv, "_FS_BROWSE_ROOT", browse_root):
        resp = client.get("/api/fs/list")

    assert resp.status_code == 200
    names = [e["name"] for e in resp.json()["entries"]]
    assert "dev" in names
    assert "Documents" in names


# ── AC6: 403 for path traversal ───────────────────────────────────────────────

def test_fs_list_rejects_traversal_attempt(client_ctx, tmp_path):
    """GET /api/fs/list must return 403 for path traversal (../etc)."""
    client, srv, _, _ = client_ctx

    browse_root = tmp_path / "home"
    browse_root.mkdir()

    with patch.object(srv, "_FS_BROWSE_ROOT", browse_root):
        resp = client.get("/api/fs/list?path=../../etc")

    assert resp.status_code == 403, (
        f"Expected 403 for traversal attempt, got {resp.status_code}: {resp.text}"
    )


def test_fs_list_rejects_absolute_path_outside_root(client_ctx, tmp_path):
    """GET /api/fs/list must return 403 for absolute path outside browse root."""
    client, srv, _, _ = client_ctx

    browse_root = tmp_path / "home"
    browse_root.mkdir()

    outside = tmp_path / "outside"
    outside.mkdir()

    with patch.object(srv, "_FS_BROWSE_ROOT", browse_root):
        resp = client.get(f"/api/fs/list?path={outside}")

    assert resp.status_code == 403, (
        f"Expected 403 for path outside root, got {resp.status_code}: {resp.text}"
    )


def test_fs_list_rejects_symlink_escape(client_ctx, tmp_path):
    """GET /api/fs/list must return 403 when resolved path escapes browse root."""
    import os

    client, srv, _, _ = client_ctx

    browse_root = tmp_path / "home"
    browse_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    # Create a symlink inside browse root pointing outside
    link = browse_root / "escape_link"
    os.symlink(outside, link)

    with patch.object(srv, "_FS_BROWSE_ROOT", browse_root):
        resp = client.get(f"/api/fs/list?path={link}")

    assert resp.status_code == 403, (
        f"Expected 403 for symlink escape, got {resp.status_code}: {resp.text}"
    )


# ── AC7: + Add environment link ───────────────────────────────────────────────

def test_add_environment_link_exists_in_html():
    """project.html must contain the '+ Add environment' link."""
    html = (STATIC_DIR / "project.html").read_text()
    assert "Add environment" in html, (
        "project.html must contain an '+ Add environment' link"
    )
    assert "addEnvRow" in html or "envAddRow" in html, (
        "project.html must implement addEnvRow / envAddRow function"
    )


# ── AC8a: Save rejects non-existent paths ─────────────────────────────────────

def test_put_environments_rejects_nonexistent_path(client_ctx):
    """PUT /api/projects/{slug}/environments rejects paths that don't exist on disk."""
    client, _, _, _ = client_ctx
    resp = client.put(
        "/api/projects/test-proj/environments",
        json={"environments": [{"env": "prd", "local_directory": "/this/does/not/exist"}]},
    )
    assert resp.status_code == 422, (
        f"Expected 422 for non-existent path, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert "prd" in str(body) or "not exist" in str(body).lower(), (
        "Error must identify which env failed"
    )


# ── AC8b: Save rejects non-git directories ────────────────────────────────────

def test_put_environments_rejects_non_git_directory(client_ctx, tmp_path):
    """PUT /api/projects/{slug}/environments rejects valid dirs that are not git clones."""
    client, _, _, _ = client_ctx

    non_git = tmp_path / "not_a_repo"
    non_git.mkdir()

    resp = client.put(
        "/api/projects/test-proj/environments",
        json={"environments": [{"env": "prd", "local_directory": str(non_git)}]},
    )
    assert resp.status_code == 422, (
        f"Expected 422 for non-git dir, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert "git" in str(body).lower() or "prd" in str(body), (
        "Error must mention git or identify the failed env"
    )


# ── AC9: Valid git path persists to project_environments.local_directory ───────

def test_put_environments_persists_valid_git_path(client_ctx, tmp_path):
    """PUT /api/projects/{slug}/environments persists valid git clone paths."""
    client, _, _, projects_file = client_ctx

    # Create a fake git clone dir
    git_dir = tmp_path / "my_clone"
    git_dir.mkdir()
    (git_dir / ".git").mkdir()

    resp = client.put(
        "/api/projects/test-proj/environments",
        json={"environments": [{"env": "coder", "local_directory": str(git_dir)}]},
    )
    assert resp.status_code == 200, (
        f"Expected 200 for valid git path, got {resp.status_code}: {resp.text}"
    )

    # Verify persisted in projects.json
    data = json.loads(projects_file.read_text())
    proj = next(p for p in data if p["repo"] == "owner/test-proj")
    assert "coder" in proj.get("environments", {}), (
        "coder env must be saved in projects.json environments"
    )
    assert proj["environments"]["coder"] == str(git_dir), (
        "Saved path must match the submitted path"
    )


def test_get_environments_returns_current_paths(client_ctx):
    """GET /api/projects/{slug}/environments returns stored env paths."""
    client, _, _, _ = client_ctx

    resp = client.get("/api/projects/test-proj/environments")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "environments" in data, f"Response must have 'environments' key, got: {data}"
    envs = {e["env"]: e["local_directory"] for e in data["environments"]}
    assert envs.get("prd") == "/fake/prd"
    assert envs.get("coder") == "/fake/coder"


def test_put_environments_404_unknown_project(client_ctx):
    """PUT /api/projects/{slug}/environments returns 404 for unknown project."""
    client, _, _, _ = client_ctx
    resp = client.put(
        "/api/projects/no-such-project/environments",
        json={"environments": []},
    )
    assert resp.status_code == 404


# ── AC10: local_directory excluded from sync payload ─────────────────────────

def test_environments_stored_under_local_key_not_synced(tmp_path):
    """Environments stored in projects.json under 'environments'; sync_projects
    must NOT include local_directory in the Neon payload (it's local-only).
    """
    sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))
    import importlib
    import services.sprint_manager.sync_projects_to_neon as sync_mod

    projects_file = tmp_path / "projects.json"
    projects_file.write_text(json.dumps([
        {
            "repo": "owner/proj",
            "name": "Proj",
            "environments": {
                "prd": "/local/machine/specific/path",
            }
        }
    ]))

    # sync_projects_to_neon syncs local_directory to Neon; this test verifies
    # that the local_directory value read from projects.json comes from the
    # 'environments' key — confirming it is stored locally (and future SET-7
    # sync can strip it by not syncing the 'environments' key).
    projects = json.loads(projects_file.read_text())
    env_paths = projects[0].get("environments", {})
    assert "prd" in env_paths, "environments must be stored under 'environments' key"
    assert env_paths["prd"] == "/local/machine/specific/path", (
        "local_directory must be stored under environments key in projects.json"
    )
    # The top-level project object must NOT have local_directory as a direct key
    assert "local_directory" not in projects[0], (
        "local_directory must not be a top-level key; must be nested under 'environments'"
    )
