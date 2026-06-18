"""Tests for maintenance_router import and include_router formatting (issue #1343)."""
import re


def test_maintenance_router_on_own_import_line():
    """
    AC1: maintenance_router should appear on its own dedicated line
    within the `from routers import (...)` block, not sharing a line
    with timeline_router or any other name.
    """
    with open("apps/dashboard/server.py", "r") as f:
        content = f.read()

    # Find the from routers import block
    import_block_match = re.search(
        r"from routers import \((.*?)\)",
        content,
        re.DOTALL
    )
    assert import_block_match, "Could not find 'from routers import (...)' block"

    import_block = import_block_match.group(1)

    # Check that maintenance_router is on its own line
    # It should match pattern: whitespace + maintenance_router + optional comma + newline
    lines = import_block.split("\n")
    maintenance_found = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("maintenance_router"):
            # Verify it's the only name on this line
            # Valid patterns: "maintenance_router," or "maintenance_router" (last line)
            assert stripped in ("maintenance_router,", "maintenance_router"), \
                f"maintenance_router shares a line with other imports: {line}"
            maintenance_found = True
            break

    assert maintenance_found, "maintenance_router not found in imports"


def test_maintenance_router_not_sharing_line_with_timeline_router():
    """
    AC1 (extended): Verify timeline_router and maintenance_router
    are on separate lines.
    """
    with open("apps/dashboard/server.py", "r") as f:
        content = f.read()

    import_block_match = re.search(
        r"from routers import \((.*?)\)",
        content,
        re.DOTALL
    )
    assert import_block_match

    import_block = import_block_match.group(1)

    # Check that no line contains both timeline_router and maintenance_router
    lines = import_block.split("\n")
    for line in lines:
        stripped = line.strip()
        assert not (
            "timeline_router" in stripped and "maintenance_router" in stripped
        ), f"timeline_router and maintenance_router on same line: {line}"


def test_include_router_in_grouped_block():
    """
    AC2: app.include_router(maintenance_router) should be moved into
    the grouped include_router block alongside other routers,
    not adjacent to the logs_service import.
    """
    with open("apps/dashboard/server.py", "r") as f:
        lines = f.readlines()

    # Find the line with logs_service import
    logs_service_import_idx = None
    for i, line in enumerate(lines):
        if "from routers.logs_service import" in line:
            logs_service_import_idx = i
            break

    assert logs_service_import_idx is not None, \
        "Could not find 'from routers.logs_service import' line"

    # Find the first include_router block (grouped section)
    include_router_start_idx = None
    for i in range(logs_service_import_idx + 1, len(lines)):
        if "app.include_router(" in lines[i]:
            include_router_start_idx = i
            break

    assert include_router_start_idx is not None, \
        "Could not find first app.include_router call"

    # Find the maintenance_router include call
    maintenance_include_idx = None
    for i, line in enumerate(lines):
        if "app.include_router(maintenance_router)" in line:
            maintenance_include_idx = i
            break

    assert maintenance_include_idx is not None, \
        "Could not find app.include_router(maintenance_router)"

    # Verify it's in the grouped block: should be at or after the start of the block
    # and definitely not immediately after the logs_service import (which would be
    # the very next line after logs_service).
    assert maintenance_include_idx >= include_router_start_idx, \
        "maintenance_router include should be within the grouped block"

    # Verify there is a blank line between logs_service import and the first include_router
    blank_line_between = False
    for i in range(logs_service_import_idx + 1, include_router_start_idx):
        if lines[i].strip() == "":
            blank_line_between = True
            break

    assert blank_line_between, \
        "There should be a blank line between logs_service import and the include_router block"


def test_blank_line_before_grouped_include_block():
    """
    AC3: A blank line should separate the from routers.logs_service import
    statement from the include_router block (consistent style).
    """
    with open("apps/dashboard/server.py", "r") as f:
        lines = f.readlines()

    # Find logs_service import
    logs_service_idx = None
    for i, line in enumerate(lines):
        if "from routers.logs_service import" in line:
            logs_service_idx = i
            break

    assert logs_service_idx is not None

    # Find first include_router after logs_service
    include_router_idx = None
    for i in range(logs_service_idx + 1, len(lines)):
        if "app.include_router(" in lines[i]:
            include_router_idx = i
            break

    assert include_router_idx is not None

    # There should be at least one blank line between them
    blank_line_found = False

    for i in range(logs_service_idx + 1, include_router_idx):
        if lines[i].strip() == "":
            blank_line_found = True
            break

    assert blank_line_found, \
        "No blank line found between logs_service import and include_router block"


def test_maintenance_endpoint_reachable():
    """
    AC4: No functional changes — maintenance router is still registered
    and endpoints are reachable. This is verified by checking that the
    router is imported and included.
    """
    with open("apps/dashboard/server.py", "r") as f:
        content = f.read()

    # Verify maintenance_router is imported from routers
    assert "maintenance_router" in content, "maintenance_router not imported"

    # Verify it's included
    assert "app.include_router(maintenance_router)" in content, \
        "maintenance_router not included in app"


def test_one_name_per_line_consistency():
    """
    AC5: The from routers import (...) block follows one-name-per-line
    formatting consistently after the change.
    """
    with open("apps/dashboard/server.py", "r") as f:
        content = f.read()

    import_block_match = re.search(
        r"from routers import \((.*?)\)",
        content,
        re.DOTALL
    )
    assert import_block_match

    import_block = import_block_match.group(1)
    lines = import_block.split("\n")

    # Each non-empty line should contain exactly one router name
    # (ignoring whitespace and commas)
    for line in lines:
        stripped = line.strip()
        if not stripped:  # Skip empty lines
            continue

        # Remove trailing comma and whitespace
        name = stripped.rstrip(",").strip()

        # A valid line should be just the name or empty
        # Count the number of _router words (each router name ends with _router)
        router_count = name.count("_router")
        assert router_count <= 1, \
            f"Line contains multiple routers: {line}"
