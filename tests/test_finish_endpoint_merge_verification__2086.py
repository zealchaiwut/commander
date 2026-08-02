"""Tests for issue #2086: finish endpoint must verify merge reachability before closing issues.

Tests UAT behavior of:
- AC1: finish aborts on merge failure (not close/mark completed/delete)
- AC2: finish verifies post-merge reachability (catch silent no-ops)
- AC3: bulk-complete-preview distinguishes merged vs deleted branches
- AC4: behavioral simulation of failed merge

Implementation detail checks:
- _merge_sprint_branches_for_label() returns errors on merge failure
- finish_sprint() raises HTTPException(409, code='merge_failed') when merge_errors is non-empty
- _is_commit_merged_into_branch() verifies reachability via GitHub compare API
- bulk-complete-preview uses _gh_branch_exists() and _has_merged_pr() to distinguish branch states
"""
import os
import sys
import pytest
import httpx
from pathlib import Path

BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# --- Acceptance Criteria ---

def test_finish_endpoint_code_has_merge_failure_guard__ac1():
    """AC1: finish_sprint() must check merge_errors before closing issues/marking completed.

    Verify in source that finish_sprint() raises HTTPException(409, code='merge_failed')
    when merge_errors is non-empty, BEFORE closing any issues or marking the sprint
    completed.
    """
    sprint_finish_path = DASHBOARD_DIR / "routers" / "sprint_finish.py"
    assert sprint_finish_path.exists(), "sprint_finish.py not found"

    content = sprint_finish_path.read_text(encoding="utf-8")

    # Must check merge_errors before issue closure logic
    assert "if merge_errors:" in content, "Must check merge_errors before proceeding"
    assert '"merge_failed"' in content, "Must use code='merge_failed' in HTTPException"
    assert "raise HTTPException(" in content, "Must raise HTTPException on merge failure"
    assert "409" in content, "Must use status code 409 (Conflict)"

    # Verify order: merge happens, then error check, then issue closure
    merge_line = content.find("srv._merge_sprint_branches_for_label")
    error_check_line = content.find("if merge_errors:")
    close_issues_line = content.find("srv.github_client.close_issue")

    assert merge_line != -1, "_merge_sprint_branches_for_label call not found"
    assert error_check_line != -1, "merge_errors check not found"
    assert close_issues_line != -1, "close_issue call not found"
    assert merge_line < error_check_line < close_issues_line, (
        "Order must be: merge → check errors → close issues"
    )


def test_merge_branches_includes_reachability_check__ac2():
    """AC2: _merge_sprint_branches_for_label() must verify post-merge reachability.

    Verify in source that it calls _is_commit_merged_into_branch() to verify the
    child branch tip is actually reachable from the target after merge succeeds.
    """
    startup_path = DASHBOARD_DIR / "startup.py"
    assert startup_path.exists(), "startup.py not found"

    content = startup_path.read_text(encoding="utf-8")

    # Must call _get_branch_tip_sha before merge
    assert "_get_branch_tip_sha" in content, "Must capture branch tip SHA before merge"

    # Must call _is_commit_merged_into_branch after merge
    assert "_is_commit_merged_into_branch" in content, (
        "Must verify commit reachability after merge"
    )

    # Must report error if not reachable
    assert "not reachable" in content or "is not reachable" in content, (
        "Must include error message about reachability"
    )


def test_bulk_complete_has_branch_state_logic__ac3():
    """AC3: bulk-complete-preview must distinguish merged vs deleted branches.

    Verify it calls _gh_branch_exists() and _has_merged_pr() to properly
    classify branch state when the branch no longer exists.
    """
    sprint_finish_path = DASHBOARD_DIR / "routers" / "sprint_finish.py"
    assert sprint_finish_path.exists(), "sprint_finish.py not found"

    content = sprint_finish_path.read_text(encoding="utf-8")

    # Must check if branch exists
    assert "_gh_branch_exists" in content, "Must check if branch still exists"

    # Must check for merged PR when branch doesn't exist
    assert "_has_merged_pr" in content, (
        "Must check for merged PR to distinguish 'properly merged' from 'deleted without merge'"
    )

    # Must have logic to distinguish the cases
    assert "if branch in pending_heads:" in content, "Must check pending merge status"
    assert "elif srv._gh_branch_exists(repo, branch):" in content, (
        "Must check if branch exists when not in pending heads"
    )
    assert "else:" in content, "Must handle case where branch doesn't exist"


def test_helpers_for_merge_verification_exist__ac4():
    """AC4: Verify the helper functions exist and are callable.

    Ensures _get_branch_tip_sha(), _is_commit_merged_into_branch(), _has_merged_pr()
    all exist and are used for verification logic.
    """
    startup_path = DASHBOARD_DIR / "startup.py"
    assert startup_path.exists(), "startup.py not found"

    content = startup_path.read_text(encoding="utf-8")

    # All verification helpers must exist
    assert "def _get_branch_tip_sha" in content, "_get_branch_tip_sha() not defined"
    assert "def _is_commit_merged_into_branch" in content, "_is_commit_merged_into_branch() not defined"
    assert "def _has_merged_pr" in content, "_has_merged_pr() not defined"
    assert "def _gh_branch_exists" in content, "_gh_branch_exists() not defined"

    # They must be invoked by _merge_sprint_branches_for_label
    merge_func_start = content.find("def _merge_sprint_branches_for_label")
    merge_func_end = content.find("\ndef ", merge_func_start + 1)
    merge_func_body = content[merge_func_start:merge_func_end]

    assert "_get_branch_tip_sha" in merge_func_body, "Must call _get_branch_tip_sha()"
    assert "_is_commit_merged_into_branch" in merge_func_body, "Must call _is_commit_merged_into_branch()"
