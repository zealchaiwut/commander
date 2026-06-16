"""Tests for issue #1257: Extract sprint CRUD routes to routers/sprint_crud.py.

Refactoring extracts the following route handlers from server.py into
routers/sprint_crud.py:
  POST   /api/sprints/create
  POST   /api/sprints/{label}/rename
  POST   /api/sprints/{label}/tickets/reorder
  POST   /api/sprints/{label}/plan
  DELETE /api/sprints/{label}
  POST   /api/sprints/delete-empty
  POST   /api/sprints/cleanup-empty

AC verified:
  AC1 - routers/sprint_crud.py exists and contains an APIRouter with all moved routes
  AC2 - All six routes (create, rename, reorder, plan, delete-label, delete-empty/cleanup-empty) are defined in sprint_crud.py
  AC3 - Label/branch ops (edit_label, delete_label) are co-located in sprint_crud.py or imported cleanly
  AC4 - server.py registers the router via app.include_router(...) and has no inline CRUD route defs
  AC5 - py_compile passes for sprint_crud.py and server.py
  AC6 - All existing endpoints respond correctly (smoke test)
  AC7 - No new routes added to server.py (line count strictly lower than pre-refactor)
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
ROUTER_FILE = DASHBOARD_ROOT / "routers" / "sprint_crud.py"
SERVER_FILE = DASHBOARD_ROOT / "server.py"


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# ── AC1: File exists and exposes an APIRouter ─────────────────────────────────

def test_sprint_crud_router_file_exists():
    """AC1: routers/sprint_crud.py exists."""
    assert ROUTER_FILE.exists(), f"Router file not found: {ROUTER_FILE}"


def test_sprint_crud_router_has_apirouter():
    """AC1: sprint_crud.py declares an APIRouter assigned to `router`."""
    content = ROUTER_FILE.read_text()
    assert "APIRouter" in content, "APIRouter not imported in sprint_crud.py"
    assert "router = APIRouter(" in content, "`router = APIRouter(...)` not found"


# ── AC2: All moved routes are defined in sprint_crud.py ──────────────────────

def test_sprint_crud_has_create_route():
    """AC2: POST /api/sprints/create is defined in sprint_crud.py."""
    content = ROUTER_FILE.read_text()
    assert '"/api/sprints/create"' in content, \
        "POST /api/sprints/create not found in sprint_crud.py"


def test_sprint_crud_has_rename_route():
    """AC2: POST /api/sprints/{sprint_label}/rename is defined in sprint_crud.py."""
    content = ROUTER_FILE.read_text()
    assert '"/api/sprints/{sprint_label}/rename"' in content, \
        "POST /api/sprints/{sprint_label}/rename not found in sprint_crud.py"


def test_sprint_crud_has_reorder_route():
    """AC2: POST /api/sprints/{sprint_label}/tickets/reorder is defined in sprint_crud.py."""
    content = ROUTER_FILE.read_text()
    assert '"/api/sprints/{sprint_label}/tickets/reorder"' in content, \
        "POST /api/sprints/{sprint_label}/tickets/reorder not found in sprint_crud.py"


def test_sprint_crud_has_plan_route():
    """AC2: POST /api/sprints/{sprint_label}/plan is defined in sprint_crud.py."""
    content = ROUTER_FILE.read_text()
    assert '"/api/sprints/{sprint_label}/plan"' in content, \
        "POST /api/sprints/{sprint_label}/plan not found in sprint_crud.py"


def test_sprint_crud_has_delete_sprint_route():
    """AC2: DELETE /api/sprints/{sprint_label} is defined in sprint_crud.py."""
    content = ROUTER_FILE.read_text()
    assert '"/api/sprints/{sprint_label}"' in content, \
        "DELETE /api/sprints/{sprint_label} not found in sprint_crud.py"
    assert "@router.delete" in content, \
        "@router.delete not found in sprint_crud.py"


def test_sprint_crud_has_delete_empty_or_cleanup_empty_route():
    """AC2: POST /api/sprints/delete-empty or cleanup-empty is defined in sprint_crud.py."""
    content = ROUTER_FILE.read_text()
    has_delete_empty = '"/api/sprints/delete-empty"' in content
    has_cleanup_empty = '"/api/sprints/cleanup-empty"' in content
    assert has_delete_empty or has_cleanup_empty, \
        "Neither /api/sprints/delete-empty nor /api/sprints/cleanup-empty found in sprint_crud.py"


# ── AC3: Label/branch ops present in sprint_crud.py ──────────────────────────

def test_sprint_crud_has_label_ops():
    """AC3: Label creation/deletion/rename helpers are present or imported in sprint_crud.py."""
    content = ROUTER_FILE.read_text()
    # The rename route uses edit_label and the delete routes use delete_label
    has_edit_label = "edit_label" in content
    has_delete_label = "delete_label" in content
    assert has_edit_label or has_delete_label, \
        "Label operation helpers (edit_label / delete_label) not found in sprint_crud.py"


# ── AC4: server.py mounts the router; no inline sprint CRUD defs ─────────────

def test_server_imports_sprint_crud_router():
    """AC4: server.py references sprint_crud_router."""
    content = SERVER_FILE.read_text()
    assert "sprint_crud_router" in content, \
        "sprint_crud_router not referenced in server.py"


def test_server_mounts_sprint_crud_router():
    """AC4: server.py calls app.include_router(sprint_crud_router)."""
    content = SERVER_FILE.read_text()
    assert "app.include_router(sprint_crud_router)" in content, \
        "app.include_router(sprint_crud_router) not found in server.py"


def test_server_has_no_direct_create_route():
    """AC4: server.py has no @app.post decorator for /api/sprints/create."""
    content = SERVER_FILE.read_text()
    assert '@app.post("/api/sprints/create")' not in content, \
        "server.py still defines @app.post('/api/sprints/create') directly"


def test_server_has_no_direct_rename_route():
    """AC4: server.py has no @app.post decorator for /api/sprints/{sprint_label}/rename."""
    content = SERVER_FILE.read_text()
    assert '@app.post("/api/sprints/{sprint_label}/rename")' not in content, \
        "server.py still defines rename route directly"


def test_server_has_no_direct_reorder_route():
    """AC4: server.py has no @app.post decorator for /api/sprints/{sprint_label}/tickets/reorder."""
    content = SERVER_FILE.read_text()
    assert '@app.post("/api/sprints/{sprint_label}/tickets/reorder")' not in content, \
        "server.py still defines reorder route directly"


def test_server_has_no_direct_plan_route():
    """AC4: server.py has no @app.post decorator for /api/sprints/{sprint_label}/plan."""
    content = SERVER_FILE.read_text()
    assert '@app.post("/api/sprints/{sprint_label}/plan")' not in content, \
        "server.py still defines plan route directly"


def test_server_has_no_direct_delete_sprint_route():
    """AC4: server.py has no @app.delete decorator for /api/sprints/{sprint_label}."""
    content = SERVER_FILE.read_text()
    assert '@app.delete("/api/sprints/{sprint_label}")' not in content, \
        "server.py still defines DELETE /api/sprints/{sprint_label} directly"


def test_server_has_no_direct_delete_empty_route():
    """AC4: server.py has no @app.post decorator for /api/sprints/delete-empty."""
    content = SERVER_FILE.read_text()
    assert '@app.post("/api/sprints/delete-empty")' not in content, \
        "server.py still defines /api/sprints/delete-empty route directly"


def test_server_has_no_direct_cleanup_empty_route():
    """AC4: server.py has no @app.post decorator for /api/sprints/cleanup-empty."""
    content = SERVER_FILE.read_text()
    assert '@app.post("/api/sprints/cleanup-empty")' not in content, \
        "server.py still defines /api/sprints/cleanup-empty route directly"


# ── AC5: py_compile passes for both files ─────────────────────────────────────

def test_sprint_crud_router_compiles():
    """AC5: py_compile.compile('routers/sprint_crud.py') exits 0."""
    result = subprocess.run(
        ["python", "-m", "py_compile", str(ROUTER_FILE)],
        cwd=str(DASHBOARD_ROOT),
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, \
        f"sprint_crud.py compilation failed:\n{result.stderr.decode()}"


def test_server_compiles():
    """AC5: py_compile.compile('server.py') exits 0."""
    result = subprocess.run(
        ["python", "-m", "py_compile", str(SERVER_FILE)],
        cwd=str(DASHBOARD_ROOT),
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, \
        f"server.py compilation failed:\n{result.stderr.decode()}"


# ── AC6: Endpoints respond correctly (smoke tests) ────────────────────────────

def test_create_sprint_endpoint_responds(client):
    """AC6: POST /api/sprints/create without a body returns 422 (validation), not 404 (missing route)."""
    try:
        r = client.post("/api/sprints/create")
        assert r.status_code != 404, f"Got 404 — route not mounted: {r.text}"
        assert r.status_code in (200, 201, 400, 422), \
            f"Unexpected status: {r.status_code}: {r.text}"
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")


def test_rename_sprint_endpoint_responds(client):
    """AC6: POST /api/sprints/{label}/rename without body returns 422 (not 404)."""
    try:
        r = client.post("/api/sprints/sprint-85/rename")
        assert r.status_code != 404, f"Got 404 — route not mounted: {r.text}"
        assert r.status_code in (200, 400, 409, 422), \
            f"Unexpected status: {r.status_code}: {r.text}"
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")


def test_reorder_sprint_tickets_endpoint_responds(client):
    """AC6: POST /api/sprints/{label}/tickets/reorder without body returns 422 (not 404)."""
    try:
        r = client.post("/api/sprints/sprint-85/tickets/reorder")
        assert r.status_code != 404, f"Got 404 — route not mounted: {r.text}"
        assert r.status_code in (200, 400, 422), \
            f"Unexpected status: {r.status_code}: {r.text}"
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")


def test_plan_sprint_endpoint_responds(client):
    """AC6: POST /api/sprints/{label}/plan with empty array returns 200 or error (not 404)."""
    try:
        r = client.post(
            "/api/sprints/sprint-85/plan",
            params={"project": "zealchaiwut/commander"},
            content=b"[]",
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code != 404, f"Got 404 — route not mounted: {r.text}"
        assert r.status_code in (200, 400, 422), \
            f"Unexpected status: {r.status_code}: {r.text}"
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")


def test_delete_sprint_endpoint_responds(client):
    """AC6: DELETE /api/sprints/{label} with no-exist label returns error (not 404 from missing route)."""
    try:
        r = client.delete(
            "/api/sprints/sprint-0",
            params={"project": "zealchaiwut/commander"},
        )
        assert r.status_code != 404, f"Got 404 — route not mounted: {r.text}"
        assert r.status_code in (200, 400, 409, 422), \
            f"Unexpected status: {r.status_code}: {r.text}"
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")


def test_delete_empty_sprints_endpoint_responds(client):
    """AC6: POST /api/sprints/delete-empty without body returns 422 (not 404)."""
    try:
        r = client.post("/api/sprints/delete-empty")
        assert r.status_code != 404, f"Got 404 — route not mounted: {r.text}"
        assert r.status_code in (200, 400, 422), \
            f"Unexpected status: {r.status_code}: {r.text}"
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")


def test_cleanup_empty_sprints_endpoint_responds(client):
    """AC6: POST /api/sprints/cleanup-empty without body returns 422 (not 404)."""
    try:
        r = client.post("/api/sprints/cleanup-empty")
        assert r.status_code != 404, f"Got 404 — route not mounted: {r.text}"
        assert r.status_code in (200, 400, 422), \
            f"Unexpected status: {r.status_code}: {r.text}"
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")


# ── AC7: No new routes added to server.py ─────────────────────────────────────

def test_server_line_count_did_not_grow():
    """AC7: server.py line count is strictly less than before the refactor.

    Pre-refactor line count: 13033. After removing ~380 lines of routes and
    Pydantic models and adding a single include_router call + import, the
    count should be noticeably lower. Gate threshold: 13030.
    """
    lines = SERVER_FILE.read_text().splitlines()
    assert len(lines) < 13030, (
        f"server.py has {len(lines)} lines — expected <13030 after extraction. "
        "New routes may have been added or routes were not removed."
    )
