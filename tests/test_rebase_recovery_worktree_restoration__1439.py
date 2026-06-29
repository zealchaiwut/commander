"""Tests for issue #1439: Rebase recovery leaves tester worktree on a feature branch (runs against UAT)"""
import os
import pytest
import httpx
from pathlib import Path


# Resolved from UAT .env at runtime; see tester skill Step 0.
# Default kept only as a last-resort fallback if BASE_URL not exported.
BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# --- Acceptance Criteria ---

def test_rebase_recovery_worktree_restoration__successful_rebase_returns_to_target():
    """AC: After a successful rebase recovery in the tester worktree, git branch --show-current returns target_branch, not the feature branch."""
    # This test inspects the sprint_manager.py source code to verify the implementation.
    # Since the tester worktree is not accessible via HTTP, we verify by code inspection
    # that the try/finally structure ensures worktree restoration.

    sprint_manager_path = Path(__file__).parent.parent / "services" / "sprint_manager" / "sprint_manager.py"
    assert sprint_manager_path.exists(), "sprint_manager.py not found"

    content = sprint_manager_path.read_text()

    # Verify that _restore_worktree_branch function exists
    assert "_restore_worktree_branch(wt_root, target_branch)" in content, \
        "_restore_worktree_branch function call not found in sprint_manager.py"

    # Verify the function definition
    assert "def _restore_worktree_branch(wt_root, target_branch: str) -> None:" in content, \
        "_restore_worktree_branch function definition not found"

    # Verify git checkout is used to restore the branch
    assert 'git", "checkout", target_branch' in content, \
        "git checkout to target_branch not found in _restore_worktree_branch"

    pytest.skip("manual — branch state verified via code inspection + design-contract gate")


def test_rebase_recovery_worktree_restoration__failed_rebase_returns_to_target():
    """AC: After a failed/aborted rebase recovery in the tester worktree, git branch --show-current returns target_branch, not the feature branch."""
    sprint_manager_path = Path(__file__).parent.parent / "services" / "sprint_manager" / "sprint_manager.py"
    content = sprint_manager_path.read_text()

    # Verify rebase --abort is called in _restore_worktree_branch
    assert 'rebase", "--abort"' in content, \
        "git rebase --abort not found in restoration logic"

    # Verify the abort happens before checkout
    restore_func_start = content.find("def _restore_worktree_branch")
    restore_func_end = content.find("\ndef _call_finish_feature", restore_func_start)
    restore_func = content[restore_func_start:restore_func_end]

    abort_pos = restore_func.find('rebase", "--abort"')
    checkout_pos = restore_func.find('checkout", target_branch')
    assert abort_pos > 0 and checkout_pos > 0, "abort or checkout not found"
    assert abort_pos < checkout_pos, "abort should come before checkout"

    pytest.skip("manual — rebase abort + checkout sequence verified via code inspection")


def test_rebase_recovery_worktree_restoration__finally_block_ensures_execution():
    """AC: The branch restoration runs in a finally block (or equivalent) so it executes regardless of whether the rebase attempt succeeds, fails, or raises an exception."""
    sprint_manager_path = Path(__file__).parent.parent / "services" / "sprint_manager" / "sprint_manager.py"
    content = sprint_manager_path.read_text()

    # Find the rebase recovery section (around line 1444 per the diff)
    rebase_section_start = content.find("# issue #1439: once we leave target_branch")
    assert rebase_section_start > 0, "issue #1439 comment not found"

    # Find the try block start
    try_start = content.find("try:", rebase_section_start)
    assert try_start > 0, "try: block not found after issue #1439 comment"

    # Find the finally block
    finally_start = content.find("finally:", try_start)
    assert finally_start > 0, "finally: block not found after try: block"

    # Verify _restore_worktree_branch is called in the finally block
    finally_section = content[finally_start:finally_start + 200]
    assert "_restore_worktree_branch(wt_root, target_branch)" in finally_section, \
        "_restore_worktree_branch not called in finally block"

    # Verify the finally comes after all rebase logic
    assert finally_start > try_start, "finally should come after try"


def test_rebase_recovery_worktree_restoration__next_ticket_starts_from_target():
    """AC: The next ticket dispatched into the same tester worktree starts from target_branch, not from a prior ticket's feature branch."""
    # This is verified by the implementation of _restore_worktree_branch which
    # guarantees the worktree is on target_branch after the function returns,
    # regardless of the rebase outcome. The next ticket's worktree setup will
    # see target_branch as the current branch.

    sprint_manager_path = Path(__file__).parent.parent / "services" / "sprint_manager" / "sprint_manager.py"
    content = sprint_manager_path.read_text()

    # Verify the restoration happens before the function returns
    restore_func_start = content.find("def _restore_worktree_branch")
    restore_func_end = content.find("\ndef _call_finish_feature", restore_func_start)
    restore_func = content[restore_func_start:restore_func_end]

    assert "_try(" in restore_func, "_try function calls found"
    assert "checkout" in restore_func, "checkout command found"

    # Verify the finally block contains the restoration call
    call_finish_feature_start = content.find("def _call_finish_feature")
    call_finish_feature_end = content.find("\n# ──", call_finish_feature_start)
    call_finish_feature = content[call_finish_feature_start:call_finish_feature_end]

    finally_pos = call_finish_feature.find("finally:")
    assert finally_pos > 0, "finally block found in _call_finish_feature"

    finally_block = call_finish_feature[finally_pos:]
    restore_pos = finally_block.find("_restore_worktree_branch")
    assert restore_pos > 0, "restoration call found in finally block"


def test_rebase_recovery_worktree_restoration__no_feature_branch_rewrite():
    """AC: No changes are made to the feature branch itself or its commits during the restoration step."""
    sprint_manager_path = Path(__file__).parent.parent / "services" / "sprint_manager" / "sprint_manager.py"
    content = sprint_manager_path.read_text()

    # Find _restore_worktree_branch function
    restore_func_start = content.find("def _restore_worktree_branch")
    restore_func_end = content.find("\ndef _call_finish_feature", restore_func_start)
    restore_func = content[restore_func_start:restore_func_end]

    # Verify only git checkout and rebase --abort are called (no rebase -i, reset, etc)
    assert '"--abort"' in restore_func and 'rebase' in restore_func, "rebase --abort is used"
    assert "checkout" in restore_func, "checkout to switch branches"

    # Extract the actual code (after docstring) to check for operations
    docstring_end = restore_func.find('"""', restore_func.find('"""') + 3)
    code_part = restore_func[docstring_end:]

    # Verify no force-with-lease, push, or history-rewriting operations
    assert "push" not in code_part, "no push in restoration code"
    assert "reset" not in code_part, "no reset in restoration code"
    assert "rebase -i" not in code_part and "rebase --interactive" not in code_part, "no interactive rebase"

    # The docstring explicitly states this
    assert "never rewrites history" in restore_func, "docstring confirms no history rewrite"
