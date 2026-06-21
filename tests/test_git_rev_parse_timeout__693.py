"""Tester AC verification for issue #693.

Add timeout to the `git rev-parse --is-inside-work-tree` subprocess call in
`put_project_environments`. A hung git (e.g. offline NFS-mounted .git) must not
block the HTTP request indefinitely.

This fix is verified in-process because the behaviour under test — the
`timeout` kwarg on subprocess.run and the `subprocess.TimeoutExpired` handler —
is not observable over plain HTTP without fault injection into the subprocess
call. The endpoint is driven via FastAPI's TestClient with subprocess.run
patched to spy on / simulate the timeout.

AC1 — rev-parse subprocess.run is invoked with a positive `timeout` kwarg.
AC2 — a TimeoutExpired surfaces as a graceful 422 naming the failing env,
       instead of hanging or 500-crashing.
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
        yield TestClient(srv.app, raise_server_exceptions=False), srv


def _make_git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
    return repo


def test_git_rev_parse_timeout__rev_parse_passes_positive_timeout(client_ctx, tmp_path):
    # AC1: the rev-parse subprocess.run must carry a positive timeout kwarg.
    client, srv = client_ctx
    repo = _make_git_repo(tmp_path)
    real_run = subprocess.run
    captured = {}

    def spy_run(cmd, *args, **kwargs):
        if isinstance(cmd, (list, tuple)) and "rev-parse" in cmd:
            captured["timeout"] = kwargs.get("timeout")
        return real_run(cmd, *args, **kwargs)

    with patch.object(srv.subprocess, "run", side_effect=spy_run):
        resp = client.put(
            "/api/projects/test-proj/environments",
            json={"environments": [{"env": "prd", "local_directory": str(repo)}]},
        )

    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    assert "timeout" in captured, "rev-parse subprocess.run was never called"
    t = captured["timeout"]
    assert isinstance(t, (int, float)) and t > 0, f"timeout must be a positive number, got {t!r}"


def test_git_rev_parse_timeout__timeout_returns_graceful_422(client_ctx, tmp_path):
    # AC2: a TimeoutExpired must surface as a 422 naming the failing env, no hang.
    client, srv = client_ctx
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

    assert resp.status_code == 422, f"expected 422 on timeout, got {resp.status_code}: {resp.text}"
    body = str(resp.json())
    assert "coder" in body, f"error must name the failing env 'coder', got: {body}"
    assert "timed out" in body.lower() or "timeout" in body.lower(), (
        f"error must communicate a timeout, got: {body}"
    )
