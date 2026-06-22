"""Tests for issue #1260: Extract Sprint Finish Preview Routes to routers/sprint_finish.py.

Extracts the GET/read-only preview handlers from server.py into a dedicated router:
  GET /api/projects/{owner}/{repo_name}/sprints/{label}/finish-preview
  GET /api/projects/{owner}/{repo_name}/sprints/{label}/bulk-complete-preview

AC verified:
  AC1 - routers/sprint_finish.py exists and contains both preview route handlers
  AC2 - server.py registers the router; neither handler remains defined directly in server.py
  AC3 - No new routes added to server.py
  AC4 - Preview endpoints respond identically (same method, path, status codes, schema)
  AC5 - python -m py_compile server.py routers/sprint_finish.py exits 0
  AC6 - Write/mutation paths (finish, bulk-complete) remain in server.py untouched
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


# ── AC1: Router file exists and contains both preview route handlers ──────────

def test_sprint_finish_router_file_exists():
    """AC1: routers/sprint_finish.py exists."""
    assert ROUTER_FILE.exists(), f"Router file not found: {ROUTER_FILE}"


def test_sprint_finish_router_has_apirouter():
    """AC1: sprint_finish.py declares an APIRouter assigned to `router`."""
    content = ROUTER_FILE.read_text()
    assert "APIRouter" in content, "APIRouter not imported in sprint_finish.py"
    assert "router = APIRouter(" in content, "`router = APIRouter(...)` not found"


def test_sprint_finish_has_finish_preview_route():
    """AC1: finish-preview GET route is defined in sprint_finish.py."""
    content = ROUTER_FILE.read_text()
    assert "finish-preview" in content, \
        "finish-preview route not found in sprint_finish.py"
    assert "@router.get" in content, "@router.get not found in sprint_finish.py"


def test_sprint_finish_has_bulk_complete_preview_route():
    """AC1: bulk-complete-preview GET route is defined in sprint_finish.py."""
    content = ROUTER_FILE.read_text()
    assert "bulk-complete-preview" in content, \
        "bulk-complete-preview route not found in sprint_finish.py"


# ── AC2: server.py registers the router; handlers no longer in server.py ─────

def test_server_has_no_direct_finish_preview_route():
    """AC2: server.py has no @app.get decorator for finish-preview."""
    content = SERVER_FILE.read_text()
    assert '@app.get("/api/projects/{owner}/{repo_name}/sprints/{label}/finish-preview")' not in content, \
        "server.py still defines the finish-preview route handler directly"


def test_server_has_no_direct_bulk_complete_preview_route():
    """AC2: server.py has no @app.get decorator for bulk-complete-preview."""
    content = SERVER_FILE.read_text()
    assert '@app.get("/api/projects/{owner}/{repo_name}/sprints/{label}/bulk-complete-preview")' not in content, \
        "server.py still defines the bulk-complete-preview route handler directly"


def test_server_imports_sprint_finish_router():
    """AC2: server.py references sprint_finish_router."""
    content = SERVER_FILE.read_text()
    assert "sprint_finish_router" in content, \
        "sprint_finish_router not referenced in server.py"


def test_server_mounts_sprint_finish_router():
    """AC2: server.py calls app.include_router(sprint_finish_router)."""
    content = SERVER_FILE.read_text()
    assert "app.include_router(sprint_finish_router)" in content, \
        "app.include_router(sprint_finish_router) not found in server.py"


# ── AC3: No new routes added to server.py ────────────────────────────────────

def test_server_line_count_did_not_grow():
    """AC3: server.py line count is strictly less than before the refactor.

    Pre-refactor line count: 11755. Removing ~146 lines of route handlers and
    adding 2 lines (import + include_router) gives ~11611. Gate: 11750.
    """
    lines = SERVER_FILE.read_text().splitlines()
    assert len(lines) < 11750, (
        f"server.py has {len(lines)} lines — expected <11750 after extraction. "
        "New routes may have been added or handlers were not removed."
    )


# ── AC4: Preview endpoints respond identically ────────────────────────────────

def test_finish_preview_invalid_label_returns_400(client):
    """AC4: GET finish-preview with invalid label returns 400, not 404."""
    try:
        r = client.get("/api/projects/zealchaiwut/commander/sprints/not-a-sprint/finish-preview")
        assert r.status_code == 400, \
            f"Expected 400 for invalid label, got {r.status_code}: {r.text}"
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")


def test_finish_preview_valid_label_returns_expected_shape(client):
    """AC4: GET finish-preview with a real sprint label returns a structured JSON response."""
    try:
        r = client.get("/api/projects/zealchaiwut/commander/sprints/sprint-85/finish-preview")
        assert r.status_code in (200, 409, 422), \
            f"Unexpected status: {r.status_code}: {r.text}"
        if r.status_code == 200:
            data = r.json()
            assert "all_tickets" in data, f"'all_tickets' missing: {data}"
            assert "uat_tickets" in data, f"'uat_tickets' missing: {data}"
            assert "non_uat_tickets" in data, f"'non_uat_tickets' missing: {data}"
            assert "base_label" in data, f"'base_label' missing: {data}"
            assert "next_sprint_label" in data, f"'next_sprint_label' missing: {data}"
            assert "next_sprint_exists" in data, f"'next_sprint_exists' missing: {data}"
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")


def test_bulk_complete_preview_invalid_label_returns_400(client):
    """AC4: GET bulk-complete-preview with invalid label returns 400, not 404."""
    try:
        r = client.get("/api/projects/zealchaiwut/commander/sprints/not-a-sprint/bulk-complete-preview")
        assert r.status_code == 400, \
            f"Expected 400 for invalid label, got {r.status_code}: {r.text}"
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")


def test_bulk_complete_preview_valid_label_not_404(client):
    """AC4: GET bulk-complete-preview with a valid label is routed (not 404)."""
    try:
        r = client.get("/api/projects/zealchaiwut/commander/sprints/sprint-85/bulk-complete-preview")
        assert r.status_code != 404, \
            f"Got 404 — route not mounted: {r.text}"
        assert r.status_code in (200, 400, 409, 422), \
            f"Unexpected status: {r.status_code}: {r.text}"
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")


# ── AC5: py_compile passes for both files ─────────────────────────────────────

def test_sprint_finish_router_compiles():
    """AC5: py_compile.compile('routers/sprint_finish.py') exits 0."""
    result = subprocess.run(
        ["python", "-m", "py_compile", str(ROUTER_FILE)],
        cwd=str(DASHBOARD_ROOT),
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, \
        f"sprint_finish.py compilation failed:\n{result.stderr.decode()}"


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


# ── AC6: Write/mutation paths remain in server.py untouched ──────────────────

def test_finish_write_path_still_in_server():
    """AC6: The POST finish handler is still defined in server.py."""
    content = SERVER_FILE.read_text()
    assert '@app.post("/api/projects/{owner}/{repo_name}/sprints/{label}/finish")' in content, \
        "POST finish route was removed from server.py — write paths must remain"


def test_bulk_complete_write_path_still_in_server():
    """AC6: The POST bulk-complete handler is still defined in server.py."""
    content = SERVER_FILE.read_text()
    assert '@app.post("/api/projects/{owner}/{repo_name}/sprints/{label}/bulk-complete")' in content, \
        "POST bulk-complete route was removed from server.py — write paths must remain"


def test_finish_write_endpoint_responds(client):
    """AC6: POST finish without body returns 422 (validation), not 404."""
    try:
        r = client.post("/api/projects/zealchaiwut/commander/sprints/sprint-85/finish")
        assert r.status_code != 404, \
            f"Got 404 — finish write route not mounted: {r.text}"
        assert r.status_code in (200, 400, 422), \
            f"Unexpected status: {r.status_code}: {r.text}"
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")


def test_bulk_complete_write_endpoint_responds(client):
    """AC6: POST bulk-complete without body returns 422 (validation), not 404."""
    try:
        r = client.post("/api/projects/zealchaiwut/commander/sprints/sprint-85/bulk-complete")
        assert r.status_code != 404, \
            f"Got 404 — bulk-complete write route not mounted: {r.text}"
        assert r.status_code in (200, 400, 422), \
            f"Unexpected status: {r.status_code}: {r.text}"
    except httpx.ConnectError:
        pytest.skip("UAT server not responding")
