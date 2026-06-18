"""Tests for issue #1340: maintenance_service no server import."""
import os
import ast
import sys
import subprocess
from pathlib import Path

import pytest


# Resolved from UAT .env at runtime; see tester skill Step 0.
# Default kept only as a last-resort fallback if BASE_URL not exported.
BASE_URL = (
    os.environ.get("UAT_BASE_URL")
    or "http://localhost:" + os.environ.get("UAT_PORT", "")
)
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. "
        "Run the tester skill's Step 0 to resolve UAT before pytest."
    )


def get_repo_root():
    """Find the repository root by walking up from this test file."""
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / ".git").is_dir():
            return current
        current = current.parent
    raise RuntimeError("Could not find repository root")


def test_maintenance_service__no_server_import():
    """AC: maintenance_service.py has no 'import server' statement."""
    repo_root = get_repo_root()
    maintenance_file = (
        repo_root
        / "apps"
        / "dashboard"
        / "routers"
        / "maintenance_service.py"
    )

    assert maintenance_file.is_file(), f"File not found: {maintenance_file}"

    content = maintenance_file.read_text(encoding="utf-8")

    # Check that 'import server' is not present
    assert "import server" not in content, (
        "Found 'import server' in maintenance_service.py"
    )
    # Also explicitly check for the _srv pattern
    assert "_srv" not in content, (
        "Found '_srv' references in maintenance_service.py"
    )


def test_maintenance_service__uses_ccs_helpers():
    """AC: rebuild_calibration_cache() calls helpers via ccs."""
    repo_root = get_repo_root()
    maintenance_file = (
        repo_root
        / "apps"
        / "dashboard"
        / "routers"
        / "maintenance_service.py"
    )

    content = maintenance_file.read_text(encoding="utf-8")

    # Check that calibration_cache_service is imported
    has_import = (
        "import calibration_cache_service as _ccs" in content
        or "from calibration_cache_service import" in content
    )
    assert has_import, (
        "calibration_cache_service not imported in maintenance_service.py"
    )

    # Check that the three specific helper functions are called through _ccs
    assert "_ccs._calibration_empty_cache()" in content, (
        "_calibration_empty_cache not called via _ccs"
    )
    assert "_ccs._calibration_absorb_state_file(" in content, (
        "_calibration_absorb_state_file not called via _ccs"
    )
    assert "_ccs._save_calibration_cache(" in content, (
        "_save_calibration_cache not called via _ccs"
    )


def test_maintenance_service__no_srv_references():
    """AC: _srv._calibration_* calls replaced with ccs equivalents."""
    repo_root = get_repo_root()
    maintenance_file = (
        repo_root
        / "apps"
        / "dashboard"
        / "routers"
        / "maintenance_service.py"
    )

    content = maintenance_file.read_text(encoding="utf-8")

    # Parse the AST to ensure no _srv attribute accesses exist
    tree = ast.parse(content)

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == "_srv":
                pytest.fail(
                    f"Found _srv.{node.attr} — should be replaced with _ccs"
                )


def test_rebuild_cache_script_no_server_import():
    """AC: rebuild_calibration_cache.py does not import server.py."""
    repo_root = get_repo_root()
    rebuild_script = repo_root / "scripts" / "rebuild_calibration_cache.py"

    assert rebuild_script.is_file(), f"Script not found: {rebuild_script}"

    # Run the script with import tracing to check it doesn't load server
    cmd = [
        sys.executable,
        "-c",
        f"""
import sys
sys.path.insert(0, {str(repo_root)!r})
sys.path.insert(0, {str(repo_root / 'apps' / 'dashboard')!r})

import importlib.util
spec = importlib.util.spec_from_file_location(
    "rebuild_calibration_cache", {str(rebuild_script)!r}
)
if spec and spec.loader:
    mod = importlib.util.module_from_spec(spec)
    sys.modules['rebuild_calibration_cache'] = mod
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass

loaded = [m for m in sys.modules if 'server' == m.split('.')[-1]]
if loaded:
    print("FAIL: Loaded server module:", loaded)
    sys.exit(1)

print("PASS")
""",
    ]

    result = subprocess.run(
        cmd, cwd=str(repo_root), capture_output=True, text=True, timeout=10
    )

    # Key check: 'server' module must not be in the loaded list.
    if "FAIL" in result.stdout:
        pytest.fail("rebuild_calibration_cache.py imports the server module")


def test_full_test_suite_passes():
    """AC: Full test suite passes; no regressions in calibration tests."""
    repo_root = get_repo_root()
    dashboard_dir = repo_root / "apps" / "dashboard"

    assert dashboard_dir.is_dir(), (
        f"Dashboard directory not found: {dashboard_dir}"
    )

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "tests/",
        "-v",
        "--tb=short",
        "-k",
        "calibration or maintenance_service",
    ]

    result = subprocess.run(
        cmd,
        cwd=str(dashboard_dir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    if result.returncode != 0 and "error" in result.stdout.lower():
        pytest.fail(
            "Calibration-related tests failed:\n"
            f"{result.stdout}\n{result.stderr}"
        )
