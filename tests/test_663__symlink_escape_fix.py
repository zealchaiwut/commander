"""Tests for issue #663 – Fix symlink escape vulnerability in /api/fs/list endpoint.

AC coverage:
  AC1  — Symlink inside root pointing outside MUST NOT appear in directory listing
  AC2  — Navigation through an intermediate symlink that escapes root → 403
  AC3  — Escape-then-reenter: path through outside-root symlink that resolves back
          inside root MUST still return 403 (the current resolve() check misses this)
  AC4  — Normal (non-symlink) subdirectories are still listed and navigable
"""

import os
import sys
import json
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))


@pytest.fixture()
def client_ctx(tmp_path):
    projects_file = tmp_path / "projects.json"
    projects_file.write_text(json.dumps([
        {"repo": "owner/test-proj", "name": "Test Proj", "environments": {}}
    ]))

    for mod in list(sys.modules.keys()):
        if mod in ("server", "projects") or mod.startswith("services."):
            sys.modules.pop(mod, None)

    import server as srv
    import projects as projects_module
    from routers import settings_service

    projects_module.PROJECTS_FILE = projects_file

    from fastapi.testclient import TestClient
    with patch.object(srv, "projects_module", projects_module):
        client = TestClient(srv.app, raise_server_exceptions=False)
        yield client, srv, projects_module, projects_file, settings_service


# ── AC1: Symlink not shown in directory listing ────────────────────────────────

def test_fs_list_symlink_escape_not_in_listing(client_ctx, tmp_path):
    """Symlink inside browse root pointing outside MUST NOT appear as an entry."""
    client, srv, _, _, settings_service = client_ctx

    browse_root = tmp_path / "home"
    browse_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    normal_dir = browse_root / "normal"
    normal_dir.mkdir()

    # Symlink inside root pointing outside
    link = browse_root / "escape_link"
    os.symlink(outside, link)

    with patch.object(settings_service, "_FS_BROWSE_ROOT", browse_root):
        resp = client.get(f"/api/fs/list?path={browse_root}")

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    names = [e["name"] for e in resp.json()["entries"]]
    assert "escape_link" not in names, (
        "Symlink pointing outside root must NOT appear in directory listing"
    )
    assert "normal" in names, (
        "Normal subdirectory must still appear in listing"
    )


# ── AC2: Navigation through intermediate escaping symlink → 403 ───────────────

def test_fs_list_intermediate_symlink_escape_blocked(client_ctx, tmp_path):
    """Path component that is a symlink escaping root → 403.

    /root/link_out -> /outside
    Requesting /root/link_out/subdir must return 403 even though the
    subdir itself may not exist; the symlink escapes root.
    """
    client, srv, _, _, settings_service = client_ctx

    browse_root = tmp_path / "home"
    browse_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "subdir").mkdir()

    link_out = browse_root / "link_out"
    os.symlink(outside, link_out)

    with patch.object(settings_service, "_FS_BROWSE_ROOT", browse_root):
        resp = client.get(f"/api/fs/list?path={link_out / 'subdir'}")

    assert resp.status_code == 403, (
        f"Navigation through intermediate escaping symlink must return 403, got {resp.status_code}"
    )


# ── AC3: Escape-then-reenter via chained symlinks → 403 ───────────────────────

def test_fs_list_escape_reenter_via_chained_symlinks_blocked(client_ctx, tmp_path):
    """Symlink chain that exits root and re-enters MUST return 403.

    /root/link_out -> /outside
    /outside/link_back -> /root/safe
    Requesting /root/link_out/link_back resolves to /root/safe (inside root),
    so the old resolve()-only check passes — but the path traversed /outside,
    which is outside root. The new component-wise check must block it.
    """
    client, srv, _, _, settings_service = client_ctx

    browse_root = tmp_path / "home"
    browse_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    safe = browse_root / "safe"
    safe.mkdir()

    # first hop: root → outside
    link_out = browse_root / "link_out"
    os.symlink(outside, link_out)

    # second hop: outside → back into root
    link_back = outside / "link_back"
    os.symlink(safe, link_back)

    with patch.object(settings_service, "_FS_BROWSE_ROOT", browse_root):
        resp = client.get(f"/api/fs/list?path={link_out / 'link_back'}")

    assert resp.status_code == 403, (
        f"Escape-then-reenter symlink chain must return 403, got {resp.status_code}: {resp.text}"
    )


# ── AC4: Normal dirs still work after the fix ─────────────────────────────────

def test_fs_list_normal_dirs_unaffected(client_ctx, tmp_path):
    """Normal subdirectories still listed and navigable after the security fix."""
    client, srv, _, _, settings_service = client_ctx

    browse_root = tmp_path / "home"
    browse_root.mkdir()
    subdir = browse_root / "projects"
    subdir.mkdir()
    nested = subdir / "myapp"
    nested.mkdir()

    with patch.object(settings_service, "_FS_BROWSE_ROOT", browse_root):
        # Root listing
        resp = client.get(f"/api/fs/list?path={browse_root}")
        assert resp.status_code == 200
        names = [e["name"] for e in resp.json()["entries"]]
        assert "projects" in names

        # Navigate into subdir
        resp2 = client.get(f"/api/fs/list?path={subdir}")
        assert resp2.status_code == 200
        names2 = [e["name"] for e in resp2.json()["entries"]]
        assert "myapp" in names2
