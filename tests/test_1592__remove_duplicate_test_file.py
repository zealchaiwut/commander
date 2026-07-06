"""Tests for issue #1592: Remove duplicate test file for #1200 design-token consolidation.

This issue verifies that the duplicate test file test_consolidate_design_tokens__1200.py
has been removed and consolidation into test_1200__consolidate_design_tokens.py is complete.

Acceptance Criteria:
  AC1: tests/test_consolidate_design_tokens__1200.py is deleted from the repository.
  AC2: tests/test_1200__consolidate_design_tokens.py is retained and contains all test cases.
  AC3: The surviving file follows the test_<issue>__<slug>.py naming convention.
  AC4: All tests in test_1200__consolidate_design_tokens.py pass after consolidation.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
TESTS_DIR = REPO_ROOT / "tests"


def test_ac1_duplicate_file_deleted():
    """AC1: tests/test_consolidate_design_tokens__1200.py must not exist."""
    old_file = TESTS_DIR / "test_consolidate_design_tokens__1200.py"
    assert not old_file.exists(), (
        f"Duplicate test file {old_file} still exists — "
        "it must be deleted per AC1"
    )


def test_ac2_consolidated_file_exists():
    """AC2: tests/test_1200__consolidate_design_tokens.py must be present."""
    consolidated_file = TESTS_DIR / "test_1200__consolidate_design_tokens.py"
    assert consolidated_file.exists(), (
        f"Consolidated test file {consolidated_file} does not exist — "
        "the surviving file must be retained per AC2"
    )


def test_ac2_consolidated_file_has_all_tests():
    """AC2: Consolidated file must contain all original test functions."""
    consolidated_file = TESTS_DIR / "test_1200__consolidate_design_tokens.py"
    content = consolidated_file.read_text(encoding="utf-8")

    # Expected test functions from both original files
    required_tests = [
        "test_ac1_no_token_vars_in_project_html_root",
        "test_ac1_sidebar_width_retained",
        "test_ac2_no_dark_mode_token_overrides_inline",
        "test_ac2_no_inline_dark_block_at_all",
        "test_ac3_zero_overlap_confirmed",
        "test_ac4_tokens_css_link_present",
        "test_ac4_link_before_inline_styles",
        "test_ac5_dark_mode_block_in_tokens_css",
    ]

    missing_tests = []
    for test_name in required_tests:
        if f"def {test_name}" not in content:
            missing_tests.append(test_name)

    assert not missing_tests, (
        f"Consolidated file missing {len(missing_tests)} test function(s): {missing_tests} — "
        "no test coverage should be lost in the consolidation per AC2"
    )


def test_ac3_naming_convention():
    """AC3: Surviving file follows test_<issue>__<slug>.py naming convention."""
    consolidated_file = TESTS_DIR / "test_1200__consolidate_design_tokens.py"
    assert consolidated_file.exists(), "Consolidated file must exist"

    filename = consolidated_file.name
    # Expected: test_<issue>__<slug>.py where <issue> is 1200 and <slug> is consolidate_design_tokens
    assert filename.startswith("test_"), f"File {filename} should start with 'test_'"
    assert "__" in filename, f"File {filename} should contain '__' separator"
    assert filename.endswith(".py"), f"File {filename} should end with '.py'"

    # More specific: should be test_1200__consolidate_design_tokens.py
    assert filename == "test_1200__consolidate_design_tokens.py", (
        f"File should be named 'test_1200__consolidate_design_tokens.py' per convention, "
        f"not '{filename}'"
    )


def test_ac4_consolidated_tests_pass():
    """AC4: All tests in consolidated file must pass."""
    consolidated_file = TESTS_DIR / "test_1200__consolidate_design_tokens.py"
    assert consolidated_file.exists(), "Consolidated file must exist"

    # Set up environment for pytest
    env = os.environ.copy()
    # Ensure we're testing against UAT if available, otherwise local
    base_url = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "8001")
    env["UAT_BASE_URL"] = base_url
    env["UAT_PORT"] = os.environ.get("UAT_PORT", "8001")

    # Run pytest on the consolidated file
    result = subprocess.run(
        ["python", "-m", "pytest", str(consolidated_file), "-v", "--tb=short"],
        cwd=str(REPO_ROOT / "apps" / "dashboard"),
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )

    assert result.returncode == 0, (
        f"Tests in {consolidated_file.name} failed with exit code {result.returncode}\n"
        f"STDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )


def test_ac4_only_one_test_file_for_1200():
    """AC4 verification: Ensure exactly one test file for issue 1200 exists."""
    # List all files matching patterns for 1200
    test_files_1200 = list(TESTS_DIR.glob("*1200*"))
    # Filter out pycache
    test_files_1200 = [f for f in test_files_1200 if f.is_file() and f.suffix == ".py"]

    assert len(test_files_1200) == 1, (
        f"Expected exactly 1 test file for issue 1200, found {len(test_files_1200)}: "
        f"{[f.name for f in test_files_1200]} — "
        "the duplicate must be deleted and only the consolidated file should remain"
    )

    assert test_files_1200[0].name == "test_1200__consolidate_design_tokens.py", (
        f"The sole test file for 1200 should be 'test_1200__consolidate_design_tokens.py', "
        f"not '{test_files_1200[0].name}'"
    )
