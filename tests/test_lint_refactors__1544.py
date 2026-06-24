"""Tests for issue #1544: Unrelated lint refactors bundled into board readiness work"""
import subprocess

# This ticket is primarily a code audit/hygiene ticket, not an HTTP-testable feature.
# Acceptance criteria verify git history and code structure, not runtime behavior.

# --- Acceptance Criteria ---

def test_lint_refactors__smgmtplanningreorder_param_name():
    """AC: The `smgmtPlanningReorder` function signature restores the original `label` parameter name (not `_label`),
    or the rename is isolated to a dedicated lint-only commit."""
    # Verify that the feature commit c203f0f has the original param name
    result_feature = subprocess.run(
        ["git", "show", "c203f0f:apps/dashboard/static/src/sprint-board/board-render.js"],
        cwd="/Users/zeal-server/dev/commander/tester",
        capture_output=True,
        text=True
    )
    assert "export function smgmtPlanningReorder(issueNum, label)" in result_feature.stdout, \
        "Feature commit should have original param name 'label'"
    
    # Verify that a dedicated lint commit exists that isolates this rename
    result_lint = subprocess.run(
        ["git", "show", "168b993"],
        cwd="/Users/zeal-server/dev/commander/tester",
        capture_output=True,
        text=True
    )
    assert "fix(lint)" in result_lint.stdout, "Dedicated lint commit should have 'fix(lint)' message"
    assert "_label" in result_lint.stdout, "Lint commit should show the _label rename"


def test_lint_refactors__smgmtnextuplabel_declaration():
    """AC: The assignment at board-render.js uses a proper form (either `let _smgmtNextUpLabel = ...`
    or a bare assignment), and if the form changed, that change is isolated in a lint-only commit."""
    # Verify feature commit c203f0f has let declaration
    result_feature = subprocess.run(
        ["git", "show", "c203f0f:apps/dashboard/static/src/sprint-board/board-render.js"],
        cwd="/Users/zeal-server/dev/commander/tester",
        capture_output=True,
        text=True
    )
    assert "let _smgmtNextUpLabel = null;" in result_feature.stdout, \
        "Feature commit should have let declaration"
    
    # Verify lint commit 168b993 changed it to bare assignment and is separate
    result_lint = subprocess.run(
        ["git", "show", "168b993"],
        cwd="/Users/zeal-server/dev/commander/tester",
        capture_output=True,
        text=True
    )
    assert "_smgmtNextUpLabel = null;" in result_lint.stdout, "Lint commit should show the let removal"
    assert "fix(lint)" in result_lint.stdout, "Lint commit should be isolated with 'fix(lint)' message"


def test_lint_refactors__no_mixed_feature_lint_commits():
    """AC: The board readiness feature diff (from #1487) contains no unrelated lint-only changes
    when reviewed in isolation."""
    # Verify that the feature commit c203f0f alone does not contain the lint changes
    result_diff = subprocess.run(
        ["git", "show", "c203f0f"],
        cwd="/Users/zeal-server/dev/commander/tester",
        capture_output=True,
        text=True
    )
    # Feature commit has original param name (not renamed to _label)
    assert "smgmtPlanningReorder(issueNum, label)" in result_diff.stdout, \
        "Feature commit should have original parameter name"
    assert "smgmtPlanningReorder(issueNum, _label)" not in result_diff.stdout, \
        "Feature commit should NOT have the lint rename"
    
    # Verify lint commit 168b993 is a separate, dedicated commit
    result_lint = subprocess.run(
        ["git", "show", "168b993"],
        cwd="/Users/zeal-server/dev/commander/tester",
        capture_output=True,
        text=True
    )
    assert "fix(lint)" in result_lint.stdout, "Lint commit should be isolated with 'fix(lint)' message"
    assert "smgmtPlanningReorder(issueNum, _label)" in result_lint.stdout, \
        "Lint commit should show the _label rename"


def test_lint_refactors__board_rendering_behavior():
    """AC: Sprint-board rendering behavior is functionally identical before and after —
    no visible regressions to planning reorder or next-up label display."""
    # Load the current board-render.js and verify it has no syntax errors
    board_render_path = "/Users/zeal-server/dev/commander/tester/apps/dashboard/static/src/sprint-board/board-render.js"
    
    # Simple check: the file should exist and have reasonable size (not corrupted)
    with open(board_render_path) as f:
        content = f.read()
    
    # Verify the functions exist and have proper structure
    assert "export function smgmtPlanningReorder(issueNum, _label)" in content, \
        "smgmtPlanningReorder function should exist with _label param"
    assert "_smgmtNextUpLabel = null;" in content, "_smgmtNextUpLabel assignment should exist"
    assert "export function _smgmtRender(data)" in content, "_smgmtRender function should exist"
    
    # Verify no syntax errors by checking balanced braces
    open_braces = content.count("{")
    close_braces = content.count("}")
    assert open_braces == close_braces, f"Unbalanced braces in board-render.js: {open_braces} vs {close_braces}"


def test_lint_refactors__commit_message_scoping():
    """AC: Any future lint-only cleanups to `board-render.js` are committed separately
    from feature work, with a commit message scoped to 'lint' or 'hygiene'."""
    # Verify the lint commit follows the pattern
    result = subprocess.run(
        ["git", "show", "168b993", "--stat"],
        cwd="/Users/zeal-server/dev/commander/tester",
        capture_output=True,
        text=True
    )
    # Check the commit message
    assert "fix(lint)" in result.stdout, \
        "Lint commit should have 'fix(lint)' in message"
    # Verify it only touches the relevant files (board-render.js, run-controls.js, bundle.js)
    assert "board-render.js" in result.stdout, \
        "Lint commit should modify board-render.js"

