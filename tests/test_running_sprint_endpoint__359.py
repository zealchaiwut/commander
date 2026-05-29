"""Tests for issue #359 — Add GET /api/projects/{project}/running-sprint endpoint.

Verifies all three response paths:
  - 200: sprint is running (live PID file)
  - 204: no sprint running (no PID file or stale PID file)
  - 404: project does not exist
"""
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SERVER_PY = DASHBOARD_DIR / "server.py"

if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))


# ── helpers ───────────────────────────────────────────────────────────────────

def _get_test_client():
    if "server" in sys.modules:
        del sys.modules["server"]
    from fastapi.testclient import TestClient
    import server as srv
    return TestClient(srv.app, raise_server_exceptions=False), srv


@pytest.fixture()
def client_and_srv():
    client, srv = _get_test_client()
    yield client, srv
    if "server" in sys.modules:
        del sys.modules["server"]


# ── AC-1: Route exists in server.py ──────────────────────────────────────────

def test_ac1_route_decorator_present():
    """The @app.get decorator for the running-sprint route must be in server.py."""
    src = SERVER_PY.read_text()
    assert '@app.get("/api/projects/{project}/running-sprint")' in src, (
        'Expected @app.get("/api/projects/{project}/running-sprint") in server.py'
    )


def test_ac1_response_import_present():
    """server.py must import Response from fastapi.responses for 204 support."""
    src = SERVER_PY.read_text()
    assert "Response" in src, (
        "server.py must import Response from fastapi.responses to return 204"
    )


# ── AC-2: 200 with {label, started_at, pid} when sprint is running ───────────

def test_ac2_returns_200_when_sprint_running():
    """Returns 200 with label, started_at, pid when a live PID file exists."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        sprints_dir = tmp / ".commander" / "sprints"
        sprints_dir.mkdir(parents=True)

        # Write a live PID (current process is alive)
        live_pid = os.getpid()
        pid_file = sprints_dir / "sprint-99-pid"
        pid_file.write_text(str(live_pid))

        fake_project = [{"repo": "owner/testproj", "name": "Test Project"}]

        client, srv = _get_test_client()
        with patch.object(srv.projects_module, "load_projects", return_value=fake_project), \
             patch.object(srv, "_project_root_path", return_value=tmp):
            response = client.get("/api/projects/testproj/running-sprint")

    assert response.status_code == 200, (
        f"Expected 200 when sprint is running, got {response.status_code}"
    )
    data = response.json()
    assert "label" in data, f"Response must contain 'label' field: {data}"
    assert "started_at" in data, f"Response must contain 'started_at' field: {data}"
    assert "pid" in data, f"Response must contain 'pid' field: {data}"
    assert data["label"] == "sprint-99", f"Expected label 'sprint-99', got {data['label']!r}"
    assert isinstance(data["pid"], int), f"pid must be an int, got {type(data['pid'])}"
    assert data["pid"] == live_pid, f"pid must match file content {live_pid}, got {data['pid']}"


def test_ac2_started_at_is_iso8601():
    """started_at in the 200 response must be an ISO 8601 UTC timestamp."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        sprints_dir = tmp / ".commander" / "sprints"
        sprints_dir.mkdir(parents=True)

        live_pid = os.getpid()
        (sprints_dir / "sprint-99-pid").write_text(str(live_pid))

        fake_project = [{"repo": "owner/testproj", "name": "Test Project"}]

        client, srv = _get_test_client()
        with patch.object(srv.projects_module, "load_projects", return_value=fake_project), \
             patch.object(srv, "_project_root_path", return_value=tmp):
            response = client.get("/api/projects/testproj/running-sprint")

    assert response.status_code == 200
    started_at = response.json()["started_at"]
    assert isinstance(started_at, str), "started_at must be a string"
    try:
        dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    except ValueError as exc:
        pytest.fail(f"started_at is not valid ISO 8601: {started_at!r} — {exc}")
    assert dt.tzinfo is not None, f"started_at must include timezone info; got {started_at!r}"


def test_ac2_response_has_exactly_three_fields():
    """200 response body must have exactly: label, started_at, pid."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        sprints_dir = tmp / ".commander" / "sprints"
        sprints_dir.mkdir(parents=True)
        (sprints_dir / "sprint-1-pid").write_text(str(os.getpid()))

        fake_project = [{"repo": "owner/testproj", "name": "Test Project"}]

        client, srv = _get_test_client()
        with patch.object(srv.projects_module, "load_projects", return_value=fake_project), \
             patch.object(srv, "_project_root_path", return_value=tmp):
            response = client.get("/api/projects/testproj/running-sprint")

    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {"label", "started_at", "pid"}, (
        f"200 body must have exactly label, started_at, pid; got keys: {set(data.keys())}"
    )


# ── AC-3: 204 when no sprint is running ──────────────────────────────────────

def test_ac3_returns_204_when_no_pid_file():
    """Returns 204 when sprints dir exists but contains no PID files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        sprints_dir = tmp / ".commander" / "sprints"
        sprints_dir.mkdir(parents=True)

        fake_project = [{"repo": "owner/testproj", "name": "Test Project"}]

        client, srv = _get_test_client()
        with patch.object(srv.projects_module, "load_projects", return_value=fake_project), \
             patch.object(srv, "_project_root_path", return_value=tmp):
            response = client.get("/api/projects/testproj/running-sprint")

    assert response.status_code == 204, (
        f"Expected 204 when no sprint is running, got {response.status_code}"
    )
    assert not response.content, f"204 response must have empty body, got: {response.content!r}"


def test_ac3_returns_204_when_sprints_dir_missing():
    """Returns 204 when the .commander/sprints directory does not exist at all."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        # Do NOT create the sprints dir

        fake_project = [{"repo": "owner/testproj", "name": "Test Project"}]

        client, srv = _get_test_client()
        with patch.object(srv.projects_module, "load_projects", return_value=fake_project), \
             patch.object(srv, "_project_root_path", return_value=tmp):
            response = client.get("/api/projects/testproj/running-sprint")

    assert response.status_code == 204, (
        f"Expected 204 when sprints dir missing, got {response.status_code}"
    )


# ── AC-4: 404 when project does not exist ────────────────────────────────────

def test_ac4_returns_404_for_unknown_project():
    """Returns 404 when the project slug is not in the registered projects list."""
    fake_project = [{"repo": "owner/realproject", "name": "Real Project"}]

    client, srv = _get_test_client()
    with patch.object(srv.projects_module, "load_projects", return_value=fake_project):
        response = client.get("/api/projects/does-not-exist/running-sprint")

    assert response.status_code == 404, (
        f"Expected 404 for unknown project, got {response.status_code}"
    )


def test_ac4_returns_404_when_no_projects_registered():
    """Returns 404 when load_projects returns an empty list."""
    client, srv = _get_test_client()
    with patch.object(srv.projects_module, "load_projects", return_value=[]):
        response = client.get("/api/projects/commander/running-sprint")

    assert response.status_code == 404, (
        f"Expected 404 when no projects registered, got {response.status_code}"
    )


# ── AC-5 / AC-6: Stale PID files (dead process) treated as not running ───────

def test_ac6_stale_pid_file_returns_204():
    """Returns 204 when PID file exists but the process is no longer alive."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        sprints_dir = tmp / ".commander" / "sprints"
        sprints_dir.mkdir(parents=True)

        # Use a PID that is guaranteed not to exist
        dead_pid = 99999999
        (sprints_dir / "sprint-dead-pid").write_text(str(dead_pid))

        fake_project = [{"repo": "owner/testproj", "name": "Test Project"}]

        client, srv = _get_test_client()
        with patch.object(srv.projects_module, "load_projects", return_value=fake_project), \
             patch.object(srv, "_project_root_path", return_value=tmp), \
             patch("os.kill", side_effect=ProcessLookupError):
            response = client.get("/api/projects/testproj/running-sprint")

    assert response.status_code == 204, (
        f"Expected 204 for stale PID file, got {response.status_code}"
    )


def test_ac6_stale_pid_file_cleaned_up():
    """Stale PID files should be removed after the endpoint detects they are dead."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        sprints_dir = tmp / ".commander" / "sprints"
        sprints_dir.mkdir(parents=True)

        dead_pid = 99999999
        pid_file = sprints_dir / "sprint-stale-pid"
        pid_file.write_text(str(dead_pid))
        assert pid_file.exists(), "PID file must exist before the request"

        fake_project = [{"repo": "owner/testproj", "name": "Test Project"}]

        client, srv = _get_test_client()
        with patch.object(srv.projects_module, "load_projects", return_value=fake_project), \
             patch.object(srv, "_project_root_path", return_value=tmp), \
             patch("os.kill", side_effect=ProcessLookupError):
            client.get("/api/projects/testproj/running-sprint")

        # The stale file must have been cleaned up
        assert not pid_file.exists(), (
            "Stale PID file must be cleaned up after detecting dead process"
        )


# ── AC-8: No new schema or files ─────────────────────────────────────────────

def test_ac8_only_server_py_modified():
    """Only server.py should be modified in this feature commit — no new files added."""
    import subprocess
    result = subprocess.run(
        ["git", "diff", "sprint/sprint-25...HEAD", "--name-only"],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    changed = set(result.stdout.splitlines())
    test_files = {f for f in changed if f.startswith("tests/")}
    non_test = changed - test_files
    expected_non_test = {"apps/dashboard/server.py"}
    unexpected = non_test - expected_non_test
    assert not unexpected, (
        f"Only server.py should be modified (no new files/schema); unexpected: {unexpected}"
    )


def test_ac8_no_new_db_tables():
    """No new database tables should be introduced — endpoint reads existing state only."""
    src = SERVER_PY.read_text()
    # The route body should not contain any CREATE TABLE or ORM model references
    # Find the function body for get_running_sprint
    start = src.find("def get_running_sprint(")
    end = src.find("\n\n\n", start)
    if end == -1:
        end = len(src)
    func_body = src[start:end]
    assert "CREATE TABLE" not in func_body.upper(), "Route must not create new DB tables"
    assert "Base.metadata" not in func_body, "Route must not reference ORM metadata"


# ── Code structure sanity ─────────────────────────────────────────────────────

def test_route_is_synchronous():
    """get_running_sprint must be a synchronous def (not async) — it does no I/O awaiting."""
    src = SERVER_PY.read_text()
    assert "def get_running_sprint(" in src, "get_running_sprint function must exist"
    # Must not be async def
    assert "async def get_running_sprint(" not in src, (
        "get_running_sprint should be synchronous (def, not async def)"
    )


def test_route_follows_project_scoped_pattern():
    """Route must be registered under /api/projects/{project}/... consistent with other project routes."""
    src = SERVER_PY.read_text()
    assert '"/api/projects/{project}/running-sprint"' in src, (
        "Route path must be /api/projects/{project}/running-sprint"
    )
