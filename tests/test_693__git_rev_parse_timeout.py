"""Tests for issue #693 — Add timeout to git rev-parse call in put_project_environments.

The `git rev-parse --is-inside-work-tree` subprocess call had no timeout. If git
hangs (e.g. network-mounted .git on an offline NFS), the HTTP request blocks
indefinitely and the dashboard becomes unresponsive.

AC coverage:
  AC1 — The subprocess.run call for `git rev-parse` passes a positive `timeout`
        kwarg, so a hung git process cannot block the request forever.
  AC2 — When the git rev-parse call times out (subprocess.TimeoutExpired), the
        endpoint does not crash/hang; it returns 422 with a descriptive error
        that identifies the failing env name.
"""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))


@pytest.fixture()
def client_ctx(tmp_path):
    """Yield (client, srv, projects_module, projects_file) with fresh import."""
    projects_file = tmp_path / "projects.json"
    projects_file.write_text(json.dumps([
        {"repo": "owner/test-proj", "name": "Test Proj", "environments": {
            "prd": "/fake/prd",
        }}
    ]))

    from _sys_modules_guard import temporary_module_purge

    with temporary_module_purge("server", "projects", prefixes=("services.",)):
        import server as srv
        import projects as projects_module

        projects_module.PROJECTS_FILE = projects_file

        from fastapi.testclient import TestClient
        with patch.object(srv, "projects_module", projects_module):
            client = TestClient(srv.app, raise_server_exceptions=False)
            yield client, srv, projects_module, projects_file


def _make_git_repo(tmp_path, name="repo"):
    """Create a real, valid git working clone so validation reaches rev-parse."""
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
    return repo


# ── AC1: git rev-parse call passes a positive timeout ────────────────────────

def test_git_rev_parse_called_with_timeout(client_ctx, tmp_path):
    """The rev-parse subprocess.run must be invoked with a positive timeout."""
    client, srv, _, _ = client_ctx
    repo = _make_git_repo(tmp_path)

    real_run = subprocess.run
    captured = {}

    def spy_run(cmd, *args, **kwargs):
        # Only capture the rev-parse validation call.
        if isinstance(cmd, (list, tuple)) and "rev-parse" in cmd:
            captured["timeout"] = kwargs.get("timeout")
        return real_run(cmd, *args, **kwargs)

    with patch.object(srv.subprocess, "run", side_effect=spy_run):
        resp = client.put(
            "/api/projects/test-proj/environments",
            json={"environments": [{"env": "prd", "local_directory": str(repo)}]},
        )

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    assert "timeout" in captured, "rev-parse subprocess.run was never called"
    assert captured["timeout"] is not None, "rev-parse call must pass a timeout kwarg"
    assert isinstance(captured["timeout"], (int, float)) and captured["timeout"] > 0, (
        f"timeout must be a positive number, got {captured['timeout']!r}"
    )


# ── AC2: a hung git (TimeoutExpired) yields a graceful 422, not a hang ───────

def test_git_rev_parse_timeout_returns_422(client_ctx, tmp_path):
    """A TimeoutExpired from git rev-parse must surface as a 422, not a crash."""
    client, srv, _, _ = client_ctx
    repo = _make_git_repo(tmp_path)

    def hang_run(cmd, *args, **kwargs):
        if isinstance(cmd, (list, tuple)) and "rev-parse" in cmd:
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 10))
        return subprocess.run(cmd, *args, **kwargs)

    with patch.object(srv.subprocess, "run", side_effect=hang_run):
        resp = client.put(
            "/api/projects/test-proj/environments",
            json={"environments": [{"env": "coder", "local_directory": str(repo)}]},
        )

    assert resp.status_code == 422, (
        f"Expected 422 on git timeout, got {resp.status_code}: {resp.text}"
    )
    body = str(resp.json())
    assert "coder" in body, f"Error must identify the failing env 'coder', got: {body}"
    assert "timed out" in body.lower() or "timeout" in body.lower(), (
        f"Error must communicate a timeout occurred, got: {body}"
    )
