"""Tests for issue #1265: Extract bulk-ticket draft/create routes to routers/bulk_tickets.py"""
import os
import subprocess
import sys
import pytest
import httpx
import json


# Resolved from UAT .env at runtime; see tester skill Step 0.
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

def test_bulk_routes_extraction__routes_exist_in_bulk_tickets_module():
    """AC: routers/bulk_tickets.py contains all four routes: POST /api/tickets/draft, /create, /bulk, /post-selected"""
    # Verify file exists and contains the four route decorators
    with open("apps/dashboard/routers/bulk_tickets.py") as f:
        content = f.read()

    # Check for all four route definitions
    assert '@router.post("/api/tickets/draft")' in content, "POST /api/tickets/draft not found"
    assert '@router.post("/api/tickets/create"' in content, "POST /api/tickets/create not found"
    assert '@router.post("/api/tickets/bulk"' in content, "POST /api/tickets/bulk not found"
    assert '@router.post("/api/tickets/bulk/{job_id}/post-selected")' in content, "POST /api/tickets/bulk/{job_id}/post-selected not found"


def test_bulk_routes_extraction__routes_removed_from_server():
    """AC: server.py no longer defines any of these four routes directly"""
    # Check server.py does not have these route decorators
    with open("apps/dashboard/server.py") as f:
        content = f.read()

    # These decorators should not be in server.py
    assert '@app.post("/api/tickets/draft")' not in content, "POST /api/tickets/draft still in server.py"
    assert '@app.post("/api/tickets/create"' not in content, "POST /api/tickets/create still in server.py"
    assert '@app.post("/api/tickets/bulk"' not in content, "POST /api/tickets/bulk still in server.py"
    assert '@app.post("/api/tickets/bulk/{job_id}/post-selected")' not in content, "POST /api/tickets/bulk/{job_id}/post-selected still in server.py"


def test_bulk_routes_extraction__router_included_in_server():
    """AC: server.py includes routers/bulk_tickets.router so routes remain mounted"""
    with open("apps/dashboard/server.py") as f:
        content = f.read()

    # Check that router is imported and included
    assert "from routers.bulk_tickets import" in content or "import routers.bulk_tickets" in content
    assert "app.include_router(bulk_tickets_router)" in content or "include_router" in content


def test_bulk_routes_extraction__python_compile_no_errors():
    """AC: python -m py_compile exits 0 with no output"""
    result = subprocess.run(
        ["python", "-m", "py_compile", "apps/dashboard/server.py", "apps/dashboard/routers/bulk_tickets.py"],
        capture_output=True,
        text=True,
        cwd="/Users/zeal-server/dev/commander/tester",
    )
    assert result.returncode == 0, f"Compile failed: {result.stderr}"
    assert not result.stdout.strip(), f"Unexpected compile output: {result.stdout}"


def test_bulk_routes_extraction__no_direct_imports():
    """AC: No other files import draft/create handler functions directly from server.py"""
    # Search for patterns like "from server import create_ticket" or similar
    import os
    import re

    cwd = "/Users/zeal-server/dev/commander/tester"
    # Exclude the routers directory itself and venv
    for root, dirs, files in os.walk(cwd):
        # Skip vendor dirs
        dirs[:] = [d for d in dirs if d not in ("venv", "__pycache__", ".git", "node_modules", ".pytest_cache")]

        for file in files:
            if not file.endswith(".py"):
                continue
            filepath = os.path.join(root, file)
            if "routers/bulk_tickets.py" in filepath or "test_bulk_routes_extraction__1265.py" in filepath:
                continue  # Skip the router file itself and the test file

            with open(filepath) as f:
                try:
                    content = f.read()
                except (UnicodeDecodeError, IOError):
                    continue

            # Check for problematic imports like: from server import create_ticket_draft
            if re.search(r"from\s+server\s+import\s+.*(?:draft|create_ticket)", content):
                pytest.fail(f"Found direct function import from server in {filepath}")
