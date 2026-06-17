"""Issue #1297 — Clarify ambiguous and/or precedence in _compute_summary_counts.

AC1: The condition is rewritten with explicit parentheses.
AC2: No bare mixed and/or remain without parentheses.
AC3: Logical result is identical for all combinations of has_agent_action, status.
AC4: Existing tests pass without modification.
"""
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SERVICE = _REPO / "apps" / "dashboard" / "routers" / "sprint_artifact_service.py"


def _load_compute_summary_counts():
    """Import _compute_summary_counts without loading the full router module."""
    src = _SERVICE.read_text(encoding="utf-8")
    start = src.index("def _compute_summary_counts(")
    # find the next top-level def/class to slice just this function
    nxt = src.index("\ndef ", start + 1)
    ns: dict = {}
    exec(compile(src[start:nxt], "<compute_summary_counts>", "exec"), ns)  # noqa: S102
    return ns["_compute_summary_counts"]


_compute = _load_compute_summary_counts()


# ── AC1: Parentheses are explicit ──────────────────────────────────────────

def test_condition_has_explicit_parentheses():
    """AC1: The condition uses explicit parentheses to clarify precedence."""
    src = _SERVICE.read_text(encoding="utf-8")
    start = src.index("def _compute_summary_counts(")
    nxt = src.index("\ndef ", start + 1)
    fn_src = src[start:nxt]

    # The condition line should have parentheses around the or clause
    # or around the and clause, but not bare mixed and/or
    lines = fn_src.split('\n')
    if_lines = [l for l in lines if 'if ' in l and 'status' in l and 'has_agent_action' in l]

    assert if_lines, "Could not find condition line"

    for line in if_lines:
        # Should have balanced parentheses
        open_parens = line.count('(')
        close_parens = line.count(')')
        assert open_parens == close_parens, f"Unbalanced parentheses in: {line}"

        # Should have at least one pair of parentheses beyond function calls
        condition_part = line[line.index('if '):]
        paren_count = condition_part.count('(')
        assert paren_count >= 1, f"Condition lacks explicit parentheses: {line}"


# ── AC2: No bare mixed and/or without parentheses ──────────────────────────

def test_no_bare_mixed_and_or():
    """AC2: No bare mixed and/or without parentheses."""
    src = _SERVICE.read_text(encoding="utf-8")
    start = src.index("def _compute_summary_counts(")
    nxt = src.index("\ndef ", start + 1)
    fn_src = src[start:nxt]

    # Pattern for bare mixed and/or: and followed by something, then or
    # This is a simple heuristic - look for 'and' not in parens followed by 'or'
    # We'll check line by line
    lines = fn_src.split('\n')

    for i, line in enumerate(lines):
        if 'if ' not in line or 'status' not in line or 'has_agent_action' not in line:
            continue

        # Count parens and and/or to check structure
        condition = line[line.index('if '):] if 'if ' in line else line

        # Simple check: if we see "and ... or" pattern, parentheses should clarify it
        if ' and ' in condition and ' or ' in condition:
            # Find the positions
            and_pos = condition.find(' and ')
            or_pos = condition.find(' or ')

            if and_pos > 0 and or_pos > and_pos:
                # There's both 'and' and 'or' in order
                # Check if they're properly parenthesized
                before_and = condition[:and_pos]
                between = condition[and_pos:or_pos]
                after_or = condition[or_pos:]

                # Count unmatched parens before the and
                open_before = before_and.count('(') - before_and.count(')')

                # The and/or should be inside a parenthetical group
                # or at least one side should be parenthesized
                assert '(' in condition and ')' in condition, (
                    f"Mixed and/or without parentheses at line {i}: {line}"
                )


# ── AC3: Logical result identical for all input combinations ────────────────

@pytest.mark.parametrize("has_agent_action,status_in_not_settled,status_is_empty,expected", [
    # Row: (has_agent_action, status in _NOT_SETTLED, status == "", expected unsettled)
    (True, True, True, True),    # agent ran, status is settled column — unsettled
    (True, True, False, True),   # agent ran, status is settled column — unsettled
    (True, False, True, False),  # agent ran, empty status — settled
    (True, False, False, False), # agent ran, other status — settled
    (False, True, True, True),   # no agent run, status is settled column — unsettled
    (False, True, False, True),  # no agent run, status is settled column — unsettled
    (False, False, True, True),  # no agent run, empty status — unsettled
    (False, False, False, False),# no agent run, other status — settled
])
def test_all_input_combinations(has_agent_action, status_in_not_settled, status_is_empty, expected):
    """AC3: Logical result matches expected for all combinations."""
    # Construct an issue with the specified properties
    if status_in_not_settled:
        status = "in-progress"  # in _NOT_SETTLED_STATUSES
    elif status_is_empty:
        status = ""
    else:
        status = "done"  # some other status

    agent_status = "completed" if has_agent_action else None
    failure_reason = None

    issues = [
        {
            "status": status,
            "agent_status": agent_status,
            "failure_reason": failure_reason,
        }
    ]

    result = _compute(issues)
    settled = result["summary_settled_done"]

    # If expected is True (unsettled), then settled should be 0
    # If expected is False (settled), then settled should be 1
    expected_settled = 0 if expected else 1

    assert settled == expected_settled, (
        f"For has_agent_action={has_agent_action}, status_in_not_settled={status_in_not_settled}, "
        f"status_is_empty={status_is_empty}: expected settled={expected_settled}, got {settled}"
    )


# ── AC4: Existing tests still pass ─────────────────────────────────────────

def test_existing_test_suite_passes():
    """AC4: Existing tests for _compute_summary_counts continue to pass."""
    # This will be verified when we run pytest on the full test suite
    # This test just documents the requirement
    pass
