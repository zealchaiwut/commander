"""Tests for issue #1343: tidy maintenance_router import placement."""
import re


SERVER_PATH = "apps/dashboard/server.py"


def _read_server():
    with open(SERVER_PATH, "r") as f:
        return f.read()


def test_maintenance_router_import_separate_line():
    # AC: maintenance_router appears on its own dedicated line within the
    # from routers import (...) block, not sharing a line with any other name.
    content = _read_server()

    import_block_match = re.search(
        r"from routers import \((.*?)\)", content, re.DOTALL
    )
    assert import_block_match, (
        "Could not find 'from routers import (...)' block"
    )
    import_block = import_block_match.group(1)
    lines = import_block.split("\n")

    maintenance_lines = [
        ln for ln in lines if "maintenance_router" in ln
    ]
    assert len(maintenance_lines) == 1, (
        "maintenance_router should appear exactly once in the import block"
    )
    maintenance_line = maintenance_lines[0].strip()
    assert maintenance_line in ("maintenance_router,", "maintenance_router"), (
        "Expected 'maintenance_router,' on its own line, "
        f"got: {maintenance_line!r}"
    )

    shared = [
        ln for ln in lines
        if "maintenance_router" in ln and "timeline_router" in ln
    ]
    assert len(shared) == 0, (
        "maintenance_router should not share a line with timeline_router"
    )


def test_include_router_maintenance_in_grouped_block():
    # AC: app.include_router(maintenance_router) is moved into the grouped
    # include_router block, not adjacent to from routers.logs_service import.
    content = _read_server()

    maintenance_pos = content.find("app.include_router(maintenance_router)")
    logs_pos = content.find("from routers.logs_service import")
    activity_pos = content.find("app.include_router(activity_router)")

    assert maintenance_pos > 0, (
        "app.include_router(maintenance_router) not found"
    )
    assert logs_pos > 0, "from routers.logs_service import not found"
    assert activity_pos > 0, "app.include_router(activity_router) not found"

    # maintenance include must appear AFTER the logs_service import
    assert maintenance_pos > logs_pos, (
        "app.include_router(maintenance_router) should appear "
        "after the logs_service import"
    )

    # should be within 200 chars of activity_router, confirming grouped block
    distance = abs(activity_pos - maintenance_pos)
    assert distance < 200, (
        "app.include_router(maintenance_router) should be near the grouped "
        f"include_router block, distance: {distance}"
    )


def test_blank_line_between_imports_and_include_block():
    # AC: A blank line separating the from routers.logs_service import ...
    # statement from the include_router block is restored.
    with open(SERVER_PATH, "r") as f:
        lines = f.readlines()

    logs_idx = None
    for i, line in enumerate(lines):
        if "from routers.logs_service import" in line:
            logs_idx = i
            break

    assert logs_idx is not None, (
        "from routers.logs_service import not found"
    )

    next_line = lines[logs_idx + 1].strip()
    assert next_line == "", (
        f"Expected blank line after logs_service import, "
        f"got: {next_line!r}"
    )

    line_after_blank = lines[logs_idx + 2].strip()
    assert line_after_blank.startswith("app.include_router"), (
        "Expected include_router call after blank line, "
        f"got: {line_after_blank!r}"
    )


def test_import_block_one_name_per_line_consistency():
    # AC: The from routers import (...) block follows one-name-per-line
    # formatting consistently after the change.
    content = _read_server()

    import_block_match = re.search(
        r"from routers import \((.*?)\)", content, re.DOTALL
    )
    assert import_block_match, (
        "Could not find 'from routers import (...)' block"
    )
    import_block = import_block_match.group(1)

    for line in import_block.split("\n"):
        if line.count(",") > 1:
            raise AssertionError(
                "Multiple router names on one line violates "
                f"one-name-per-line formatting: {line!r}"
            )


def test_maintenance_endpoints_reachable():
    # AC: No functional changes — the router is still registered.
    # Verified by confirming app.include_router(maintenance_router) is present.
    content = _read_server()

    assert "maintenance_router" in content, (
        "maintenance_router not found in server.py"
    )
    assert "app.include_router(maintenance_router)" in content, (
        "app.include_router(maintenance_router) not found"
        " — router not registered"
    )
