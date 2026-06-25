"""Acceptance tests for issue #837 — commit package-lock.json and use npm ci.

Each test is anchored to a specific AC item. Tests verify the committed file
state and CI workflow definition; they do not run npm or CI live.
"""
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_JSON = REPO_ROOT / "package.json"
PACKAGE_LOCK = REPO_ROOT / "package-lock.json"
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
FRONTEND_BUILD_YML = WORKFLOWS_DIR / "frontend-build.yml"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def lock_data():
    assert PACKAGE_LOCK.exists(), (
        "package-lock.json must exist at the repo root"
    )
    return json.loads(_read(PACKAGE_LOCK))


@pytest.fixture(scope="module")
def ci_text():
    assert FRONTEND_BUILD_YML.exists(), (
        ".github/workflows/frontend-build.yml must exist"
    )
    return _read(FRONTEND_BUILD_YML)


# AC1 — package-lock.json is committed alongside package.json
def test_837__package_lock_exists():
    """AC1: package-lock.json must exist next to package.json."""
    assert PACKAGE_JSON.exists(), (
        "package.json must exist at the repo root"
    )
    assert PACKAGE_LOCK.exists(), (
        "package-lock.json must be committed at the repo root"
    )
    assert PACKAGE_LOCK.stat().st_size > 0, (
        "package-lock.json must be non-empty"
    )


# AC2 — package-lock.json reflects the declared devDependencies
def test_837__package_lock_contains_esbuild(lock_data):
    """AC2: package-lock.json must pin esbuild (satisfies ^0.20.0)."""
    packages = lock_data.get("packages", {})
    esbuild_key = "node_modules/esbuild"
    assert esbuild_key in packages, (
        "package-lock.json must contain node_modules/esbuild"
    )
    version = packages[esbuild_key].get("version", "")
    # Must be 0.20.x or higher minor in the 0.x range — satisfies ^0.20.0
    assert re.match(r"^0\.\d+\.\d+$", version), (
        f"esbuild version '{version}' must be in the 0.x.y range"
    )
    major, minor, _ = (int(x) for x in version.split("."))
    assert (major, minor) >= (0, 20), (
        f"esbuild version {version} must satisfy ^0.20.0 (>= 0.20.0)"
    )


def test_837__package_lock_contains_eslint(lock_data):
    """AC2: package-lock.json must pin eslint (satisfies ^9.0.0)."""
    packages = lock_data.get("packages", {})
    eslint_key = "node_modules/eslint"
    assert eslint_key in packages, (
        "package-lock.json must contain node_modules/eslint"
    )
    version = packages[eslint_key].get("version", "")
    assert re.match(r"^\d+\.\d+\.\d+", version), (
        f"eslint version '{version}' must be a semver string"
    )
    major = int(version.split(".")[0])
    assert major >= 9, (
        f"eslint version {version} must satisfy ^9.0.0 (major >= 9)"
    )


def test_837__package_lock_lockfile_version(lock_data):
    """AC2 (structural): lockfile must have a valid version (1-3)."""
    lv = lock_data.get("lockfileVersion")
    assert lv in (1, 2, 3), (
        f"lockfileVersion must be 1, 2, or 3; got {lv!r}"
    )


# AC3 — frontend-build.yml uses npm ci instead of npm install
def test_837__ci_uses_npm_ci(ci_text):
    """AC3: the install step must run 'npm ci', not 'npm install'."""
    assert "npm ci" in ci_text, (
        "frontend-build.yml must use 'npm ci' for the install step"
    )


def test_837__ci_no_npm_install(ci_text):
    """AC3 (negative): 'npm install' must not appear in the install step."""
    # Allow 'npm install' only if it is clearly a comment (starts with #).
    # A bare `run: npm install` line should be gone.
    for line in ci_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert "npm install" not in stripped, (
            f"frontend-build.yml must not use 'npm install'; "
            f"found: {line!r}"
        )


# AC4 — paths filter retains package-lock.json and it now exists
def test_837__ci_paths_filter_includes_package_lock(ci_text):
    """AC4: paths trigger must include package-lock.json."""
    assert "package-lock.json" in ci_text, (
        "frontend-build.yml paths filter must include 'package-lock.json'"
    )


def test_837__package_lock_makes_paths_filter_meaningful():
    """AC4: package-lock.json must exist so the paths filter is real."""
    assert PACKAGE_LOCK.exists(), (
        "package-lock.json must be present in the repo "
        "so the paths filter is meaningful"
    )
