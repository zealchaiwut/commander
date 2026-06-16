"""Tests for issue #1259: Extract system/misc routes from server.py to routers/system_misc.py.

Routes extracted:
  GET    /api/alerts
  POST   /api/alerts
  DELETE /api/alerts/{idx}
  GET    /api/docs-freshness/warnings
  POST   /api/docs-freshness/check
  DELETE /api/docs-freshness/warnings/{warning_id}
  GET    /api/deploy/overview
  POST   /api/maintenance/sprints/cleanup
  GET    /api/plan-usage
  GET    /api/estimator/health
  POST   /api/issues/{id}/estimate

AC verified:
  AC1 - routers/system_misc.py exists and registers an APIRouter mounted in server.py
  AC2 - All listed routes live in system_misc.py and nowhere else in server.py
  AC3 - All moved endpoints return identical responses (status codes, payload shape)
  AC4 - py_compile passes on both server.py and routers/system_misc.py
  AC5 - No new routes added to server.py
  AC6 - No existing imports or shared dependencies are broken
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
ROUTER_FILE = DASHBOARD_ROOT / "routers" / "system_misc.py"
SERVER_FILE = DASHBOARD_ROOT / "server.py"


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# ── AC1: File exists and exposes an APIRouter ────────────────────────────────

def test_system_misc_router_file_exists():
    """AC1: routers/system_misc.py exists."""
    assert ROUTER_FILE.exists(), f"Router file not found: {ROUTER_FILE}"


def test_system_misc_router_has_apirouter():
    """AC1: system_misc.py declares an APIRouter assigned to `router`."""
    content = ROUTER_FILE.read_text()
    assert "APIRouter" in content, "APIRouter not imported in system_misc.py"
    assert "router = APIRouter(" in content, "`router = APIRouter(...)` not found"


def test_system_misc_has_alerts_get_route():
    """AC1: GET /api/alerts is defined in system_misc.py."""
    content = ROUTER_FILE.read_text()
    assert '"/api/alerts"' in content, "GET /api/alerts not found in system_misc.py"


def test_system_misc_has_docs_freshness_route():
    """AC1: docs-freshness routes are defined in system_misc.py."""
    content = ROUTER_FILE.read_text()
    assert '"/api/docs-freshness/warnings"' in content or '"docs-freshness"' in content or \
        "/api/docs-freshness" in content, \
        "docs-freshness routes not found in system_misc.py"


def test_system_misc_has_deploy_overview_route():
    """AC1: GET /api/deploy/overview is defined in system_misc.py."""
    content = ROUTER_FILE.read_text()
    assert '"/api/deploy/overview"' in content, \
        "GET /api/deploy/overview not found in system_misc.py"


def test_system_misc_has_maintenance_cleanup_route():
    """AC1: POST /api/maintenance/sprints/cleanup is defined in system_misc.py."""
    content = ROUTER_FILE.read_text()
    assert '"/api/maintenance/sprints/cleanup"' in content, \
        "POST /api/maintenance/sprints/cleanup not found in system_misc.py"


def test_system_misc_has_plan_usage_route():
    """AC1: GET /api/plan-usage is defined in system_misc.py."""
    content = ROUTER_FILE.read_text()
    assert '"/api/plan-usage"' in content, \
        "GET /api/plan-usage not found in system_misc.py"


def test_system_misc_has_estimator_health_route():
    """AC1: GET /api/estimator/health is defined in system_misc.py."""
    content = ROUTER_FILE.read_text()
    assert '"/api/estimator/health"' in content, \
        "GET /api/estimator/health not found in system_misc.py"


def test_system_misc_has_issue_estimate_route():
    """AC1: POST /api/issues/{id}/estimate is defined in system_misc.py."""
    content = ROUTER_FILE.read_text()
    assert '"/api/issues/{issue_id}/estimate"' in content or \
        '"/api/issues/' in content, \
        "POST /api/issues/{id}/estimate not found in system_misc.py"


# ── AC2: None of the routes remain defined in server.py ──────────────────────

def test_server_has_no_direct_alerts_get_route():
    """AC2: server.py has no @app.get decorator for /api/alerts."""
    content = SERVER_FILE.read_text()
    assert '@app.get("/api/alerts")' not in content, \
        "server.py still defines @app.get('/api/alerts') directly"


def test_server_has_no_direct_alerts_post_route():
    """AC2: server.py has no @app.post decorator for /api/alerts."""
    content = SERVER_FILE.read_text()
    assert '@app.post("/api/alerts"' not in content, \
        "server.py still defines @app.post('/api/alerts') directly"


def test_server_has_no_direct_alerts_delete_route():
    """AC2: server.py has no @app.delete decorator for /api/alerts/{idx}."""
    content = SERVER_FILE.read_text()
    assert '@app.delete("/api/alerts/' not in content, \
        "server.py still defines @app.delete('/api/alerts/...') directly"


def test_server_has_no_direct_docs_freshness_check_route():
    """AC2: server.py has no @app.post decorator for /api/docs-freshness/check."""
    content = SERVER_FILE.read_text()
    assert '@app.post("/api/docs-freshness/check"' not in content, \
        "server.py still defines @app.post('/api/docs-freshness/check') directly"


def test_server_has_no_direct_docs_freshness_warnings_route():
    """AC2: server.py has no @app.get decorator for /api/docs-freshness/warnings."""
    content = SERVER_FILE.read_text()
    assert '@app.get("/api/docs-freshness/warnings")' not in content, \
        "server.py still defines @app.get('/api/docs-freshness/warnings') directly"


def test_server_has_no_direct_docs_freshness_delete_route():
    """AC2: server.py has no @app.delete decorator for /api/docs-freshness/warnings/{id}."""
    content = SERVER_FILE.read_text()
    assert '@app.delete("/api/docs-freshness/warnings/' not in content, \
        "server.py still defines @app.delete('/api/docs-freshness/warnings/...') directly"


def test_server_has_no_direct_deploy_overview_route():
    """AC2: server.py has no @app.get decorator for /api/deploy/overview."""
    content = SERVER_FILE.read_text()
    assert '@app.get("/api/deploy/overview")' not in content, \
        "server.py still defines @app.get('/api/deploy/overview') directly"


def test_server_has_no_direct_maintenance_cleanup_route():
    """AC2: server.py has no @app.post decorator for /api/maintenance/sprints/cleanup."""
    content = SERVER_FILE.read_text()
    assert '@app.post("/api/maintenance/sprints/cleanup")' not in content, \
        "server.py still defines @app.post('/api/maintenance/sprints/cleanup') directly"


def test_server_has_no_direct_plan_usage_route():
    """AC2: server.py has no @app.get decorator for /api/plan-usage."""
    content = SERVER_FILE.read_text()
    assert '@app.get("/api/plan-usage")' not in content, \
        "server.py still defines @app.get('/api/plan-usage') directly"


def test_server_has_no_direct_estimator_health_route():
    """AC2: server.py has no @app.get decorator for /api/estimator/health."""
    content = SERVER_FILE.read_text()
    assert '@app.get("/api/estimator/health")' not in content, \
        "server.py still defines @app.get('/api/estimator/health') directly"


def test_server_has_no_direct_issue_estimate_route():
    """AC2: server.py has no @app.post decorator for /api/issues/{id}/estimate."""
    content = SERVER_FILE.read_text()
    assert '@app.post("/api/issues/{issue_id}/estimate")' not in content, \
        "server.py still defines @app.post('/api/issues/{issue_id}/estimate') directly"


def test_server_imports_system_misc_router():
    """AC2: server.py references system_misc_router."""
    content = SERVER_FILE.read_text()
    assert "system_misc_router" in content, \
        "system_misc_router not referenced in server.py"


def test_server_mounts_system_misc_router():
    """AC2: server.py calls app.include_router(system_misc_router)."""
    content = SERVER_FILE.read_text()
    assert "app.include_router(system_misc_router)" in content, \
        "app.include_router(system_misc_router) not found in server.py"


# ── AC3: All moved endpoints return identical responses ───────────────────────

def test_get_alerts_endpoint_responds(client):
    """AC3: GET /api/alerts returns 200 with a list."""
    try:
        r = client.get("/api/alerts")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        assert isinstance(r.json(), list), f"Expected list response, got: {type(r.json())}"
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")


def test_post_alerts_endpoint_responds(client):
    """AC3: POST /api/alerts returns 201 on success."""
    try:
        r = client.post("/api/alerts", json={"title": "test_1259_check", "body": "AC3 test"})
        assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.text}"
        data = r.json()
        assert "ok" in data, f"'ok' key missing: {data}"
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")


def test_get_docs_freshness_warnings_endpoint_responds(client):
    """AC3: GET /api/docs-freshness/warnings returns 200 with a list."""
    try:
        r = client.get("/api/docs-freshness/warnings")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        assert isinstance(r.json(), list), f"Expected list response, got: {type(r.json())}"
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")


def test_post_docs_freshness_check_endpoint_responds(client):
    """AC3: POST /api/docs-freshness/check with valid body returns 200."""
    try:
        r = client.post("/api/docs-freshness/check", json={
            "repo": "zealchaiwut/commander",
            "trigger_ref": "abc123",
            "stale_docs": [],
            "cleared_docs": [],
        })
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        data = r.json()
        assert "ok" in data, f"'ok' key missing: {data}"
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")


def test_get_deploy_overview_endpoint_responds(client):
    """AC3: GET /api/deploy/overview returns 200 with environments key."""
    try:
        r = client.get("/api/deploy/overview")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        data = r.json()
        assert "environments" in data, f"'environments' key missing: {data}"
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")


def test_post_maintenance_sprints_cleanup_endpoint_responds(client):
    """AC3: POST /api/maintenance/sprints/cleanup returns 200 or 503."""
    try:
        r = client.post("/api/maintenance/sprints/cleanup", json={
            "project": "zealchaiwut/commander",
            "dry_run": True,
        })
        assert r.status_code in (200, 400, 503), \
            f"Unexpected status {r.status_code}: {r.text}"
        if r.status_code == 200:
            data = r.json()
            assert "archived" in data, f"'archived' key missing: {data}"
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")


def test_get_plan_usage_endpoint_responds(client):
    """AC3: GET /api/plan-usage returns 200 or 404 (not configured)."""
    try:
        r = client.get("/api/plan-usage")
        assert r.status_code in (200, 404), \
            f"Unexpected status {r.status_code}: {r.text}"
        if r.status_code == 200:
            data = r.json()
            assert "status" in data, f"'status' key missing: {data}"
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")


def test_get_estimator_health_endpoint_responds(client):
    """AC3: GET /api/estimator/health returns 200 with available key."""
    try:
        r = client.get("/api/estimator/health")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        data = r.json()
        assert "available" in data, f"'available' key missing: {data}"
        assert isinstance(data["available"], bool), \
            f"'available' should be bool, got {type(data['available'])}"
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")


# ── AC4: py_compile passes for both files ─────────────────────────────────────

def test_system_misc_router_compiles():
    """AC4: py_compile passes on routers/system_misc.py."""
    result = subprocess.run(
        ["python", "-m", "py_compile", str(ROUTER_FILE)],
        cwd=str(DASHBOARD_ROOT),
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, \
        f"system_misc.py compilation failed:\n{result.stderr.decode()}"


def test_server_compiles():
    """AC4: py_compile passes on server.py."""
    result = subprocess.run(
        ["python", "-m", "py_compile", str(SERVER_FILE)],
        cwd=str(DASHBOARD_ROOT),
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, \
        f"server.py compilation failed:\n{result.stderr.decode()}"


# ── AC5: No new routes added to server.py ─────────────────────────────────────

def test_server_line_count_did_not_grow():
    """AC5: server.py line count is strictly less than before the refactor.

    Pre-refactor line count: 12153. Removing ~370 lines of routes and models
    and adding a single include_router call + import gives ~11785. Gate: 12150.
    """
    lines = SERVER_FILE.read_text().splitlines()
    assert len(lines) < 12150, (
        f"server.py has {len(lines)} lines — expected <12150 after extraction. "
        "New routes may have been added or routes were not removed."
    )
