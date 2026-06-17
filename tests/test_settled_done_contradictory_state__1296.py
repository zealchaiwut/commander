"""Issue #1296 — Narrow settled-done equivalence between materialize and canonical formula.

AC1: _compute_summary_counts and _settled_done_from_columns treat the
     contradictory state (agent_status='completed', status='sit') identically.
AC3: Normal-state outputs are unchanged.
AC4: Unit test covers the contradictory state and asserts both functions return
     the same settled-done value.
"""
from pathlib import Path
import sys

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SERVICE = _REPO / "apps" / "dashboard" / "routers" / "sprint_artifact_service.py"
_SERVER = _REPO / "apps" / "dashboard" / "server.py"


def _load_compute_summary_counts():
    """Import _compute_summary_counts without loading the full router module."""
    src = _SERVICE.read_text(encoding="utf-8")
    start = src.index("def _compute_summary_counts(")
    # find the next top-level def/class to slice just this function
    nxt = src.index("\ndef ", start + 1)
    ns: dict = {}
    exec(compile(src[start:nxt], "<compute_summary_counts>", "exec"), ns)  # noqa: S102
    return ns["_compute_summary_counts"]


def _load_settled_done_from_columns():
    """Import _settled_done_from_columns without loading the full server module."""
    src = _SERVER.read_text(encoding="utf-8")
    start = src.index("def _settled_done_from_columns(")
    nxt = src.index("\ndef ", start + 1)
    ns: dict = {}
    exec(compile(src[start:nxt], "<settled_done>", "exec"), ns)  # noqa: S102
    return ns["_settled_done_from_columns"]


_compute = _load_compute_summary_counts()
_canonical = _load_settled_done_from_columns()


# ── AC1 / AC4: contradictory state ──────────────────────────────────────────

def test_contradictory_state_sit_completed_treated_as_not_settled():
    """AC1 + AC4: status='sit' + agent_status='completed' must count as NOT settled.

    The canonical formula subtracts all sit tickets unconditionally, so
    _compute_summary_counts must also count this issue as not-settled.
    """
    issues = [
        {"status": "sit", "agent_status": "completed", "failure_reason": None},
    ]
    result = _compute(issues)
    # One issue, counted as not-settled → settled_done == 0
    assert result["summary_settled_done"] == 0, (
        "Contradictory state (status='sit', agent_status='completed') "
        "must NOT count as settled-done"
    )


def test_contradictory_state_backlog_completed_treated_as_not_settled():
    """AC1: status='backlog'/'pending' + agent_status='completed' must not be settled."""
    issues = [
        {"status": "pending", "agent_status": "completed", "failure_reason": None},
    ]
    result = _compute(issues)
    assert result["summary_settled_done"] == 0


def test_contradictory_state_in_progress_completed_treated_as_not_settled():
    """AC1: status='in-progress' + agent_status='completed' must not be settled."""
    issues = [
        {"status": "in-progress", "agent_status": "completed", "failure_reason": None},
    ]
    result = _compute(issues)
    assert result["summary_settled_done"] == 0


def test_both_functions_agree_on_contradictory_state():
    """AC1: Both _compute_summary_counts and _settled_done_from_columns agree.

    For the contradictory state, simulate what _settled_done_from_columns sees:
    one issue in the 'sit' column → settled_done = total - sit = 1 - 1 = 0.
    """
    issues = [
        {"status": "sit", "agent_status": "completed", "failure_reason": None},
    ]
    compute_result = _compute(issues)["summary_settled_done"]
    canonical_result = _canonical(total=1, columns={"sit": 1})
    assert compute_result == canonical_result, (
        f"_compute_summary_counts={compute_result} but "
        f"_settled_done_from_columns={canonical_result} for contradictory state"
    )


# ── AC3: normal states unchanged ────────────────────────────────────────────

@pytest.mark.parametrize("issues,expected_settled", [
    # pure backlog — not settled
    ([{"status": "pending", "agent_status": None, "failure_reason": None}], 0),
    # pure in-progress — not settled
    ([{"status": "in-progress", "agent_status": None, "failure_reason": None}], 0),
    # pure sit — not settled
    ([{"status": "sit", "agent_status": None, "failure_reason": None}], 0),
    # done — settled
    ([{"status": "done", "agent_status": "completed", "failure_reason": None}], 1),
    # uat — settled
    ([{"status": "uat", "agent_status": "completed", "failure_reason": None}], 1),
    # failed — settled
    ([{"status": "done", "agent_status": "failed", "failure_reason": "timeout"}], 1),
])
def test_normal_states_unchanged(issues, expected_settled):
    """AC3: Normal-state outputs must not change after the fix."""
    result = _compute(issues)
    assert result["summary_settled_done"] == expected_settled, (
        f"Normal state {issues[0]} expected settled={expected_settled}, "
        f"got {result['summary_settled_done']}"
    )


def test_mixed_normal_states():
    """AC3: Multi-issue mix of normal states produces correct settled count."""
    issues = [
        {"status": "pending", "agent_status": None, "failure_reason": None},  # not settled
        {"status": "in-progress", "agent_status": None, "failure_reason": None},  # not settled
        {"status": "sit", "agent_status": None, "failure_reason": None},  # not settled
        {"status": "done", "agent_status": "completed", "failure_reason": None},  # settled
        {"status": "uat", "agent_status": "completed", "failure_reason": None},  # settled
    ]
    result = _compute(issues)
    assert result["summary_settled_done"] == 2


# ── AC2: comment or guard must be present ───────────────────────────────────

def test_guard_or_comment_present():
    """AC2: Either a column-status guard is present in _compute_summary_counts,
    or the equivalence comment is narrowed to the normal-flow case.
    """
    src = _SERVICE.read_text(encoding="utf-8")
    start = src.index("def _compute_summary_counts(")
    nxt = src.index("\ndef ", start + 1)
    fn_src = src[start:nxt]

    has_guard = "status in _NOT_SETTLED_STATUSES" in fn_src and (
        # guard appears without the `not has_agent_action` condition
        "if status in _NOT_SETTLED_STATUSES:" in fn_src
        or "status in _NOT_SETTLED_STATUSES or" in fn_src
        # any form that makes the column-status unconditional
        or fn_src.count("status in _NOT_SETTLED_STATUSES") >= 2
    )
    has_narrowed_comment = (
        "normal flow" in fn_src.lower()
        or "normal-flow" in fn_src.lower()
        or "contradictory" in fn_src.lower()
    )

    assert has_guard or has_narrowed_comment, (
        "AC2: _compute_summary_counts must either have an unconditional "
        "column-status guard or a comment narrowing equivalence to normal flow"
    )
