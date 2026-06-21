"""Tests for issue #664 — Improve git repository validation in put_project_environments.

AC coverage:
  AC1 — PUT /api/projects/{slug}/environments rejects a directory that has a .git
        folder but is not a valid git repository (git rev-parse fails), returning
        422 with a descriptive error that identifies the env name.
  AC2 — PUT /api/projects/{slug}/environments accepts a properly initialised git
        repository (git init), returning 200.
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
    """Yield (client, srv) with patched projects.json and fresh module import."""
    projects_file = tmp_path / "projects.json"
    projects_data = [
        {"repo": "owner/test-proj", "name": "Test Proj", "environments": {"prd": "/fake/prd"}}
    ]
    projects_file.write_text(json.dumps(projects_data))

    import server as srv
    import projects as projects_module
    projects_module.PROJECTS_FILE = projects_file

    from fastapi.testclient import TestClient
    # Patch load_projects on the shared projects module — routers import
    # `projects` directly (refactor #1267), so patching srv.projects_module (and
    # popping/reimporting) left the already-registered routers on the old module.
    with patch.object(projects_module, "load_projects", return_value=list(projects_data)):
        client = TestClient(srv.app, raise_server_exceptions=False)
        yield client, srv, projects_module, projects_file


# ── AC1: Rejects .git dir that is not a valid git repo ───────────────────────

def test_put_environments_rejects_broken_git_repo(client_ctx, tmp_path):
    """PUT rejects a directory with .git folder but invalid repo state (422)."""
    client, _, _, _ = client_ctx

    broken_repo = tmp_path / "broken_repo"
    broken_repo.mkdir()
    # .git dir exists but is empty — not a valid git repository
    (broken_repo / ".git").mkdir()

    resp = client.put(
        "/api/projects/test-proj/environments",
        json={"environments": [{"env": "coder", "local_directory": str(broken_repo)}]},
    )
    assert resp.status_code == 422, (
        f"Expected 422 for broken git repo, got {resp.status_code}: {resp.text}"
    )
    body = str(resp.json())
    assert "coder" in body, (
        f"Error must identify which env failed ('coder' missing from: {body})"
    )
    # Error message must communicate the repo is invalid/not-valid
    assert any(kw in body.lower() for kw in ("not a valid", "invalid", "not valid", "misconfigured", "broken")), (
        f"Error must describe the repo as invalid/misconfigured, got: {body}"
    )


def test_put_environments_error_includes_env_name_for_broken_repo(client_ctx, tmp_path):
    """Error detail for a broken git repo must include the env name."""
    client, _, _, _ = client_ctx

    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / ".git").mkdir()

    resp = client.put(
        "/api/projects/test-proj/environments",
        json={"environments": [{"env": "my-env", "local_directory": str(broken)}]},
    )
    assert resp.status_code == 422
    assert "my-env" in str(resp.json()), "Detail must include env name 'my-env'"


# ── AC2: Accepts a properly initialised git repository ───────────────────────

def test_put_environments_accepts_valid_git_repo(client_ctx, tmp_path):
    """PUT accepts a properly initialised git repository and returns 200."""
    client, _, _, projects_file = client_ctx

    valid_repo = tmp_path / "valid_repo"
    valid_repo.mkdir()
    subprocess.run(
        ["git", "init", str(valid_repo)],
        capture_output=True,
        check=True,
    )

    resp = client.put(
        "/api/projects/test-proj/environments",
        json={"environments": [{"env": "prd", "local_directory": str(valid_repo)}]},
    )
    assert resp.status_code == 200, (
        f"Expected 200 for valid git repo, got {resp.status_code}: {resp.text}"
    )
    data = json.loads(projects_file.read_text())
    proj = next(p for p in data if p["repo"] == "owner/test-proj")
    assert proj["environments"]["prd"] == str(valid_repo), (
        "Valid repo path must be persisted in projects.json"
    )
