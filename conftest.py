"""Root pytest configuration for Commander tester.

Ensures sys.path includes repo root so services/* modules can be imported
from tests anywhere in the project.
"""
import os
import socket
import sys
from pathlib import Path

# Add repo root to sys.path
_REPO_ROOT = Path(__file__).parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _uat_server_reachable() -> bool:
    """Return True if the UAT server is reachable at UAT_BASE_URL or UAT_PORT."""
    url = os.environ.get("UAT_BASE_URL", "")
    port_str = os.environ.get("UAT_PORT", "")
    if not url and not port_str:
        return False
    if url:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            host = parsed.hostname or "localhost"
            port = parsed.port or 80
        except Exception:
            return False
    else:
        try:
            host, port = "localhost", int(port_str)
        except ValueError:
            return False
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


# Modules that require a live UAT server — skipped when server is not reachable.
_LIVE_SERVER_TEST_MODULES = frozenset({"test_projbyslug_population__1978"})


def pytest_collection_modifyitems(config, items):
    """Skip UAT live-server tests when the server is not reachable.

    Prevents the sprint manager's pytest gate from failing due to a missing
    server rather than a code defect.  The tester agent sets UAT_BASE_URL
    before running its own pytest session; the gate runs without it.
    """
    live_items = [
        item for item in items
        if any(m in str(item.fspath) for m in _LIVE_SERVER_TEST_MODULES)
    ]
    if not live_items:
        return
    if _uat_server_reachable():
        return
    import pytest as _pytest
    skip = _pytest.mark.skip(
        reason="UAT server not reachable — set UAT_BASE_URL/UAT_PORT to run live-server tests"
    )
    for item in live_items:
        item.add_marker(skip)
