"""Tests for issue #837: Commit package-lock.json and use npm ci in frontend CI"""
import os
import pytest
import httpx
import json
import subprocess
from pathlib import Path


BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_ROOT = REPO_ROOT / "apps" / "dashboard"


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# --- Acceptance Criteria ---

def test_commit_package_lock_npm_ci__lockfile_exists():
    """AC1: package-lock.json file is committed to the repository"""
    lockfile = REPO_ROOT / "package-lock.json"
    assert lockfile.exists(), f"package-lock.json not found at {lockfile}"
    assert lockfile.stat().st_size > 0, "package-lock.json is empty"


def test_commit_package_lock_npm_ci__lockfile_reflects_dependencies():
    """AC2: package-lock.json reflects exact dependency versions from package.json"""
    package_json = REPO_ROOT / "package.json"
    lockfile = REPO_ROOT / "package-lock.json"

    with open(package_json) as f:
        pkg = json.load(f)
    with open(lockfile) as f:
        lock = json.load(f)

    # Check that lockfile is not empty and has packages
    assert "packages" in lock, "lockfile missing 'packages' key"
    assert len(lock["packages"]) > 0, "lockfile has no packages"

    # Verify that the root package in lockfile matches our package.json version
    if "" in lock["packages"]:
        root_pkg = lock["packages"][""]
        assert root_pkg.get("version") == pkg.get("version"), \
            f"Root package version mismatch: {root_pkg.get('version')} vs {pkg.get('version')}"


def test_commit_package_lock_npm_ci__workflow_uses_npm_ci():
    """AC3: .github/workflows/frontend-build.yml uses npm ci instead of npm install"""
    workflow_file = REPO_ROOT / ".github" / "workflows" / "frontend-build.yml"
    assert workflow_file.exists(), f"Workflow file not found at {workflow_file}"

    with open(workflow_file) as f:
        content = f.read()

    # Verify npm ci is present in the workflow
    assert "npm ci" in content, "npm ci not found in frontend-build.yml"
    # Verify it's in the Install dependencies step (not just mentioned elsewhere)
    assert "Install dependencies" in content, "Install dependencies step not found"
    lines = content.split('\n')
    install_idx = None
    for i, line in enumerate(lines):
        if "Install dependencies" in line:
            install_idx = i
            break
    assert install_idx is not None, "Install dependencies step not found"
    # Check that npm ci appears within 2-3 lines after the Install dependencies label
    found_npm_ci = False
    for j in range(install_idx, min(install_idx + 3, len(lines))):
        if "npm ci" in lines[j]:
            found_npm_ci = True
            break
    assert found_npm_ci, "npm ci not found in Install dependencies step"


def test_commit_package_lock_npm_ci__paths_filter_includes_lockfile():
    """AC4: paths filter includes package-lock.json and file exists"""
    workflow_file = REPO_ROOT / ".github" / "workflows" / "frontend-build.yml"

    with open(workflow_file) as f:
        content = f.read()

    # Check paths filter includes package-lock.json
    assert "package-lock.json" in content, "package-lock.json not in paths filter"
    # And the file itself exists (verified in AC1, but double-check)
    lockfile = REPO_ROOT / "package-lock.json"
    assert lockfile.exists(), "package-lock.json file does not exist"


def test_commit_package_lock_npm_ci__npm_ci_installs_deterministically():
    """AC5: npm ci installs deterministically with the committed lockfile"""
    # Test that npm ci can successfully read and use the lockfile
    # by running it in a temporary directory with only package.json and package-lock.json
    import tempfile
    import shutil

    package_json = REPO_ROOT / "package.json"
    lockfile = REPO_ROOT / "package-lock.json"

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        shutil.copy(package_json, tmp_path / "package.json")
        shutil.copy(lockfile, tmp_path / "package-lock.json")

        # Run npm ci in the temp directory
        result = subprocess.run(
            ["npm", "ci"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=60
        )

        assert result.returncode == 0, f"npm ci failed: {result.stderr}"
        # Verify no new entries were added to package-lock.json
        with open(tmp_path / "package-lock.json") as f:
            new_lock = json.load(f)

        with open(lockfile) as f:
            orig_lock = json.load(f)

        # The new lockfile should be identical (same packages count)
        assert len(new_lock.get("packages", {})) == len(orig_lock.get("packages", {})), \
            "npm ci modified the lockfile (new packages were added)"
