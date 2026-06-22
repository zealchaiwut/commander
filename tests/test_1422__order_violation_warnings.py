"""Tests for issue #1422 — Warn and auto-fix manual order violating DAG levels.

Anchored to Acceptance Criteria:
- AC1:  after drag-drop, board re-evaluates each ticket's position against DAG levels
- AC2:  ticket placed before a dependency shows a violation (warning indicator)
- AC3:  violation info includes the specific upstream dependency and reason
- AC4:  Fix order moves only the violating ticket to the earliest valid slot after
        all its dependencies, leaving other tickets undisturbed
- AC5:  fix is idempotent — after fixing, no violations remain for that ticket
- AC6:  warning clears when ticket is in a valid position
- AC7:  multiple tickets with violations are each flagged independently
- AC8:  ticket in a valid position produces no violation
- AC9:  acceptance-example scenario: drag #B before #A (shared file) → warning
        on #B; Fix order moves #B to earliest slot after #A; warning clears
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SERVICES_DIR = REPO_ROOT / "services" / "sprint_manager"
for _p in (str(REPO_ROOT), str(DASHBOARD_DIR), str(SERVICES_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ── AC1/AC2: violation detection ──────────────────────────────────────────────

def test_violation_detected_when_b_before_a_shared_file():
    """AC2: ticket B placed before dependency A (shared file) returns a violation for B."""
    from routers.sprints_service import compute_order_violations

    levels = [["#1"], ["#2"]]  # #2 depends on #1
    conflicts = [{"ticket1_id": 1, "ticket2_id": 2, "shared_files": ["src/utils.ts"]}]
    current_order = [2, 1]  # B before A — violation

    violations = compute_order_violations(current_order, levels, conflicts)

    assert 2 in violations, (
        f"Ticket #2 (B) should be flagged when placed before #1 (A); got {violations}"
    )
    assert 1 not in violations, (
        f"Ticket #1 (A — the upstream) must not be flagged; got {violations}"
    )


def test_no_violation_when_order_is_valid():
    """AC8: ticket in a valid position (after all its dependencies) shows no violation."""
    from routers.sprints_service import compute_order_violations

    levels = [["#1"], ["#2"]]
    conflicts = [{"ticket1_id": 1, "ticket2_id": 2, "shared_files": ["src/utils.ts"]}]
    current_order = [1, 2]  # correct order — no violation

    violations = compute_order_violations(current_order, levels, conflicts)

    assert violations == {}, (
        f"No violations when order is valid; got {violations}"
    )


def test_no_violation_single_ticket():
    """AC8: a single-ticket sprint has no violations (no comparisons to make)."""
    from routers.sprints_service import compute_order_violations

    levels = [["#1"]]
    conflicts = []
    current_order = [1]

    violations = compute_order_violations(current_order, levels, conflicts)

    assert violations == {}, f"Single-ticket sprint must have no violations; got {violations}"


def test_no_violation_no_dag_data():
    """AC1: when DAG levels and conflicts are empty, no violations are detected."""
    from routers.sprints_service import compute_order_violations

    violations = compute_order_violations([3, 1, 2], [], [])

    assert violations == {}, f"No violations without DAG data; got {violations}"


# ── AC3: violation info includes reason ──────────────────────────────────────

def test_violation_reason_mentions_upstream_ticket():
    """AC3: violation entry includes the upstream ticket number in the reason."""
    from routers.sprints_service import compute_order_violations

    levels = [["#10"], ["#20"]]
    conflicts = [{"ticket1_id": 10, "ticket2_id": 20, "shared_files": ["src/auth.py"]}]
    current_order = [20, 10]  # #20 before #10 — violation

    violations = compute_order_violations(current_order, levels, conflicts)

    assert 20 in violations
    vio_list = violations[20]
    assert len(vio_list) >= 1
    vio = vio_list[0]
    assert vio["upstream_num"] == 10, (
        f"Violation must reference upstream ticket #10; got {vio}"
    )
    assert "reason" in vio, f"Violation must have 'reason' field; got {vio}"
    assert "10" in vio["reason"], (
        f"Reason must mention upstream #10; got reason={vio['reason']!r}"
    )


def test_violation_reason_mentions_shared_file():
    """AC3: tooltip reason references the shared file."""
    from routers.sprints_service import compute_order_violations

    levels = [["#1"], ["#2"]]
    conflicts = [{"ticket1_id": 1, "ticket2_id": 2, "shared_files": ["src/utils.ts"]}]
    current_order = [2, 1]

    violations = compute_order_violations(current_order, levels, conflicts)

    reason = violations[2][0]["reason"]
    assert "utils.ts" in reason or "src/utils.ts" in reason, (
        f"Reason should mention the shared file; got {reason!r}"
    )


# ── AC4/AC5: Fix order placement ──────────────────────────────────────────────

def test_fix_order_slot_moves_b_after_a():
    """AC4: Fix order places B immediately after its dependency A."""
    from routers.sprints_service import compute_fix_order_slot

    # Current order: [2, 1, 3] — #2 is before #1 (its dependency)
    # After fix, #2 should be immediately after #1
    current_order = [2, 1, 3]
    violations = [{"upstream_num": 1, "reason": "#2 depends on #1"}]
    new_order = compute_fix_order_slot(current_order, 2, violations)

    assert 1 in new_order and 2 in new_order
    assert new_order.index(1) < new_order.index(2), (
        f"After fix, #1 must precede #2; got order {new_order}"
    )
    assert 3 in new_order, f"Ticket #3 must remain in order; got {new_order}"


def test_fix_order_only_moves_target_ticket():
    """AC4: Fix order leaves all other tickets in their original relative positions."""
    from routers.sprints_service import compute_fix_order_slot

    # Tickets 2 is violating; 1, 3, 4 must stay in their relative order
    current_order = [2, 1, 3, 4]
    violations = [{"upstream_num": 1, "reason": "#2 depends on #1"}]
    new_order = compute_fix_order_slot(current_order, 2, violations)

    # Remove ticket 2 from both and compare relative order of remaining tickets
    other_in_new = [n for n in new_order if n != 2]
    other_in_old = [n for n in current_order if n != 2]
    assert other_in_new == other_in_old, (
        f"Relative order of non-moved tickets must be preserved; "
        f"expected {other_in_old}, got {other_in_new}"
    )


def test_fix_order_earliest_valid_slot_after_latest_dep():
    """AC4: when B depends on multiple upstream tickets, Fix order places B after the latest one."""
    from routers.sprints_service import compute_fix_order_slot

    # B (ticket 5) depends on A1 (ticket 1) and A2 (ticket 3)
    # current order: [5, 1, 2, 3, 4] — 5 before both 1 and 3
    # After fix, 5 should go right after 3 (the later dep)
    current_order = [5, 1, 2, 3, 4]
    violations = [
        {"upstream_num": 1, "reason": "#5 depends on #1"},
        {"upstream_num": 3, "reason": "#5 depends on #3"},
    ]
    new_order = compute_fix_order_slot(current_order, 5, violations)

    assert new_order.index(3) < new_order.index(5), (
        f"#5 must come after #3 (its later dependency); got {new_order}"
    )
    assert new_order.index(1) < new_order.index(5), (
        f"#5 must come after #1 as well; got {new_order}"
    )


def test_fix_order_idempotent():
    """AC5: after applying Fix order, no violations remain for that ticket."""
    from routers.sprints_service import compute_order_violations, compute_fix_order_slot

    levels = [["#1"], ["#2"]]
    conflicts = [{"ticket1_id": 1, "ticket2_id": 2, "shared_files": ["src/core.py"]}]
    initial_order = [2, 1]  # violation

    violations = compute_order_violations(initial_order, levels, conflicts)
    assert 2 in violations  # has violation

    new_order = compute_fix_order_slot(initial_order, 2, violations[2])
    violations_after = compute_order_violations(new_order, levels, conflicts)

    assert 2 not in violations_after, (
        f"After Fix order, ticket #2 must have no violations; "
        f"got violations_after={violations_after}, new_order={new_order}"
    )


# ── AC6: warning clears when position is valid ───────────────────────────────

def test_no_violation_after_manual_reorder_to_valid_position():
    """AC6: warning clears automatically when ticket is repositioned to a valid slot."""
    from routers.sprints_service import compute_order_violations

    levels = [["#1"], ["#2"]]
    conflicts = [{"ticket1_id": 1, "ticket2_id": 2, "shared_files": ["src/api.py"]}]

    # Initially violating
    v1 = compute_order_violations([2, 1], levels, conflicts)
    assert 2 in v1, "Expected violation when #2 before #1"

    # Manually move #2 after #1
    v2 = compute_order_violations([1, 2], levels, conflicts)
    assert 2 not in v2, "Warning must clear when #2 is repositioned after #1"


# ── AC7: multiple independent violations ─────────────────────────────────────

def test_multiple_tickets_each_have_independent_violations():
    """AC7: when multiple tickets are in violation, each is flagged independently."""
    from routers.sprints_service import compute_order_violations

    # #2 depends on #1, #4 depends on #3
    # Both #2 and #4 are before their dependencies
    levels = [["#1", "#3"], ["#2", "#4"]]
    conflicts = [
        {"ticket1_id": 1, "ticket2_id": 2, "shared_files": ["a.py"]},
        {"ticket1_id": 3, "ticket2_id": 4, "shared_files": ["b.py"]},
    ]
    current_order = [2, 4, 1, 3]  # both #2 and #4 violate

    violations = compute_order_violations(current_order, levels, conflicts)

    assert 2 in violations, f"Ticket #2 must be flagged; got {violations}"
    assert 4 in violations, f"Ticket #4 must be flagged; got {violations}"
    assert 1 not in violations, f"Upstream #1 must not be flagged; got {violations}"
    assert 3 not in violations, f"Upstream #3 must not be flagged; got {violations}"


def test_fix_order_for_one_does_not_affect_other_violations():
    """AC7: fixing one violating ticket leaves other violations unchanged."""
    from routers.sprints_service import compute_order_violations, compute_fix_order_slot

    levels = [["#1", "#3"], ["#2", "#4"]]
    conflicts = [
        {"ticket1_id": 1, "ticket2_id": 2, "shared_files": ["a.py"]},
        {"ticket1_id": 3, "ticket2_id": 4, "shared_files": ["b.py"]},
    ]
    current_order = [2, 4, 1, 3]

    violations = compute_order_violations(current_order, levels, conflicts)
    # Fix only #2's violation
    new_order = compute_fix_order_slot(current_order, 2, violations[2])

    # #4 should still have a violation (it wasn't fixed)
    violations_after = compute_order_violations(new_order, levels, conflicts)
    assert 4 in violations_after, (
        f"Ticket #4's violation must persist after fixing only #2; got {violations_after}"
    )
    assert 2 not in violations_after, (
        f"Ticket #2 must be fixed; got {violations_after}"
    )


# ── AC9: acceptance-example scenario ─────────────────────────────────────────

def test_acceptance_example_b_before_a_shared_file():
    """AC9: #B before #A (A owns shared file) → warning on #B; Fix order moves #B after #A."""
    from routers.sprints_service import compute_order_violations, compute_fix_order_slot

    # #A = 10, #B = 20; B depends on A via src/utils.ts
    levels = [["#10"], ["#20"]]
    conflicts = [{"ticket1_id": 10, "ticket2_id": 20, "shared_files": ["src/utils.ts"]}]

    # User drags #B above #A
    order_after_drag = [20, 10]

    violations = compute_order_violations(order_after_drag, levels, conflicts)
    assert 20 in violations, "Warning must appear on #B when dragged before #A"
    assert 10 not in violations, "#A must not have a warning"

    # User clicks Fix order on #B
    new_order = compute_fix_order_slot(order_after_drag, 20, violations[20])

    # #B should now come after #A
    assert new_order.index(10) < new_order.index(20), (
        f"After Fix order, #B must be after #A; got {new_order}"
    )

    # Warning should clear
    violations_after = compute_order_violations(new_order, levels, conflicts)
    assert 20 not in violations_after, "Warning must clear after Fix order"


def test_fix_order_chain_dependency():
    """AC9 variant: chain A→B→C; drag C to top → warning on C; Fix order places C after B."""
    from routers.sprints_service import compute_order_violations, compute_fix_order_slot

    # Chain: A(1)→B(2)→C(3); C depends on B which depends on A
    levels = [["#1"], ["#2"], ["#3"]]
    conflicts = [
        {"ticket1_id": 1, "ticket2_id": 2, "shared_files": ["core.py"]},
        {"ticket1_id": 2, "ticket2_id": 3, "shared_files": ["derived.py"]},
    ]
    # User drags C to position 0 (top)
    order_after_drag = [3, 1, 2]

    violations = compute_order_violations(order_after_drag, levels, conflicts)
    assert 3 in violations, "C must be flagged when before its dependency B"

    # Fix order for C
    new_order = compute_fix_order_slot(order_after_drag, 3, violations[3])

    # C must come after B (and A)
    assert new_order.index(2) < new_order.index(3), (
        f"After fix, C must come after B; got {new_order}"
    )


def test_no_violation_for_independent_ticket():
    """AC8: ticket with no dependencies can be dragged anywhere without a warning."""
    from routers.sprints_service import compute_order_violations

    # #3 has no deps; #1 and #2 have a dep relationship
    levels = [["#1"], ["#2"], ["#3"]]
    conflicts = [{"ticket1_id": 1, "ticket2_id": 2, "shared_files": ["api.py"]}]
    # Independent ticket #3 is dragged to position 0
    current_order = [3, 1, 2]

    violations = compute_order_violations(current_order, levels, conflicts)

    assert 3 not in violations, (
        f"Independent ticket #3 (no deps in conflicts) must not be flagged; got {violations}"
    )
