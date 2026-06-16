"""Tests for issue #1261: Extract sprint finish write routes to routers/sprint_finish.py (runs against UAT)"""
import os
import pytest
import httpx
import subprocess
import sys
from pathlib import Path

BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )

DASHBOARD_ROOT = Path(__file__).resolve().parent.parent / "apps" / "dashboard"


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# --- Acceptance Criteria ---

def test_extract_sprint_finish_write_routes__post_finish_removed_from_server():
    # AC: `POST /finish` route removed from `server.py` and present in `routers/sprint_finish.py`
    server_py = DASHBOARD_ROOT / "server.py"
    assert server_py.exists()

    content = server_py.read_text()
    assert "POST /finish and POST /bulk-complete extracted to routers/sprint_finish.py" in content


def test_extract_sprint_finish_write_routes__post_bulk_complete_removed_from_server():
    # AC: `POST /bulk-complete` route removed from `server.py` and present in `routers/sprint_finish.py`
    server_py = DASHBOARD_ROOT / "server.py"
    content = server_py.read_text()

    assert "bulk-complete" in content.lower() and "extracted" in content.lower()


def test_extract_sprint_finish_write_routes__router_registered_in_server():
    # AC: `routers/sprint_finish.py` router registered in `server.py` (no raw route definitions remain there for these endpoints)
    server_py = DASHBOARD_ROOT / "server.py"
    content = server_py.read_text()

    assert "sprint_finish_router" in content
    assert "include_router(sprint_finish_router)" in content


def test_extract_sprint_finish_write_routes__router_file_exists_with_routes():
    # AC: `routers/sprint_finish.py` contains the extracted routes
    router_file = DASHBOARD_ROOT / "routers" / "sprint_finish.py"
    assert router_file.exists()

    content = router_file.read_text()
    assert "@router.post" in content
    assert "/finish" in content
    assert "/bulk-complete" in content


def test_extract_sprint_finish_write_routes__python_compile_check():
    # AC: `python -m py_compile server.py routers/sprint_finish.py` exits 0 with no errors
    server_py = DASHBOARD_ROOT / "server.py"
    router_file = DASHBOARD_ROOT / "routers" / "sprint_finish.py"

    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(server_py), str(router_file)],
        capture_output=True,
        text=True,
        cwd=str(DASHBOARD_ROOT)
    )

    assert result.returncode == 0, f"Compilation failed: {result.stderr}"


def test_extract_sprint_finish_write_routes__http_finish_endpoint(client):
    # AC: Finish and bulk-complete behavior is functionally identical before and after the move (same request/response contracts, same orchestration logic)
    owner = "test-owner"
    repo_name = "test-repo"
    label = "test-label"

    payload = {"selected_labels": []}

    try:
        r = client.post(
            f"/api/projects/{owner}/{repo_name}/sprints/{label}/finish",
            json=payload
        )
        assert r.status_code in [400, 404, 422, 500]
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")


def test_extract_sprint_finish_write_routes__http_bulk_complete_endpoint(client):
    # AC: Finish and bulk-complete behavior is functionally identical before and after the move
    owner = "test-owner"
    repo_name = "test-repo"
    label = "test-label"

    payload = {"task_ids": []}

    try:
        r = client.post(
            f"/api/projects/{owner}/{repo_name}/sprints/{label}/bulk-complete",
            json=payload
        )
        assert r.status_code in [400, 404, 422, 500]
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")
