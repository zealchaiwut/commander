"""Tests for issue #1544 — Restore lint refactors isolated from board-readiness feature work.

AC coverage:
  AC1  — smgmtPlanningReorder signature uses `label` param (not `_label`)
  AC2  — The _smgmtNextUpLabel assignment uses `let` declaration form
  AC3  — Board readiness feature diff has no unrelated lint-only changes
           (verified by checking _label does NOT appear in any exported function signature)
  AC4  — Rendering behavior is functionally identical — the `label` param is used
           inside the onclick handler that calls smgmtPlanningReorder
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
BOARD_JS = (
    REPO_ROOT
    / "apps"
    / "dashboard"
    / "static"
    / "src"
    / "sprint-board"
    / "board-render.js"
).read_text(encoding="utf-8")


def test_ac1_planning_reorder_uses_label_param():
    """AC1: smgmtPlanningReorder must declare `label` not `_label` as its second param."""
    match = re.search(
        r"export\s+function\s+smgmtPlanningReorder\s*\(\s*\w+\s*,\s*(\w+)\s*\)",
        BOARD_JS,
    )
    assert match is not None, "smgmtPlanningReorder export function not found"
    param_name = match.group(1)
    assert param_name == "label", (
        f"Expected second param to be 'label', got '{param_name}'. "
        "The lint rename to '_label' must be reverted."
    )


def test_ac1_no_label_underscore_in_signature():
    """AC1: The underscore-prefixed `_label` must NOT appear in smgmtPlanningReorder's signature."""
    assert "smgmtPlanningReorder(issueNum, _label)" not in BOARD_JS, (
        "Found `_label` in smgmtPlanningReorder signature — this lint rename "
        "must be reverted to `label`."
    )


def test_ac2_next_up_label_uses_let_declaration():
    """AC2: The NEXT UP block must use `let _smgmtNextUpLabel = null` not a bare assignment."""
    assert "let _smgmtNextUpLabel = null;" in BOARD_JS, (
        "The `let _smgmtNextUpLabel = null;` declaration is missing. "
        "The lint refactor that dropped `let` must be reverted — the NEXT UP block "
        "must declare a local variable, not assign to the module global."
    )


def test_ac2_no_bare_next_up_assignment():
    """AC2: Bare global assignment `_smgmtNextUpLabel = null` must not appear (only `let` form)."""
    # Allow the `let` form; reject the standalone bare assignment
    lines = BOARD_JS.splitlines()
    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped == "_smgmtNextUpLabel = null;":
            raise AssertionError(
                f"Line {lineno}: bare global assignment `_smgmtNextUpLabel = null;` found. "
                "This must be `let _smgmtNextUpLabel = null;`."
            )


def test_ac3_onclick_passes_label_variable():
    """AC3/AC4: The onclick handler that calls smgmtPlanningReorder passes the `label` variable."""
    assert "smgmtPlanningReorder(${issueNum},'${escHtml(label)}')" in BOARD_JS, (
        "The onclick handler must pass `label` to smgmtPlanningReorder. "
        "This confirms the feature diff is internally consistent."
    )


def test_ac4_planning_reorder_body_uses_issue_num():
    """AC4: smgmtPlanningReorder body references issueNum — confirm function is not broken."""
    # Extract the function body (everything between its opening and closing braces)
    fn_match = re.search(
        r"export\s+function\s+smgmtPlanningReorder\s*\([^)]*\)\s*\{",
        BOARD_JS,
    )
    assert fn_match is not None, "smgmtPlanningReorder not found"
    start = fn_match.end()
    # Walk braces to find the function end
    depth = 1
    pos = start
    while pos < len(BOARD_JS) and depth > 0:
        if BOARD_JS[pos] == "{":
            depth += 1
        elif BOARD_JS[pos] == "}":
            depth -= 1
        pos += 1
    body = BOARD_JS[start:pos]
    assert "issueNum" in body, (
        "smgmtPlanningReorder body must reference issueNum — function appears empty or broken."
    )
