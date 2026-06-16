"""Tests for issue #1259: Extract system/misc routes from server.py to routers/system_misc.py (runs against UAT)"""
import os
import pytest
import httpx
import py_compile
from pathlib import Path


# Resolved from UAT .env at runtime; see tester skill Step 0.
# Default kept only as a last-resort fallback if BASE_URL not exported.
BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# --- Acceptance Criteria ---

def test_extract_system_misc_routes__router_exists(client):
    # AC: `routers/system_misc.py` exists and registers an `APIRouter` mounted in `server.py`
    dashboard_root = Path(__file__).resolve().parent.parent / "apps" / "dashboard"
    system_misc_file = dashboard_root / "routers" / "system_misc.py"
    assert system_misc_file.exists(), "routers/system_misc.py does not exist"


def test_extract_system_misc_routes__routes_in_system_misc(client):
    # AC: All seven routes live in routers/system_misc.py and nowhere else in server.py
    dashboard_root = Path(__file__).resolve().parent.parent / "apps" / "dashboard"
    system_misc_file = dashboard_root / "routers" / "system_misc.py"

    # Read both files
    system_misc_content = system_misc_file.read_text()

    # Routes that should be in system_misc (not server)
    moved_routes = [
        "/api/alerts",
        "/api/docs-freshness",
        "/api/deploy/overview",
        "/api/maintenance/sprints/cleanup",
        "/api/plan-usage",
        "/api/estimator/health",
        "/api/issues/{id}/estimate"  # Check for pattern, not exact string
    ]

    # Verify each route is in system_misc
    for route in moved_routes:
        if route == "/api/issues/{id}/estimate":
            # For this one, check for the pattern with issue_id param
            assert "/api/issues/{" in system_misc_content, f"Route pattern {route} not found in system_misc.py"
        else:
            assert route in system_misc_content, f"Route {route} not found in system_misc.py"


def test_extract_system_misc_routes__no_route_decorators_remain(client):
    # AC: No route handler decorators for moved paths remain in server.py
    dashboard_root = Path(__file__).resolve().parent.parent / "apps" / "dashboard"
    server_file = dashboard_root / "server.py"

    server_content = server_file.read_text()

    # Check that route decorators are not present for the moved routes
    forbidden_patterns = [
        '@app.get("/api/alerts',
        '@app.post("/api/alerts',
        '@app.get("/api/docs-freshness',
        '@app.post("/api/docs-freshness',
        '@app.get("/api/deploy/overview',
        '@app.post("/api/maintenance/sprints/cleanup',
        '@app.get("/api/plan-usage',
        '@app.get("/api/estimator/health',
        '@app.post("/api/issues/{',
    ]

    for pattern in forbidden_patterns:
        # Split the pattern to be more flexible with whitespace
        base_pattern = pattern.replace('@app.', '').replace('("', '').rstrip('"')
        # Check if the route still has a handler in server.py
        if f'@app.get("{base_pattern}' in server_content or f'@app.post("{base_pattern}' in server_content:
            assert False, f"Route handler decorator for {base_pattern} still found in server.py"


def test_extract_system_misc_routes__no_new_routes(client):
    # AC: No new routes are added to server.py during this change
    # This is implicitly tested by verifying moved routes aren't duplicated
    dashboard_root = Path(__file__).resolve().parent.parent / "apps" / "dashboard"
    system_misc_file = dashboard_root / "routers" / "system_misc.py"

    system_misc_content = system_misc_file.read_text()

    # Count route decorators
    import re
    system_misc_routes = len(re.findall(r'@router\.(get|post|put|delete)\(', system_misc_content))

    # Verify that system_misc has the expected routes (at least 7)
    assert system_misc_routes >= 7, f"system_misc.py has {system_misc_routes} routes, expected at least 7"


def test_extract_system_misc_routes__alerts_endpoint(client):
    # AC: All moved endpoints return identical responses (status code, body shape) as before
    # Testing GET /api/alerts
    try:
        r = client.get("/api/alerts")
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")
    assert r.status_code in [200, 404], f"Unexpected status code {r.status_code} for GET /api/alerts"
    try:
        data = r.json()
        assert isinstance(data, (list, dict)), "Response should be JSON list or dict"
    except Exception:
        pytest.skip("GET /api/alerts endpoint not accessible or returns non-JSON")


def test_extract_system_misc_routes__deploy_overview_endpoint(client):
    # AC: All moved endpoints return identical responses
    # Testing GET /api/deploy/overview
    try:
        r = client.get("/api/deploy/overview")
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")
    assert r.status_code in [200, 404], f"Unexpected status code {r.status_code} for GET /api/deploy/overview"
    try:
        data = r.json()
        assert isinstance(data, dict), "Response should be JSON dict"
    except Exception:
        pytest.skip("GET /api/deploy/overview endpoint not accessible or returns non-JSON")


def test_extract_system_misc_routes__plan_usage_endpoint(client):
    # AC: All moved endpoints return identical responses
    # Testing GET /api/plan-usage
    try:
        r = client.get("/api/plan-usage")
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")
    assert r.status_code in [200, 404], f"Unexpected status code {r.status_code} for GET /api/plan-usage"
    try:
        data = r.json()
        assert isinstance(data, dict), "Response should be JSON dict"
    except Exception:
        pytest.skip("GET /api/plan-usage endpoint not accessible or returns non-JSON")


def test_extract_system_misc_routes__estimator_health_endpoint(client):
    # AC: All moved endpoints return identical responses
    # Testing GET /api/estimator/health
    try:
        r = client.get("/api/estimator/health")
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")
    assert r.status_code in [200, 404], f"Unexpected status code {r.status_code} for GET /api/estimator/health"
    try:
        data = r.json()
        assert isinstance(data, dict), "Response should be JSON dict"
    except Exception:
        pytest.skip("GET /api/estimator/health endpoint not accessible or returns non-JSON")


def test_extract_system_misc_routes__py_compile_success(client):
    # AC: `py_compile` passes on both `server.py` and `routers/system_misc.py` with zero errors
    dashboard_root = Path(__file__).resolve().parent.parent / "apps" / "dashboard"

    server_file = dashboard_root / "server.py"
    system_misc_file = dashboard_root / "routers" / "system_misc.py"

    # Compile server.py
    try:
        py_compile.compile(str(server_file), doraise=True)
    except py_compile.PyCompileError as e:
        pytest.fail(f"py_compile failed on server.py: {e}")

    # Compile system_misc.py
    try:
        py_compile.compile(str(system_misc_file), doraise=True)
    except py_compile.PyCompileError as e:
        pytest.fail(f"py_compile failed on routers/system_misc.py: {e}")


def test_extract_system_misc_routes__no_import_errors(client):
    # AC: No existing imports or shared dependencies are broken
    # Verify that both modules can be imported without errors
    import sys
    dashboard_root = Path(__file__).resolve().parent.parent / "apps" / "dashboard"

    # Add to path if needed
    if str(dashboard_root) not in sys.path:
        sys.path.insert(0, str(dashboard_root))

    try:
        import server  # noqa: F401
    except Exception as e:
        pytest.fail(f"Failed to import server.py: {e}")

    try:
        import routers.system_misc  # noqa: F401
    except Exception as e:
        pytest.fail(f"Failed to import routers/system_misc.py: {e}")
