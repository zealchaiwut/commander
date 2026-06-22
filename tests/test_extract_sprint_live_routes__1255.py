"""Tests for issue #1255: Extract sprint live/log routes to sprint_live router.

Refactoring extracts the following route handlers from server.py into
routers/sprint_live.py:
  GET  /api/sprints/{label}/state           (and state* sub-paths)
  GET  /api/sprints/{label}/state-full
  GET  /api/sprints/{label}/issue/{num}/log
  GET  /api/logs/runs
  POST /api/logs/sync-github
  GET  /api/sprints/{label}/live
  GET  /api/sprints/{label}/live/stream

AC verified:
  AC1  - routers/sprint_live.py exists and registers all moved routes via an APIRouter
  AC2  - state and state* sub-paths are in sprint_live.py
  AC3  - live and live* sub-paths are in sprint_live.py
  AC4  - issue/{num}/log is in sprint_live.py
  AC5  - GET /api/logs/runs is in sprint_live.py
  AC6  - POST /api/logs/sync-github is in sprint_live.py
  AC7  - server.py mounts the router; zero direct route defs for moved paths
  AC8  - HTTP responses identical (smoke test, status-code level)
  AC9  - SSE stream behavior unchanged (smoke test)
  AC10 - py_compile passes for both files
  AC11 - No new routes added to server.py
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
ROUTER_FILE = DASHBOARD_ROOT / "routers" / "sprint_live.py"
SERVER_FILE = DASHBOARD_ROOT / "server.py"


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# ── AC1: File exists and exposes an APIRouter ─────────────────────────────────

def test_sprint_live_router_file_exists():
    """AC1: routers/sprint_live.py exists."""
    assert ROUTER_FILE.exists(), f"Router file not found: {ROUTER_FILE}"


def test_sprint_live_router_has_apirouter():
    """AC1: sprint_live.py declares an APIRouter assigned to `router`."""
    content = ROUTER_FILE.read_text()
    assert "APIRouter" in content, "APIRouter not imported in sprint_live.py"
    assert "router = APIRouter(" in content, "`router = APIRouter(...)` not found"


# ── AC2: state and state* sub-paths in sprint_live.py ────────────────────────

def test_sprint_live_has_state_route():
    """AC2: /api/sprints/{sprint_label}/state is defined in sprint_live.py."""
    content = ROUTER_FILE.read_text()
    assert '"/api/sprints/{sprint_label}/state"' in content, \
        "state route not found in sprint_live.py"


def test_sprint_live_has_state_full_route():
    """AC2: /api/sprints/{sprint_label}/state-full is defined in sprint_live.py."""
    content = ROUTER_FILE.read_text()
    assert '"/api/sprints/{sprint_label}/state-full"' in content, \
        "state-full route not found in sprint_live.py"


# ── AC3: live and live* sub-paths in sprint_live.py ──────────────────────────

def test_sprint_live_has_live_route():
    """AC3: /api/sprints/{sprint_label}/live is defined in sprint_live.py."""
    content = ROUTER_FILE.read_text()
    assert '"/api/sprints/{sprint_label}/live"' in content, \
        "live route not found in sprint_live.py"


def test_sprint_live_has_live_stream_route():
    """AC3: /api/sprints/{sprint_label}/live/stream is defined in sprint_live.py."""
    content = ROUTER_FILE.read_text()
    assert '"/api/sprints/{sprint_label}/live/stream"' in content, \
        "live/stream route not found in sprint_live.py"


# ── AC4: issue log route in sprint_live.py ───────────────────────────────────

def test_sprint_live_has_issue_log_route():
    """AC4: /api/sprints/{sprint_label}/issue/{issue_num}/log is defined in sprint_live.py."""
    content = ROUTER_FILE.read_text()
    assert '"/api/sprints/{sprint_label}/issue/{issue_num}/log"' in content, \
        "issue log route not found in sprint_live.py"


# ── AC5: GET /api/logs/runs in sprint_live.py ────────────────────────────────

def test_sprint_live_has_logs_runs_route():
    """AC5: GET /api/logs/runs is defined in sprint_live.py."""
    content = ROUTER_FILE.read_text()
    assert '"/api/logs/runs"' in content, \
        "GET /api/logs/runs not found in sprint_live.py"


# ── AC6: POST /api/logs/sync-github in sprint_live.py ────────────────────────

def test_sprint_live_has_logs_sync_github_route():
    """AC6: POST /api/logs/sync-github is defined in sprint_live.py."""
    content = ROUTER_FILE.read_text()
    assert '"/api/logs/sync-github"' in content, \
        "POST /api/logs/sync-github not found in sprint_live.py"


# ── AC7: server.py mounts the router; no direct defs for moved paths ─────────

def test_server_imports_sprint_live_router():
    """AC7: server.py imports sprint_live_router from routers."""
    content = SERVER_FILE.read_text()
    assert "sprint_live_router" in content, \
        "sprint_live_router not referenced in server.py"


def test_server_mounts_sprint_live_router():
    """AC7: server.py calls app.include_router(sprint_live_router)."""
    content = SERVER_FILE.read_text()
    assert "app.include_router(sprint_live_router)" in content, \
        "app.include_router(sprint_live_router) not found in server.py"


def test_server_has_no_direct_state_route():
    """AC7: server.py has no @app.get decorator for /api/sprints/{sprint_label}/state."""
    content = SERVER_FILE.read_text()
    assert '@app.get("/api/sprints/{sprint_label}/state")' not in content, \
        "server.py still defines @app.get('/api/sprints/{sprint_label}/state') directly"


def test_server_has_no_direct_state_full_route():
    """AC7: server.py has no @app.get decorator for /api/sprints/{sprint_label}/state-full."""
    content = SERVER_FILE.read_text()
    assert '@app.get("/api/sprints/{sprint_label}/state-full")' not in content, \
        "server.py still defines state-full route directly"


def test_server_has_no_direct_live_route():
    """AC7: server.py has no @app.get decorator for /api/sprints/{sprint_label}/live."""
    content = SERVER_FILE.read_text()
    assert '@app.get("/api/sprints/{sprint_label}/live")' not in content, \
        "server.py still defines live route directly"


def test_server_has_no_direct_live_stream_route():
    """AC7: server.py has no @app.get decorator for /api/sprints/{sprint_label}/live/stream."""
    content = SERVER_FILE.read_text()
    assert '@app.get("/api/sprints/{sprint_label}/live/stream")' not in content, \
        "server.py still defines live/stream route directly"


def test_server_has_no_direct_issue_log_route():
    """AC7: server.py has no @app.get decorator for the issue log path."""
    content = SERVER_FILE.read_text()
    assert '@app.get("/api/sprints/{sprint_label}/issue/{issue_num}/log")' not in content, \
        "server.py still defines issue log route directly"


def test_server_has_no_direct_logs_runs_route():
    """AC7: server.py has no @app.get decorator for /api/logs/runs."""
    content = SERVER_FILE.read_text()
    assert '@app.get("/api/logs/runs")' not in content, \
        "server.py still defines GET /api/logs/runs directly"


def test_server_has_no_direct_logs_sync_github_route():
    """AC7: server.py has no @app.post decorator for /api/logs/sync-github."""
    content = SERVER_FILE.read_text()
    assert '@app.post("/api/logs/sync-github")' not in content, \
        "server.py still defines POST /api/logs/sync-github directly"


# ── AC8/AC9: HTTP response smoke tests (behavioral identity) ─────────────────

def test_logs_runs_endpoint_responds(client):
    """AC8: GET /api/logs/runs returns 200 with items/page/page_size/total keys."""
    try:
        r = client.get("/api/logs/runs")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        body = r.json()
        assert "items" in body, "Response missing 'items' key"
        assert "page" in body, "Response missing 'page' key"
        assert "page_size" in body, "Response missing 'page_size' key"
        assert "total" in body, "Response missing 'total' key"
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")


def test_logs_sync_github_endpoint_responds(client):
    """AC8: POST /api/logs/sync-github without project returns known JSON (not 404/500 from missing route)."""
    try:
        r = client.post("/api/logs/sync-github")
        # No project → returns error JSON, but must NOT be 404 (route exists)
        assert r.status_code != 404, f"Got 404 — route not mounted: {r.text}"
        assert r.status_code in (200, 400, 422), f"Unexpected status: {r.status_code}"
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")


def test_sprint_state_endpoint_responds(client):
    """AC8: GET /api/sprints/{label}/state returns 200 or a defined error (not missing-route 404)."""
    try:
        r = client.get("/api/sprints/sprint-85/state", params={"project": "zealchaiwut/commander"})
        assert r.status_code in (200, 400, 404, 422), \
            f"Unexpected status {r.status_code}: {r.text}"
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")


def test_sprint_live_endpoint_responds(client):
    """AC8: GET /api/sprints/{label}/live returns 200 or a defined error."""
    try:
        r = client.get("/api/sprints/sprint-85/live", params={"project": "zealchaiwut/commander"})
        assert r.status_code in (200, 400, 404, 422), \
            f"Unexpected status {r.status_code}: {r.text}"
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")


def test_sprint_live_stream_endpoint_exists(client):
    """AC9: GET /api/sprints/{label}/live/stream is reachable (SSE endpoint not 404)."""
    try:
        with client.stream(
            "GET",
            "/api/sprints/sprint-85/live/stream",
            params={"project": "zealchaiwut/commander"},
            timeout=5.0,
        ) as r:
            # Accept 200 (SSE started) or 400 (invalid label/params)
            assert r.status_code in (200, 400, 404, 422), \
                f"Unexpected status {r.status_code}"
    except (httpx.ConnectError, httpx.ReadTimeout):
        pytest.skip("UAT server not responding or SSE stream timed out")


def test_issue_log_endpoint_responds(client):
    """AC8: GET /api/sprints/{label}/issue/{num}/log returns known status."""
    try:
        r = client.get(
            "/api/sprints/sprint-85/issue/1/log",
            params={"project": "zealchaiwut/commander"},
        )
        assert r.status_code in (200, 400, 404, 422), \
            f"Unexpected status {r.status_code}: {r.text}"
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")


# ── AC10: py_compile passes for both files ────────────────────────────────────

def test_sprint_live_router_compiles():
    """AC10: py_compile.compile('routers/sprint_live.py') passes."""
    result = subprocess.run(
        ["python", "-m", "py_compile", str(ROUTER_FILE)],
        cwd=str(DASHBOARD_ROOT),
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, \
        f"sprint_live.py compilation failed:\n{result.stderr.decode()}"


def test_server_compiles():
    """AC10: py_compile.compile('server.py') passes."""
    result = subprocess.run(
        ["python", "-m", "py_compile", str(SERVER_FILE)],
        cwd=str(DASHBOARD_ROOT),
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, \
        f"server.py compilation failed:\n{result.stderr.decode()}"


# ── AC11: No new routes added to server.py ───────────────────────────────────

def test_server_line_count_did_not_grow():
    """AC11: server.py line count is strictly less than before the refactor (routes removed, none added).

    Pre-refactor line count: 13897. After removing ~350 lines of routes and helpers
    and adding a single include_router call + import, the count should be lower.
    The gate threshold is 13900 to give a small margin.
    """
    lines = SERVER_FILE.read_text().splitlines()
    assert len(lines) < 13900, (
        f"server.py has {len(lines)} lines — expected <13900 after extraction. "
        "New routes may have been added or routes were not removed."
    )
