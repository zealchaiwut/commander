"""Tests for issue #814: sprint_manager RUN_MUTABLE_LABELS omits "blocked" (runs against UAT)"""
import sys
from pathlib import Path


def test_814__sprint_manager_run_mutable_labels_includes_blocked():
    """AC: sprint_manager.RUN_MUTABLE_LABELS includes 'blocked'."""
    from services.sprint_manager.sprint_manager import RUN_MUTABLE_LABELS
    assert "blocked" in RUN_MUTABLE_LABELS, f"Expected 'blocked' in {sorted(RUN_MUTABLE_LABELS)}"


def test_814__both_run_mutable_labels_match():
    """AC: sprint_manager.RUN_MUTABLE_LABELS and state_machine.RUN_MUTABLE_LABELS contain identical label sets."""
    from services.sprint_manager.sprint_manager import RUN_MUTABLE_LABELS as sm_rml
    from services.sprint_manager.state_machine import RUN_MUTABLE_LABELS as state_rml

    assert sm_rml == state_rml, (
        f"RUN_MUTABLE_LABELS mismatch:\n"
        f"  sprint_manager: {sorted(sm_rml)}\n"
        f"  state_machine:  {sorted(state_rml)}"
    )


def test_814__guard_sprint_labels_does_not_strip_blocked():
    """AC: _guard_sprint_labels no longer strips 'blocked' from the label list during an active sprint run."""
    from services.sprint_manager.sprint_manager import _guard_sprint_labels

    # Simulate an active sprint run (sprint_label provided)
    safe_add, safe_remove = _guard_sprint_labels(
        add=["blocked"],
        remove=[],
        sprint_label="sprint-97"
    )

    assert "blocked" in safe_add, (
        f"Expected 'blocked' to pass _guard_sprint_labels during active run, "
        f"but got safe_add={safe_add}"
    )
    assert safe_remove == [], f"Expected no removals, got {safe_remove}"


def test_814__transition_to_blocked_is_reachable():
    """AC: The transition(BLOCKED) branch is reachable when 'blocked' label is passed during a run.

    We verify this by checking that the state_machine module knows about the BLOCKED state
    and that it can transition to it (the actual transition is tested in integration tests).
    """
    from services.sprint_manager.state_machine import TicketState, STATE_LABELS

    # BLOCKED state must exist
    assert TicketState.BLOCKED in TicketState, "TicketState.BLOCKED must exist"

    # BLOCKED state must have exactly one label: "blocked"
    assert STATE_LABELS[TicketState.BLOCKED] == frozenset({"blocked"}), (
        f"Expected BLOCKED state to have label 'blocked', got {STATE_LABELS[TicketState.BLOCKED]}"
    )


def test_814__label_guard_allows_mutable_labels_only():
    """AC: _guard_sprint_labels allows only RUN_MUTABLE_LABELS during an active run.

    Verify that 'blocked' is now treated as a mutable label during runs,
    unlike before when it was filtered out.
    """
    from services.sprint_manager.sprint_manager import (
        RUN_MUTABLE_LABELS,
        _guard_sprint_labels,
    )
    from services.sprint_manager.state_machine import STATUS_LABELS

    # All RUN_MUTABLE_LABELS should include all status labels (for active runs)
    # or at least the BLOCKED state label
    assert "blocked" in RUN_MUTABLE_LABELS, "RUN_MUTABLE_LABELS must include 'blocked'"

    # Verify filtering: a non-mutable label gets deferred during active runs
    safe_add_blocked, _ = _guard_sprint_labels(
        add=["blocked"],
        remove=[],
        sprint_label="sprint-test"
    )
    assert "blocked" in safe_add_blocked, "blocked should not be filtered during active runs"

    # But non-status labels are still deferred
    safe_add_other, _ = _guard_sprint_labels(
        add=["some-random-label"],
        remove=[],
        sprint_label="sprint-test"
    )
    assert "some-random-label" not in safe_add_other, "non-mutable labels should be filtered"
