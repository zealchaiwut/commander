"""
Tests for issue #1770: Delete orphaned and broken build_frontend_bundle.py
"""

import subprocess
import os
from pathlib import Path


def test_build_frontend_bundle_script_deleted():
    """AC1: scripts/build_frontend_bundle.py is deleted from the repository"""
    script_path = Path("scripts/build_frontend_bundle.py")
    assert not script_path.exists(), "scripts/build_frontend_bundle.py should be deleted"


def test_no_references_to_build_frontend_bundle():
    """AC2: grep -r 'build_frontend_bundle' . returns zero results outside test/docs context"""
    result = subprocess.run(
        ["grep", "-r", "build_frontend_bundle", ".",
         "--exclude-dir=.git", "--exclude-dir=.pytest_cache", "--exclude-dir=.code-review-graph",
         "--exclude=*.pyc", "--exclude=__pycache__"],
        capture_output=True,
        text=True,
    )
    # Filter out references that are in documentation or test context
    lines = result.stdout.strip().split("\n") if result.stdout.strip() else []
    real_refs = [
        line for line in lines
        if line and not line.startswith("./tests/") and not line.startswith("./docs/")
    ]
    assert len(real_refs) == 0, f"Found unexpected references to build_frontend_bundle: {real_refs}"


def test_npm_build_succeeds():
    """AC3: npm run build exits with code 0 after the deletion"""
    result = subprocess.run(
        ["npm", "run", "build"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"npm run build should exit with code 0, got {result.returncode}\nstderr: {result.stderr}"

    # Verify the bundle was created
    bundle_path = Path("apps/dashboard/static/dist/bundle.js")
    assert bundle_path.exists(), "apps/dashboard/static/dist/bundle.js should exist after npm build"
