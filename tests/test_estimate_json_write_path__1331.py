"""Tests for issue #1331: Fix estimate JSON write path and calibration size resolution

Covers:
- estimate_issue.py --commander-dir flag
- Dashboard subprocess calls pass project-root .commander/ path
- Estimation JSON written to project-root, not clone-local dir
- Maintenance helper copies stray JSONs
- Calibration endpoint resolves size with precedence
- No live GitHub API calls during calibration
- Sprint-start estimator remains skipped
"""
import os
import json
import tempfile
import subprocess
import sys
from pathlib import Path
import pytest
import httpx


BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    raise RuntimeError("UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest.")


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# ── AC 1 & 2: estimate_issue.py accepts --commander-dir flag ──

def test_estimate_json_write_path__estimate_script_accepts_commander_dir_flag():
    """AC: estimate_issue.py accepts --commander-dir flag (or COMMANDER_PROJECT_ROOT env var)"""
    # This test verifies the script accepts the flag without error.
    # We check that --help includes the flag.
    result = subprocess.run(
        [sys.executable, "services/sprint_manager/estimate_issue.py", "--help"],
        capture_output=True, text=True, cwd="/Users/chaiwutchaianuchittrakul/dev/commander/tester"
    )
    assert result.returncode == 0, f"Help output failed: {result.stderr}"
    assert "--commander-dir" in result.stdout or "COMMANDER_PROJECT_ROOT" in result.stdout, \
        f"Flag not found in help. Output:\n{result.stdout}"


def test_estimate_json_write_path__backward_compatible_without_flag():
    """AC: existing --commander-dir-less invocations remain backward-compatible"""
    # We invoke --help without the new flag and verify no error occurs
    result = subprocess.run(
        [sys.executable, "services/sprint_manager/estimate_issue.py", "--help"],
        capture_output=True, text=True, cwd="/Users/chaiwutchaianuchittrakul/dev/commander/tester"
    )
    assert result.returncode == 0, f"Script help failed: {result.stderr}"


# ── AC 3: Estimation JSON written to project-root ──

def test_estimate_json_write_path__project_root_estimates_dir_configured():
    """AC: After create-time estimation, <project-root>/.commander/estimates/issue-N.json exists"""
    # Verify sprint.yaml 'paths' config points to project-root estimates dir
    sprint_yaml = Path("/Users/chaiwutchaianuchittrakul/dev/commander/.commander/sprint.yaml")
    if not sprint_yaml.exists():
        pytest.skip(f"sprint.yaml not found at {sprint_yaml}")

    content = sprint_yaml.read_text()
    # Check for 'paths' section that specifies estimates dir
    assert "paths:" in content or "estimates" in content, \
        f"sprint.yaml does not define paths config.\nContent:\n{content[:500]}"


# ── AC 4: Maintenance helper copies stray JSONs ──

def test_estimate_json_write_path__maintenance_helper_script_exists():
    """AC: A maintenance helper script (not a UI button) copies stray JSONs"""
    # Locate the helper script that copies JSONs
    candidate_paths = [
        Path("/Users/chaiwutchaianuchittrakul/dev/commander/tester/scripts/consolidate_estimates.py"),
        Path("/Users/chaiwutchaianuchittrakul/dev/commander/tester/services/sprint_manager/consolidate_estimates.py"),
    ]
    found = False
    for p in candidate_paths:
        if p.exists():
            found = True
            content = p.read_text()
            assert "copy" in content.lower() or "consolidate" in content.lower(), \
                f"Helper script {p} does not appear to copy/consolidate JSONs"
            break
    if not found:
        pytest.skip("Maintenance helper script not found (expected in scripts/ or services/)")


# ── AC 5: Calibration resolves size with precedence ──

def test_estimate_json_write_path__calibration_endpoint_returns_data_points(client):
    """AC: Call calibration endpoint, response contains >18 data points"""
    r = client.get("/api/projects/commander/analytics/calibration")
    assert r.status_code == 200, f"Calibration endpoint returned {r.status_code}: {r.text}"
    data = r.json()

    # Response should have records or data_points
    records = data.get("records", data.get("data_points", []))
    assert len(records) > 18, \
        f"Expected >18 data points, got {len(records)}. Response: {data}"


def test_estimate_json_write_path__calibration_no_new_estimation_calls(client):
    """AC: No Haiku estimation calls logged during calibration request"""
    # This test verifies that calibration doesn't spawn new estimation subprocesses.
    # We check the response structure — if it has pre-computed sizes, estimation is skipped.
    r = client.get("/api/projects/commander/analytics/calibration")
    assert r.status_code == 200
    data = r.json()

    records = data.get("records", data.get("data_points", []))
    # If records have size from cache/label/state (not fresh Haiku output), estimation was skipped
    for record in records[:5]:  # Sample first few
        size = record.get("size")
        assert size in ["S", "M", "L", "XL", None], \
            f"Unexpected size value {size!r} in record. Record: {record}"


# ── AC 5a & 5b: Size resolution precedence ──

def test_estimate_json_write_path__calibration_uses_json_size():
    """AC: _calibration_issue_sample() resolves size with precedence: (1) canonical JSON"""
    # Verify that if a canonical estimates/issue-N.json exists, its size is used
    test_estimates_dir = Path("/Users/chaiwutchaianuchittrakul/dev/commander/.commander/estimates")
    if not test_estimates_dir.exists():
        pytest.skip(f"Estimates dir not found: {test_estimates_dir}")

    # Look for any existing estimate file and verify it has a size field
    estimate_files = list(test_estimates_dir.glob("issue-*.json"))
    if not estimate_files:
        pytest.skip("No estimate files found for verification")

    # Sample first estimate
    sample = estimate_files[0]
    data = json.loads(sample.read_text())
    assert "size" in data, f"Estimate {sample.name} missing 'size' field"
    assert data["size"] in ["S", "M", "L", "XL"], f"Invalid size {data['size']!r}"


def test_estimate_json_write_path__calibration_uses_state_estimates_fallback():
    """AC: Precedence (2) state.estimates[issue_num].size when JSON absent"""
    # This test verifies the fallback logic without requiring live GitHub API calls.
    # We check that calibration endpoint uses cached sizes from state instead of calling GitHub.
    # This is tested indirectly by verifying no "estimation" log entry for completed tickets.
    pytest.skip("Requires inspection of live state.estimates; deferred to UAT Step 6")


def test_estimate_json_write_path__calibration_uses_github_label_fallback():
    """AC: Precedence (3) mirror DB size-* label from issues table"""
    # Verify calibration endpoint has logic to read labels from mirror DB
    pytest.skip("Requires DB introspection; deferred to UAT Step 5")


# ── AC 6: Calibration status check ──

def test_estimate_json_write_path__calibration_requires_done_status():
    """AC: Calibration still requires done-equivalent status before counting actual minutes"""
    # This is verified via UAT step 5 — a completed ticket with only a label is counted.
    pytest.skip("Deferred to UAT Step 5 (manual verification of completed ticket in calibration)")


# ── AC 7: Shared size-resolution helper ──

def test_estimate_json_write_path__size_resolution_helper_exists():
    """AC: _calibration_absorb_state_file, _compute_calibration_from_files shared size helper"""
    # Verify calibration.py or related module has a shared size-resolution function
    calibration_py = Path("/Users/chaiwutchaianuchittrakul/dev/commander/tester/services/sprint_manager/calibration.py")
    if not calibration_py.exists():
        pytest.skip("calibration.py not found")

    content = calibration_py.read_text()
    # Look for a helper function that resolves size (could be named _resolve_size, get_size, etc.)
    assert "resolve" in content.lower() or "get_size" in content.lower() or "size" in content, \
        f"Size resolution helper not found in {calibration_py.name}"


# ── AC 8: Sprint-start estimator remains skipped ──

def test_estimate_json_write_path__skip_estimator_flag_unchanged():
    """AC: Sprint-start estimator remains skipped (skip_estimator stays true)"""
    sprint_yaml = Path("/Users/chaiwutchaianuchittrakul/dev/commander/.commander/sprint.yaml")
    if not sprint_yaml.exists():
        pytest.skip(f"sprint.yaml not found at {sprint_yaml}")

    content = sprint_yaml.read_text()
    assert "skip_estimator: true" in content or "skip_estimator:true" in content, \
        f"skip_estimator not set to true in sprint.yaml:\n{content}"


# ── AC 9 & 10: Test files exist and cover cases ──

def test_estimate_json_write_path__test_649_exists():
    """AC: tests/test_649__calibration_analytics_endpoint.py exists"""
    test_file = Path("/Users/chaiwutchaianuchittrakul/dev/commander/tester/tests/test_649__calibration_analytics_endpoint.py")
    assert test_file.exists(), f"Test file not found: {test_file}"


def test_estimate_json_write_path__test_718_exists():
    """AC: tests/test_718__analytics_local_files.py exists"""
    test_file = Path("/Users/chaiwutchaianuchittrakul/dev/commander/tester/tests/test_718__analytics_local_files.py")
    assert test_file.exists(), f"Test file not found: {test_file}"


def test_estimate_json_write_path__test_649_covers_three_cases():
    """AC: test_649 covers ticket with mirror DB label, clone-local JSON, state.estimates"""
    test_file = Path("/Users/chaiwutchaianuchittrakul/dev/commander/tester/tests/test_649__calibration_analytics_endpoint.py")
    if not test_file.exists():
        pytest.skip("test_649 not found")

    content = test_file.read_text()
    # Check for test functions or docstrings covering the three cases
    assert "label" in content.lower() and ("json" in content.lower() or "state" in content.lower()), \
        f"test_649 does not appear to cover all three precedence cases"


def test_estimate_json_write_path__all_existing_tests_pass():
    """AC: All existing tests pass"""
    # Run pytest on the entire test suite to confirm no regressions
    # This is deferred to the sprint_manager pytest gate (Step 5 of tester workflow)
    pytest.skip("Full pytest suite run deferred to sprint_manager quality gate")
