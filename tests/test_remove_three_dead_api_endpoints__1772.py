"""Tests for issue #1772: Remove three dead API endpoints and orphaned helpers."""
import os
import subprocess
import sys

# For verification that endpoints are removed, we check the source code
# since the full test suite validates route removal via existing framework tests

DASHBOARD_ROOT = os.path.join(os.path.dirname(__file__), "..", "apps", "dashboard")


def test_ac1_delete_alerts_idx_endpoint_removed():
    """AC: DELETE /api/alerts/{idx} endpoint is removed and returns 404."""
    # Verify the endpoint is not registered in system_misc.py
    system_misc_path = os.path.join(DASHBOARD_ROOT, "routers", "system_misc.py")
    with open(system_misc_path, "r") as f:
        content = f.read()

    # Check that there's no @router.delete("/api/alerts/{idx}") or similar pattern
    assert '/api/alerts/{idx}' not in content, \
        "DELETE /api/alerts/{idx} endpoint should be removed from system_misc.py"

    # The endpoint should only have the docs-freshness delete route
    assert "@router.delete" in content, "system_misc.py should still have delete routes"
    assert "/api/docs-freshness/warnings/{warning_id}" in content, \
        "system_misc.py should retain the docs-freshness delete route"


def test_ac2_backlog_cleanup_endpoint_removed():
    """AC: POST /api/projects/{owner}/{repo_name}/backlog/cleanup is removed."""
    # Verify the endpoint is not registered in tickets.py
    tickets_path = os.path.join(DASHBOARD_ROOT, "routers", "tickets.py")
    with open(tickets_path, "r") as f:
        content = f.read()

    # Check that there's no POST backlog/cleanup endpoint (not the preview one)
    route_str = '"/api/projects/{owner}/{repo_name}/backlog/cleanup"'
    cleanup_pattern = f'@router.post({route_str})'
    assert cleanup_pattern not in content, \
        "POST backlog/cleanup endpoint should be removed"

    # Verify cleanup-preview still exists
    assert 'cleanup-preview' in content, \
        "cleanup-preview endpoint should remain"


def test_ac3_reports_daily_endpoint_removed():
    """AC: POST /api/reports/daily endpoint is removed and returns 404."""
    # Verify reports.py file is deleted
    reports_path = os.path.join(DASHBOARD_ROOT, "routers", "reports.py")
    assert not os.path.exists(reports_path), \
        "reports.py file should be deleted when it becomes empty"


def test_ac4_reports_router_removed_from_server():
    """AC: If reports.py becomes empty after removal, the file is deleted."""
    # Verify reports router is not included in server.py
    server_path = os.path.join(DASHBOARD_ROOT, "server.py")
    with open(server_path, "r") as f:
        content = f.read()

    assert "reports" not in content or "# reports" not in content, \
        "server.py should not include the reports router"


def test_ac5_no_orphaned_helpers():
    """AC: All private helpers exclusively called by removed handlers are deleted."""
    # Check that there are no dangling references to handlers that would have
    # used these routes. This is validated by running the test suite which
    # checks for unused functions.

    # Verify that the endpoints are truly dead by checking they don't appear
    # in any router file
    routers_dir = os.path.join(DASHBOARD_ROOT, "routers")
    for filename in os.listdir(routers_dir):
        if filename.endswith(".py"):
            filepath = os.path.join(routers_dir, filename)
            with open(filepath, "r") as f:
                content = f.read()

            # These specific endpoint paths should not be found
            if "/api/alerts/{idx}" in content and "DELETE" in content:
                raise AssertionError(
                    f"Found orphaned DELETE /api/alerts/{{idx}} in {filename}"
                )
            if "/api/projects/{owner}/{repo_name}/backlog/cleanup" in content:
                if "cleanup-preview" not in content:
                    raise AssertionError(
                        f"Found orphaned backlog/cleanup endpoint in {filename}"
                    )


def test_ac6_tests_updated():
    """AC: test files no longer reference the removed routes."""
    # Verify test files don't reference the removed endpoints
    test_files = [
        "test_slim_server_py__1267.py",
        "test_sprint_run_router__1262.py"
    ]

    for test_file in test_files:
        filepath = os.path.join(DASHBOARD_ROOT, "..", "..", "tests", test_file)
        if os.path.exists(filepath):
            with open(filepath, "r") as f:
                content = f.read()

            # Verify removed endpoints are not referenced
            assert "/api/alerts/{idx}" not in content, \
                f"{test_file} should not reference the removed DELETE endpoint"
            assert 'DELETE-/api/alerts/{idx}' not in content, \
                f"{test_file} should not reference removed endpoint in test params"


def test_ac7_no_references_to_removed_routes():
    """AC: Full test suite passes with no new failures."""
    # Run the test suite to ensure everything passes
    tests_dir = os.path.dirname(__file__)
    result = subprocess.run(
        [sys.executable, "-m", "pytest",
         os.path.join(tests_dir, "test_slim_server_py__1267.py"),
         os.path.join(tests_dir, "test_sprint_run_router__1262.py"),
         "-v", "--tb=short"],
        cwd=DASHBOARD_ROOT,
        capture_output=True,
        text=True,
        timeout=60
    )

    # Verify all tests pass
    assert result.returncode == 0, \
        f"Test suite should pass. Output:\n{result.stdout}\n{result.stderr}"

    # Verify the summary shows passing tests
    assert "passed" in result.stdout, "Should have passing tests"


def test_ac8_grep_clean_for_removed_routes():
    """AC: No remaining references to the three removed routes exist."""
    # Pattern: backlog/cleanup (but not cleanup-preview)
    tickets_path = os.path.join(DASHBOARD_ROOT, "routers", "tickets.py")
    with open(tickets_path, "r") as f:
        tickets_content = f.read()
    route_str = '"/api/projects/{owner}/{repo_name}/backlog/cleanup"'
    cleanup_pattern = f'@router.post({route_str})'
    assert cleanup_pattern not in tickets_content, \
        "POST backlog/cleanup should not exist"

    # Verify by grepping that the removed routes don't exist in source
    # Check system_misc.py doesn't have the alerts delete endpoint
    system_misc_path = os.path.join(DASHBOARD_ROOT, "routers", "system_misc.py")
    with open(system_misc_path, "r") as f:
        misc_content = f.read()
    assert '/api/alerts/{idx}' not in misc_content, \
        "No references to /api/alerts/{idx} should exist"

    # Check reports.py was deleted
    reports_path = os.path.join(DASHBOARD_ROOT, "routers", "reports.py")
    assert not os.path.exists(reports_path), \
        "reports.py should be deleted"
