"""Tests for issue #1261: Extract Sprint Finish Write Routes to routers/sprint_finish.py.

Extracts the POST write handlers from server.py into the existing router:
  POST /api/projects/{owner}/{repo_name}/sprints/{label}/finish
  POST /api/projects/{owner}/{repo_name}/sprints/{label}/bulk-complete

AC verified:
  AC1 - POST /finish route removed from server.py, present in routers/sprint_finish.py
  AC2 - POST /bulk-complete route removed from server.py, present in routers/sprint_finish.py
  AC3 - routers/sprint_finish.py router registered in server.py (no raw route defs remain)
  AC4 - Behavior functionally identical (same request/response contracts)
  AC5 - python -m py_compile server.py routers/sprint_finish.py exits 0
  AC6 - No new routes added to server.py beyond router registration
"""
import os
import subprocess
import httpx
import pytest
from pathlib import Path

BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )

DASHBOARD_ROOT = Path(__file__).resolve().parent.parent / "apps" / "dashboard"
ROUTER_FILE = DASHBOARD_ROOT / "routers" / "sprint_finish.py"
SERVER_FILE = DASHBOARD_ROOT / "server.py"


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# ── AC1: POST /finish removed from server.py, present in router ───────────────

def test_server_has_no_direct_finish_post_route():
    """AC1: server.py has no @app.post decorator for the /finish route."""
    content = SERVER_FILE.read_text()
    assert '@app.post("/api/projects/{owner}/{repo_name}/sprints/{label}/finish")' not in content, \
        "server.py still defines the POST /finish route handler directly — must be removed"


def test_sprint_finish_router_has_finish_post_route():
    """AC1: routers/sprint_finish.py contains a @router.post for /finish."""
    content = ROUTER_FILE.read_text()
    assert "finish\")" in content or '/finish"' in content, \
        "POST /finish route handler not found in sprint_finish.py"
    assert "@router.post" in content, "@router.post not found in sprint_finish.py"


def test_sprint_finish_router_has_finish_post_handler():
    """AC1: sprint_finish.py defines the finish_sprint async function."""
    content = ROUTER_FILE.read_text()
    assert "finish_sprint" in content, \
        "finish_sprint handler function not found in sprint_finish.py"


# ── AC2: POST /bulk-complete removed from server.py, present in router ────────

def test_server_has_no_direct_bulk_complete_post_route():
    """AC2: server.py has no @app.post decorator for /bulk-complete."""
    content = SERVER_FILE.read_text()
    assert '@app.post("/api/projects/{owner}/{repo_name}/sprints/{label}/bulk-complete")' not in content, \
        "server.py still defines the POST /bulk-complete route handler directly — must be removed"


def test_sprint_finish_router_has_bulk_complete_post_route():
    """AC2: routers/sprint_finish.py contains a @router.post for /bulk-complete."""
    content = ROUTER_FILE.read_text()
    assert "bulk-complete\")" in content or 'bulk-complete"' in content, \
        "POST /bulk-complete route not found in sprint_finish.py"


def test_sprint_finish_router_has_bulk_complete_handler():
    """AC2: sprint_finish.py defines the bulk_complete_sprint async function."""
    content = ROUTER_FILE.read_text()
    assert "bulk_complete_sprint" in content, \
        "bulk_complete_sprint handler function not found in sprint_finish.py"


# ── AC3: Router registered in server.py, no raw route defs remain ─────────────

def test_server_imports_sprint_finish_router():
    """AC3: server.py references sprint_finish_router."""
    content = SERVER_FILE.read_text()
    assert "sprint_finish_router" in content, \
        "sprint_finish_router not referenced in server.py"


def test_server_mounts_sprint_finish_router():
    """AC3: server.py calls app.include_router(sprint_finish_router)."""
    content = SERVER_FILE.read_text()
    assert "app.include_router(sprint_finish_router)" in content, \
        "app.include_router(sprint_finish_router) not found in server.py"


# ── AC4: Behavior functionally identical ─────────────────────────────────────

def test_finish_post_without_body_returns_422_not_404(client):
    """AC4: POST /finish with no body returns 422 validation error (not 404 meaning unrouted)."""
    try:
        r = client.post("/api/projects/zealchaiwut/commander/sprints/sprint-85/finish")
        assert r.status_code != 404, \
            f"Got 404 — POST /finish route not mounted in router: {r.text}"
        assert r.status_code in (400, 422), \
            f"Expected 400/422 for missing body, got {r.status_code}: {r.text}"
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")


def test_finish_post_unconfirmed_returns_400(client):
    """AC4: POST /finish with confirmed=false returns 400 (same validation as before)."""
    try:
        r = client.post(
            "/api/projects/zealchaiwut/commander/sprints/sprint-85/finish",
            json={"confirmed": False},
        )
        assert r.status_code != 404, \
            f"Got 404 — POST /finish route not mounted: {r.text}"
        assert r.status_code == 400, \
            f"Expected 400 for confirmed=false, got {r.status_code}: {r.text}"
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")


def test_finish_post_invalid_label_returns_400(client):
    """AC4: POST /finish with invalid sprint label returns 400."""
    try:
        r = client.post(
            "/api/projects/zealchaiwut/commander/sprints/not-a-sprint/finish",
            json={"confirmed": True},
        )
        assert r.status_code != 404, \
            f"Got 404 — POST /finish route not mounted: {r.text}"
        assert r.status_code == 400, \
            f"Expected 400 for invalid sprint label, got {r.status_code}: {r.text}"
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")


def test_bulk_complete_post_without_body_returns_422_not_404(client):
    """AC4: POST /bulk-complete with no body returns 422 (not 404)."""
    try:
        r = client.post("/api/projects/zealchaiwut/commander/sprints/sprint-85/bulk-complete")
        assert r.status_code != 404, \
            f"Got 404 — POST /bulk-complete route not mounted: {r.text}"
        assert r.status_code in (400, 422), \
            f"Expected 400/422 for missing body, got {r.status_code}: {r.text}"
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")


def test_bulk_complete_post_unconfirmed_returns_400(client):
    """AC4: POST /bulk-complete with confirmed=false returns 400."""
    try:
        r = client.post(
            "/api/projects/zealchaiwut/commander/sprints/sprint-85/bulk-complete",
            json={"confirmed": False},
        )
        assert r.status_code != 404, \
            f"Got 404 — POST /bulk-complete route not mounted: {r.text}"
        assert r.status_code == 400, \
            f"Expected 400 for confirmed=false, got {r.status_code}: {r.text}"
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")


def test_bulk_complete_post_invalid_label_returns_400(client):
    """AC4: POST /bulk-complete with invalid sprint label returns 400."""
    try:
        r = client.post(
            "/api/projects/zealchaiwut/commander/sprints/not-a-sprint/bulk-complete",
            json={"confirmed": True},
        )
        assert r.status_code != 404, \
            f"Got 404 — POST /bulk-complete route not mounted: {r.text}"
        assert r.status_code == 400, \
            f"Expected 400 for invalid sprint label, got {r.status_code}: {r.text}"
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")


# ── AC5: py_compile passes ────────────────────────────────────────────────────

def test_sprint_finish_router_compiles():
    """AC5: py_compile on routers/sprint_finish.py exits 0."""
    result = subprocess.run(
        ["python", "-m", "py_compile", str(ROUTER_FILE)],
        cwd=str(DASHBOARD_ROOT),
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, \
        f"sprint_finish.py compilation failed:\n{result.stderr.decode()}"


def test_server_compiles():
    """AC5: py_compile on server.py exits 0."""
    result = subprocess.run(
        ["python", "-m", "py_compile", str(SERVER_FILE)],
        cwd=str(DASHBOARD_ROOT),
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, \
        f"server.py compilation failed:\n{result.stderr.decode()}"


# ── AC6: No new routes added to server.py ─────────────────────────────────────

def test_server_line_count_decreased():
    """AC6: server.py line count decreased after removing the two POST handlers.

    Pre-refactor (post issue #1260): 11607 lines.
    Removing ~374 lines of POST handlers gives ~11233.
    Gate: must be strictly less than 11600.
    """
    lines = SERVER_FILE.read_text().splitlines()
    assert len(lines) < 11600, (
        f"server.py has {len(lines)} lines — expected <11600 after extracting POST handlers. "
        "The handlers may not have been removed, or new routes were added."
    )


def test_server_has_no_app_post_for_finish_or_bulk():
    """AC6: server.py contains no @app.post decorators for /finish or /bulk-complete."""
    content = SERVER_FILE.read_text()
    assert 'app.post("/api/projects/{owner}/{repo_name}/sprints/{label}/finish")' not in content, \
        "server.py still has @app.post for /finish"
    assert 'app.post("/api/projects/{owner}/{repo_name}/sprints/{label}/bulk-complete")' not in content, \
        "server.py still has @app.post for /bulk-complete"
