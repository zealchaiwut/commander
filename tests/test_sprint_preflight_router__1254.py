"""Tests for issue #1254 — Extract preflight & DAG routes to sprint_preflight router.

AC1: routers/sprint_preflight.py exists and contains all five route handlers
AC2: Router is mounted in server.py under /api/sprints/{label} prefix with no behavioral change
AC3: server.py contains zero direct definitions of the five moved route handlers
AC4: _build_dag and cycle-detection helpers importable from (or co-located in) sprint_preflight.py
AC5: py_compile.compile('routers/sprint_preflight.py') passes with no errors
AC6: py_compile.compile('server.py') passes with no errors
AC7: All existing preflight endpoint URLs, HTTP methods, request/response shapes identical
"""
import py_compile
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))

SPRINT_PREFLIGHT_PATH = DASHBOARD_DIR / "routers" / "sprint_preflight.py"
SERVER_PATH = DASHBOARD_DIR / "server.py"

# The five handler function names that must move to the router
HANDLER_NAMES = [
    "preflight_fix",
    "get_sprint_cycle_check",
    "get_sprint_conflicts",
    "get_sprint_dep_order",
    "get_sprint_preflight",
]

# The five endpoint URL slugs / path segments
ENDPOINT_SLUGS = [
    "preflight-fix",
    "cycle-check",
    "conflicts",
    "dep-order",
    "preflight",
]


# ── AC1 ──────────────────────────────────────────────────────────────────────

def test_ac1_router_file_exists():
    """AC1: routers/sprint_preflight.py exists."""
    assert SPRINT_PREFLIGHT_PATH.exists(), (
        "routers/sprint_preflight.py not found — the new router file must be created"
    )


def test_ac1_router_has_all_five_handlers():
    """AC1: All five route handlers are defined in sprint_preflight.py."""
    source = SPRINT_PREFLIGHT_PATH.read_text(encoding="utf-8")
    missing = [name for name in HANDLER_NAMES if f"def {name}" not in source]
    assert not missing, (
        f"Handler(s) missing from sprint_preflight.py: {missing}"
    )


def test_ac1_router_has_all_five_endpoint_slugs():
    """AC1: All five endpoint URL slugs appear in sprint_preflight.py."""
    source = SPRINT_PREFLIGHT_PATH.read_text(encoding="utf-8")
    missing = [slug for slug in ENDPOINT_SLUGS if slug not in source]
    assert not missing, (
        f"Endpoint slug(s) missing from sprint_preflight.py: {missing}"
    )


# ── AC2 ──────────────────────────────────────────────────────────────────────

def test_ac2_router_imported_in_server():
    """AC2: sprint_preflight_router is imported in server.py."""
    source = SERVER_PATH.read_text(encoding="utf-8")
    assert "sprint_preflight_router" in source, (
        "sprint_preflight_router not imported in server.py"
    )


def test_ac2_router_mounted_with_include_router():
    """AC2: server.py calls app.include_router with the sprint_preflight_router."""
    source = SERVER_PATH.read_text(encoding="utf-8")
    assert "include_router(sprint_preflight_router)" in source, (
        "app.include_router(sprint_preflight_router) not found in server.py"
    )


# ── AC3 ──────────────────────────────────────────────────────────────────────

def test_ac3_server_has_no_direct_handler_definitions():
    """AC3: server.py has zero direct definitions of the five moved handlers."""
    source = SERVER_PATH.read_text(encoding="utf-8")
    still_defined = [name for name in HANDLER_NAMES if f"def {name}" in source]
    assert not still_defined, (
        f"Handler(s) still defined directly in server.py: {still_defined}"
    )


def test_ac3_server_has_no_direct_route_decorators_for_endpoints():
    """AC3: server.py has no @app.get/post decorators for the five extracted endpoints."""
    source = SERVER_PATH.read_text(encoding="utf-8")
    for slug in ENDPOINT_SLUGS:
        # Look for @app.(get|post|put|delete|patch)("/api/sprints/{...}/<slug>")
        import re
        pattern = rf'@app\.\w+\(["\'][^"\']*{re.escape(slug)}["\']'
        assert not re.search(pattern, source), (
            f"@app route decorator for {slug!r} still in server.py — it must be in the router"
        )


# ── AC4 ──────────────────────────────────────────────────────────────────────

def test_ac4_dag_builder_referenced_in_router():
    """AC4: dag_builder or _build_dag is referenced in sprint_preflight.py."""
    source = SPRINT_PREFLIGHT_PATH.read_text(encoding="utf-8")
    assert "dag_builder" in source or "_build_dag" in source, (
        "dag_builder / _build_dag not referenced in sprint_preflight.py — "
        "cycle-detection helpers must be importable from or co-located in the router"
    )


def test_ac4_no_circular_import():
    """AC4: sprint_preflight.py does not import server at module scope (would cause circular import)."""
    source = SPRINT_PREFLIGHT_PATH.read_text(encoding="utf-8")
    # Module-level (non-indented) import of server is forbidden
    # Lines like "import server" or "from server import ..." at top level
    for line in source.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(("import server", "from server import")):
            # Only flag if it's not inside a function (indented)
            if not line.startswith(" ") and not line.startswith("\t"):
                pytest.fail(
                    f"Module-level server import found in sprint_preflight.py: {line!r}\n"
                    "Use deferred import (_server() pattern) to avoid circular imports."
                )


# ── AC5 ──────────────────────────────────────────────────────────────────────

def test_ac5_sprint_preflight_compiles():
    """AC5: py_compile.compile('routers/sprint_preflight.py') passes."""
    py_compile.compile(str(SPRINT_PREFLIGHT_PATH), doraise=True)


# ── AC6 ──────────────────────────────────────────────────────────────────────

def test_ac6_server_compiles():
    """AC6: py_compile.compile('server.py') passes."""
    py_compile.compile(str(SERVER_PATH), doraise=True)


# ── AC7 ──────────────────────────────────────────────────────────────────────

def test_ac7_conflicts_endpoint_reachable():
    """AC7: GET /api/sprints/{label}/conflicts is reachable and returns JSON."""
    from fastapi.testclient import TestClient
    import server as srv

    issue = {
        "number": 1,
        "title": "Ticket 1",
        "state": "open",
        "url": "https://github.com/test/repo/issues/1",
        "body": "",
        "labels": [{"name": "sprint-99"}],
    }
    with (
        patch.object(srv.github_client, "list_open_issues_with_body", return_value=[issue]),
        patch.object(srv.github_client, "classify_issue", return_value="backlog"),
    ):
        client = TestClient(srv.app, raise_server_exceptions=False)
        resp = client.get(
            "/api/sprints/sprint-99/conflicts",
            params={"project": "testowner/testrepo"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert "conflicts" in body
    assert "pending_count" in body


def test_ac7_dep_order_endpoint_reachable():
    """AC7: GET /api/sprints/{label}/dep-order is reachable and returns JSON."""
    from fastapi.testclient import TestClient
    import server as srv

    issue = {
        "number": 2,
        "title": "Ticket 2",
        "state": "open",
        "url": "https://github.com/test/repo/issues/2",
        "body": "",
        "labels": [{"name": "sprint-99"}],
    }
    with (
        patch.object(srv.github_client, "list_open_issues_with_body", return_value=[issue]),
        patch.object(srv.github_client, "classify_issue", return_value="backlog"),
    ):
        client = TestClient(srv.app, raise_server_exceptions=False)
        resp = client.get(
            "/api/sprints/sprint-99/dep-order",
            params={"project": "testowner/testrepo"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert "has_cycle" in body
    assert "dep_hints" in body
    assert "pending_count" in body


def test_ac7_cycle_check_endpoint_reachable():
    """AC7: GET /api/sprints/{label}/cycle-check is reachable and returns JSON."""
    from fastapi.testclient import TestClient
    import server as srv

    issue = {
        "number": 3,
        "title": "Ticket 3",
        "state": "open",
        "url": "https://github.com/test/repo/issues/3",
        "body": "",
        "labels": [{"name": "sprint-99"}],
    }
    with patch.object(srv.github_client, "list_open_issues_with_body", return_value=[issue]):
        client = TestClient(srv.app, raise_server_exceptions=False)
        resp = client.get(
            "/api/sprints/sprint-99/cycle-check",
            params={"project": "testowner/testrepo"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert "has_cycle" in body


def test_ac7_invalid_sprint_label_returns_400():
    """AC7: Invalid sprint label still returns 400 (response shape unchanged)."""
    from fastapi.testclient import TestClient
    import server as srv

    client = TestClient(srv.app, raise_server_exceptions=False)
    resp = client.get(
        "/api/sprints/INVALID-LABEL!/conflicts",
        params={"project": "testowner/testrepo"},
    )
    assert resp.status_code == 400


def test_ac7_preflight_endpoint_reachable():
    """AC7: GET /api/sprints/{label}/preflight is reachable and returns JSON with ok flag."""
    from fastapi.testclient import TestClient
    import server as srv

    def _fake_get_sprint_issues(project, sprint_label):
        return []

    with patch.object(srv, "_get_sprint_issues", _fake_get_sprint_issues):
        client = TestClient(srv.app, raise_server_exceptions=False)
        resp = client.get(
            "/api/sprints/sprint-99/preflight",
            params={"project": "testowner/testrepo"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("ok") is True
    assert "warnings" in body
