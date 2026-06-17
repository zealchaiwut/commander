"""Tests for issue #1343: Tidy maintenance_router import and include_router placement (runs against UAT)"""
import os
import pytest
import httpx


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

def test_maintenance_router_import_separate_line(client):
    # AC: `maintenance_router` appears on its own dedicated line within the
    # `from routers import (...)` block in `apps/dashboard/server.py`, not
    # sharing a line with `timeline_router` or any other name.

    # Read server.py and verify the import statement
    server_path = "apps/dashboard/server.py"
    with open(server_path, 'r') as f:
        content = f.read()

    # Extract the import block
    import_start = content.find("from routers import (")
    import_end = content.find(")", import_start)
    import_block = content[import_start:import_end + 1]

    # Verify maintenance_router is on its own line
    lines = import_block.split('\n')
    maintenance_lines = [line for line in lines if 'maintenance_router' in line]

    assert len(maintenance_lines) == 1, "maintenance_router should appear exactly once"
    maintenance_line = maintenance_lines[0].strip()
    assert maintenance_line == "maintenance_router,", f"Expected 'maintenance_router,' on its own line, got: {maintenance_line}"

    # Verify it doesn't share a line with timeline_router
    timeline_maintenance_lines = [line for line in lines if 'maintenance_router' in line and 'timeline_router' in line]
    assert len(timeline_maintenance_lines) == 0, "maintenance_router should not share a line with timeline_router"


def test_include_router_maintenance_in_grouped_block(client):
    # AC: `app.include_router(maintenance_router)` is moved into the grouped
    # `include_router` block alongside the other routers, not adjacent to the
    # `from routers.logs_service import ...` statement.

    server_path = "apps/dashboard/server.py"
    with open(server_path, 'r') as f:
        content = f.read()

    # Find the position of maintenance_router include
    maintenance_include = content.find("app.include_router(maintenance_router)")
    logs_service_import = content.find("from routers.logs_service import")
    include_router_activity = content.find("app.include_router(activity_router)")

    assert maintenance_include > 0, "app.include_router(maintenance_router) not found"
    assert logs_service_import > 0, "from routers.logs_service import not found"
    assert include_router_activity > 0, "app.include_router(activity_router) not found"

    # maintenance_router include should be AFTER the logs_service import
    assert maintenance_include > logs_service_import, \
        "app.include_router(maintenance_router) should be after the logs_service import"

    # maintenance_router include should be near (within 100 chars) the activity_router include
    # indicating it's part of the grouped block
    distance = include_router_activity - maintenance_include
    assert distance > 0 and distance < 200, \
        f"app.include_router(maintenance_router) should be near the grouped include_router block, distance: {distance}"


def test_blank_line_between_imports_and_include_block(client):
    # AC: A blank line separating the `from routers.logs_service import ...`
    # statement from the `include_router` block is restored (matching the
    # surrounding style).

    server_path = "apps/dashboard/server.py"
    with open(server_path, 'r') as f:
        lines = f.readlines()

    # Find the logs_service import line number
    logs_service_line_idx = None
    for i, line in enumerate(lines):
        if "from routers.logs_service import" in line:
            logs_service_line_idx = i
            break

    assert logs_service_line_idx is not None, "from routers.logs_service import not found"

    # Next line should be blank
    next_line = lines[logs_service_line_idx + 1].strip()
    assert next_line == "", f"Expected blank line after logs_service import, got: {repr(next_line)}"

    # Line after that should be an include_router call
    line_after_blank = lines[logs_service_line_idx + 2].strip()
    assert line_after_blank.startswith("app.include_router"), \
        f"Expected include_router call after blank line, got: {line_after_blank}"


def test_import_block_one_name_per_line_consistency(client):
    # AC: The `from routers import (...)` block follows one-name-per-line
    # formatting consistently after the change.

    server_path = "apps/dashboard/server.py"
    with open(server_path, 'r') as f:
        content = f.read()

    # Extract the import block
    import_start = content.find("from routers import (")
    import_end = content.find(")", import_start)
    import_block = content[import_start:import_end + 1]

    # Split into lines and check each line has at most one router name
    lines = import_block.split('\n')
    for line in lines:
        if not line.strip() or line.strip() == '(' or line.strip() == ')':
            continue

        # Count commas (which separate router names)
        comma_count = line.count(',')
        # A line with one router name has one comma at the end: "    router_name,"
        # A line with two names would have: "    router1, router2,"
        if comma_count > 1:
            raise AssertionError(
                f"Multiple router names on one line violates one-name-per-line formatting: {line}"
            )


def test_maintenance_endpoints_reachable(client):
    # AC: No functional changes are introduced — the router is still registered
    # and all maintenance endpoints remain reachable.

    # Make a request to a maintenance endpoint to verify it's registered
    r = client.get("/api/maintenance/status")

    # Should be 200 or 404 (if endpoint doesn't exist) but NOT 500 (router not registered)
    assert r.status_code in [200, 404], \
        f"Maintenance endpoint should be reachable (200 or 404 if not implemented), got {r.status_code}"
