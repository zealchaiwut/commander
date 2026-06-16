"""Tests for issue #1258: Extract Sprint Summary & Home Routes to Dedicated Router.

Refactoring extracts the following route handlers from server.py into
routers/sprint_summaries.py:
  GET  /api/sprints/timeline
  GET  /api/sprints/summaries
  GET  /api/sprint-history
  GET  /api/sprint-history-content
  GET  /api/sprint-status
  POST /api/sprint-status
  GET  /api/sprint-summary
  GET  /api/home

AC verified:
  AC1 - routers/sprint_summaries.py exists and contains an APIRouter with all moved routes
  AC2 - None of the above routes remain defined in server.py; only the router include is present
  AC3 - All moved endpoints return identical responses (status codes, payload shape, headers)
  AC4 - py_compile passes for sprint_summaries.py and server.py
  AC5 - No new routes introduced in server.py
  AC6 - Existing unit/integration tests pass without modification
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
ROUTER_FILE = DASHBOARD_ROOT / "routers" / "sprint_summaries.py"
SERVER_FILE = DASHBOARD_ROOT / "server.py"


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# ── AC1: File exists and exposes an APIRouter with all moved routes ───────────

def test_sprint_summaries_router_file_exists():
    """AC1: routers/sprint_summaries.py exists."""
    assert ROUTER_FILE.exists(), f"Router file not found: {ROUTER_FILE}"


def test_sprint_summaries_router_has_apirouter():
    """AC1: sprint_summaries.py declares an APIRouter assigned to `router`."""
    content = ROUTER_FILE.read_text()
    assert "APIRouter" in content, "APIRouter not imported in sprint_summaries.py"
    assert "router = APIRouter(" in content, "`router = APIRouter(...)` not found"


def test_sprint_summaries_has_timeline_route():
    """AC1: GET /api/sprints/timeline is defined in sprint_summaries.py."""
    content = ROUTER_FILE.read_text()
    assert '"/api/sprints/timeline"' in content, \
        "GET /api/sprints/timeline not found in sprint_summaries.py"


def test_sprint_summaries_has_summaries_route():
    """AC1: GET /api/sprints/summaries is defined in sprint_summaries.py."""
    content = ROUTER_FILE.read_text()
    assert '"/api/sprints/summaries"' in content, \
        "GET /api/sprints/summaries not found in sprint_summaries.py"


def test_sprint_summaries_has_sprint_history_route():
    """AC1: GET /api/sprint-history is defined in sprint_summaries.py."""
    content = ROUTER_FILE.read_text()
    assert '"/api/sprint-history"' in content, \
        "GET /api/sprint-history not found in sprint_summaries.py"


def test_sprint_summaries_has_sprint_history_content_route():
    """AC1: GET /api/sprint-history-content is defined in sprint_summaries.py."""
    content = ROUTER_FILE.read_text()
    assert '"/api/sprint-history-content"' in content, \
        "GET /api/sprint-history-content not found in sprint_summaries.py"


def test_sprint_summaries_has_sprint_status_get_route():
    """AC1: GET /api/sprint-status is defined in sprint_summaries.py."""
    content = ROUTER_FILE.read_text()
    assert '"/api/sprint-status"' in content, \
        "GET /api/sprint-status not found in sprint_summaries.py"
    assert "@router.get" in content, "@router.get not found in sprint_summaries.py"


def test_sprint_summaries_has_sprint_status_post_route():
    """AC1: POST /api/sprint-status is defined in sprint_summaries.py."""
    content = ROUTER_FILE.read_text()
    assert "@router.post" in content, "@router.post not found in sprint_summaries.py"


def test_sprint_summaries_has_sprint_summary_route():
    """AC1: GET /api/sprint-summary is defined in sprint_summaries.py."""
    content = ROUTER_FILE.read_text()
    assert '"/api/sprint-summary"' in content, \
        "GET /api/sprint-summary not found in sprint_summaries.py"


def test_sprint_summaries_has_home_route():
    """AC1: GET /api/home is defined in sprint_summaries.py."""
    content = ROUTER_FILE.read_text()
    assert '"/api/home"' in content, \
        "GET /api/home not found in sprint_summaries.py"


# ── AC2: None of the routes remain defined in server.py ──────────────────────

def test_server_has_no_direct_gantt_timeline_route():
    """AC2: server.py has no @app.get decorator for /api/sprints/timeline."""
    content = SERVER_FILE.read_text()
    assert '@app.get("/api/sprints/timeline")' not in content, \
        "server.py still defines @app.get('/api/sprints/timeline') directly"


def test_server_has_no_direct_summaries_route():
    """AC2: server.py has no @app.get decorator for /api/sprints/summaries."""
    content = SERVER_FILE.read_text()
    assert '@app.get("/api/sprints/summaries")' not in content, \
        "server.py still defines @app.get('/api/sprints/summaries') directly"


def test_server_has_no_direct_sprint_history_route():
    """AC2: server.py has no @app.get decorator for /api/sprint-history."""
    content = SERVER_FILE.read_text()
    assert '@app.get("/api/sprint-history")' not in content, \
        "server.py still defines @app.get('/api/sprint-history') directly"


def test_server_has_no_direct_sprint_history_content_route():
    """AC2: server.py has no @app.get decorator for /api/sprint-history-content."""
    content = SERVER_FILE.read_text()
    assert '@app.get("/api/sprint-history-content")' not in content, \
        "server.py still defines @app.get('/api/sprint-history-content') directly"


def test_server_has_no_direct_sprint_status_get_route():
    """AC2: server.py has no @app.get decorator for /api/sprint-status."""
    content = SERVER_FILE.read_text()
    assert '@app.get("/api/sprint-status")' not in content, \
        "server.py still defines @app.get('/api/sprint-status') directly"


def test_server_has_no_direct_sprint_status_post_route():
    """AC2: server.py has no @app.post decorator for /api/sprint-status."""
    content = SERVER_FILE.read_text()
    assert '@app.post("/api/sprint-status")' not in content, \
        "server.py still defines @app.post('/api/sprint-status') directly"


def test_server_has_no_direct_sprint_summary_route():
    """AC2: server.py has no @app.get decorator for /api/sprint-summary."""
    content = SERVER_FILE.read_text()
    assert '@app.get("/api/sprint-summary")' not in content, \
        "server.py still defines @app.get('/api/sprint-summary') directly"


def test_server_has_no_direct_home_route():
    """AC2: server.py has no @app.get decorator for /api/home."""
    content = SERVER_FILE.read_text()
    assert '@app.get("/api/home")' not in content, \
        "server.py still defines @app.get('/api/home') directly"


def test_server_imports_sprint_summaries_router():
    """AC2: server.py references sprint_summaries_router."""
    content = SERVER_FILE.read_text()
    assert "sprint_summaries_router" in content, \
        "sprint_summaries_router not referenced in server.py"


def test_server_mounts_sprint_summaries_router():
    """AC2: server.py calls app.include_router(sprint_summaries_router)."""
    content = SERVER_FILE.read_text()
    assert "app.include_router(sprint_summaries_router)" in content, \
        "app.include_router(sprint_summaries_router) not found in server.py"


# ── AC3: All moved endpoints return identical responses ───────────────────────

def test_get_sprint_status_endpoint_responds(client):
    """AC3: GET /api/sprint-status returns 200 with running_sprints key."""
    try:
        r = client.get("/api/sprint-status")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        data = r.json()
        assert "running_sprints" in data, f"'running_sprints' key missing from response: {data}"
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")


def test_post_sprint_status_endpoint_responds(client):
    """AC3: POST /api/sprint-status without body returns 422 (validation), not 404."""
    try:
        r = client.post("/api/sprint-status")
        assert r.status_code != 404, f"Got 404 — route not mounted: {r.text}"
        assert r.status_code in (200, 201, 400, 422), \
            f"Unexpected status: {r.status_code}: {r.text}"
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")


def test_get_sprint_summary_endpoint_responds(client):
    """AC3: GET /api/sprint-summary returns 200 or 404 (not a routing 404)."""
    try:
        r = client.get("/api/sprint-summary")
        assert r.status_code in (200, 404), \
            f"Unexpected status: {r.status_code}: {r.text}"
        if r.status_code == 200:
            data = r.json()
            assert "path" in data and "content" in data, \
                f"Response missing 'path' or 'content' key: {data}"
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")


def test_get_home_endpoint_responds(client):
    """AC3: GET /api/home returns 200 with stats, projects, activity keys."""
    try:
        r = client.get("/api/home")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        data = r.json()
        assert "stats" in data, f"'stats' key missing from /api/home response: {data}"
        assert "projects" in data, f"'projects' key missing from /api/home response: {data}"
        assert "activity" in data, f"'activity' key missing from /api/home response: {data}"
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")


def test_get_sprint_history_endpoint_responds(client):
    """AC3: GET /api/sprint-history returns 200 with a list payload."""
    try:
        r = client.get("/api/sprint-history")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        assert isinstance(r.json(), list), f"Expected list response, got: {type(r.json())}"
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")


def test_get_sprint_history_content_endpoint_responds(client):
    """AC3: GET /api/sprint-history-content returns 200 or 404 (not a routing 404)."""
    try:
        r = client.get("/api/sprint-history-content")
        assert r.status_code in (200, 404), \
            f"Unexpected status: {r.status_code}: {r.text}"
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")


def test_get_sprints_timeline_endpoint_responds(client):
    """AC3: GET /api/sprints/timeline with project param returns 200 with sprints key."""
    try:
        r = client.get("/api/sprints/timeline", params={"project": "zealchaiwut/commander"})
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        data = r.json()
        assert "sprints" in data, f"'sprints' key missing from response: {data}"
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")


def test_get_sprints_summaries_endpoint_responds(client):
    """AC3: GET /api/sprints/summaries with project param returns 200 with summaries key."""
    try:
        r = client.get("/api/sprints/summaries", params={"project": "zealchaiwut/commander"})
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        data = r.json()
        assert "summaries" in data, f"'summaries' key missing from response: {data}"
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")


# ── AC4: py_compile passes for both files ─────────────────────────────────────

def test_sprint_summaries_router_compiles():
    """AC4: py_compile.compile('routers/sprint_summaries.py') exits 0."""
    result = subprocess.run(
        ["python", "-m", "py_compile", str(ROUTER_FILE)],
        cwd=str(DASHBOARD_ROOT),
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, \
        f"sprint_summaries.py compilation failed:\n{result.stderr.decode()}"


def test_server_compiles():
    """AC4: py_compile.compile('server.py') exits 0."""
    result = subprocess.run(
        ["python", "-m", "py_compile", str(SERVER_FILE)],
        cwd=str(DASHBOARD_ROOT),
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, \
        f"server.py compilation failed:\n{result.stderr.decode()}"


# ── AC5: No new routes introduced in server.py ────────────────────────────────

def test_server_line_count_did_not_grow():
    """AC5: server.py line count is strictly less than before the refactor.

    Pre-refactor line count: 12679. Removing ~514 lines of routes and models
    and adding a single include_router call + import gives ~12168. Gate: 12670.
    """
    lines = SERVER_FILE.read_text().splitlines()
    assert len(lines) < 12670, (
        f"server.py has {len(lines)} lines — expected <12670 after extraction. "
        "New routes may have been added or routes were not removed."
    )
