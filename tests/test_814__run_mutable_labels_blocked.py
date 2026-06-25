"""Tests for issue #814: sprint_manager.RUN_MUTABLE_LABELS omits "blocked".

AC-1: sprint_manager.RUN_MUTABLE_LABELS includes "blocked"
AC-2: sprint_manager.RUN_MUTABLE_LABELS and state_machine.RUN_MUTABLE_LABELS
      contain identical label sets (no drift).
AC-3: _guard_sprint_labels no longer strips "blocked" from add-list during
      an active sprint run (sprint_label provided).
AC-4: _add_blocked_label during an active sprint run reaches _transition_safe
      (BLOCKED branch), not the early-return deferral path.
AC-5: A ticket that receives a HANG event ends up in BLOCKED state — not
      merely receiving a deferral comment with the label absent.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))


class TestAC1BlockedInRunMutableLabels:
    """AC-1: sprint_manager.RUN_MUTABLE_LABELS includes 'blocked'."""

    def test_blocked_present(self):
        from services.sprint_manager.sprint_manager import RUN_MUTABLE_LABELS
        assert "blocked" in RUN_MUTABLE_LABELS, (
            "'blocked' must be in sprint_manager.RUN_MUTABLE_LABELS so "
            "_guard_sprint_labels allows it during an active sprint run"
        )

    def test_existing_labels_still_present(self):
        """Existing mutable labels must not have been removed."""
        from services.sprint_manager.sprint_manager import RUN_MUTABLE_LABELS
        for label in ("in-progress", "SIT", "UAT", "needs-rework"):
            assert label in RUN_MUTABLE_LABELS, (
                f"Expected '{label}' to still be in RUN_MUTABLE_LABELS"
            )


class TestAC2NoDriftBetweenConstants:
    """AC-2: sprint_manager.RUN_MUTABLE_LABELS equals state_machine.RUN_MUTABLE_LABELS."""

    def test_identical_sets(self):
        from services.sprint_manager.sprint_manager import (
            RUN_MUTABLE_LABELS as sm_labels,
        )
        from services.sprint_manager.state_machine import (
            RUN_MUTABLE_LABELS as stm_labels,
        )
        assert sm_labels == stm_labels, (
            f"sprint_manager.RUN_MUTABLE_LABELS {sorted(sm_labels)} != "
            f"state_machine.RUN_MUTABLE_LABELS {sorted(stm_labels)}"
        )

    def test_sm_not_missing_any_state_machine_labels(self):
        from services.sprint_manager.sprint_manager import (
            RUN_MUTABLE_LABELS as sm_labels,
        )
        from services.sprint_manager.state_machine import (
            RUN_MUTABLE_LABELS as stm_labels,
        )
        missing = stm_labels - sm_labels
        assert not missing, (
            f"sprint_manager.RUN_MUTABLE_LABELS is missing labels that "
            f"state_machine.RUN_MUTABLE_LABELS has: {sorted(missing)}"
        )

    def test_sm_not_adding_extra_labels(self):
        from services.sprint_manager.sprint_manager import (
            RUN_MUTABLE_LABELS as sm_labels,
        )
        from services.sprint_manager.state_machine import (
            RUN_MUTABLE_LABELS as stm_labels,
        )
        extra = sm_labels - stm_labels
        assert not extra, (
            f"sprint_manager.RUN_MUTABLE_LABELS has extra labels not in "
            f"state_machine.RUN_MUTABLE_LABELS: {sorted(extra)}"
        )


class TestAC3GuardAllowsBlockedDuringRun:
    """AC-3: _guard_sprint_labels no longer strips 'blocked' during active sprint run."""

    def test_blocked_not_stripped_when_sprint_label_provided(self):
        from services.sprint_manager.sprint_manager import _guard_sprint_labels
        safe_add, _ = _guard_sprint_labels(
            add=["blocked"],
            remove=[],
            sprint_label="sprint-97",
        )
        assert "blocked" in safe_add, (
            "'blocked' must pass through _guard_sprint_labels when sprint_label "
            "is provided — it is now in RUN_MUTABLE_LABELS"
        )

    def test_blocked_alongside_other_labels(self):
        from services.sprint_manager.sprint_manager import _guard_sprint_labels
        safe_add, _ = _guard_sprint_labels(
            add=["blocked", "in-progress"],
            remove=[],
            sprint_label="sprint-97",
        )
        assert "blocked" in safe_add
        assert "in-progress" in safe_add

    def test_non_status_labels_still_stripped(self):
        """Non-status labels (e.g. enhancement) are still deferred."""
        from services.sprint_manager.sprint_manager import _guard_sprint_labels
        safe_add, _ = _guard_sprint_labels(
            add=["blocked", "enhancement"],
            remove=[],
            sprint_label="sprint-97",
        )
        assert "blocked" in safe_add
        assert "enhancement" not in safe_add

    def test_sprint_labels_still_protected_from_removal(self):
        """Sprint labels in remove-list are still blocked regardless."""
        from services.sprint_manager.sprint_manager import _guard_sprint_labels
        _, safe_remove = _guard_sprint_labels(
            add=[],
            remove=["sprint-97"],
            sprint_label="sprint-97",
        )
        assert "sprint-97" not in safe_remove


class TestAC4AddBlockedReachesTransition:
    """AC-4: _add_blocked_label during an active sprint run reaches _transition_safe."""

    def _call_add_blocked_with_sprint(self, issue_num=42, reason="hang timeout"):
        """Call _add_blocked_label with a sprint_label (simulating active run).

        Patches label_transitions._transition_safe directly — _add_blocked_label
        lives in label_transitions and calls its local _transition_safe, so
        patching sprint_manager._transition_safe alone has no effect.
        """
        sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))
        import services.sprint_manager.sprint_manager as sm
        import services.sprint_manager.label_transitions as lt

        transition_calls = []

        def fake_transition_safe(iss, state, **kwargs):
            transition_calls.append((iss, state))

        with patch.object(lt, "_transition_safe", side_effect=fake_transition_safe), \
             patch.object(sm.github_client, "add_comment", return_value=None):
            sm._add_blocked_label(
                issue_num=issue_num,
                reason=reason,
                repo_name="zealchaiwut/commander",
                sprint_label="sprint-97",
            )

        return transition_calls, sm

    def test_transition_safe_is_called_during_sprint_run(self):
        calls, _ = self._call_add_blocked_with_sprint()
        assert calls, (
            "_transition_safe must be called when _add_blocked_label fires "
            "during an active sprint run (sprint_label provided)"
        )

    def test_transition_targets_blocked_state(self):
        calls, sm = self._call_add_blocked_with_sprint()
        assert calls, "_transition_safe was never called"
        _, state = calls[0]
        assert state == sm._TicketState.BLOCKED, (
            f"_transition_safe must target BLOCKED state, got {state!r}"
        )

    def test_no_deferral_comment_when_label_applied(self):
        """When 'blocked' is applied successfully, no deferral comment is posted."""
        import services.sprint_manager.sprint_manager as sm
        import services.sprint_manager.label_transitions as lt

        deferral_comments = []

        def fake_add_comment(issue_num, body, **kwargs):
            if "deferred" in body:
                deferral_comments.append(body)

        with patch.object(lt, "_transition_safe", return_value=None), \
             patch.object(sm.github_client, "add_comment", side_effect=fake_add_comment):
            sm._add_blocked_label(
                issue_num=42,
                reason="hang timeout",
                repo_name="zealchaiwut/commander",
                sprint_label="sprint-97",
            )

        assert not deferral_comments, (
            f"Deferral comment should not be posted when 'blocked' label is applied; "
            f"got: {deferral_comments}"
        )


class TestAC5TicketEndsInBlockedState:
    """AC-5: A ticket receiving a HANG ends up in BLOCKED state, not just a deferral comment."""

    def test_blocked_state_not_just_comment(self):
        """After _add_blocked_label with sprint_label, BLOCKED transition is exercised."""
        sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))
        import services.sprint_manager.sprint_manager as sm
        import services.sprint_manager.label_transitions as lt

        blocked_transitions = []

        def fake_transition_safe(iss, state, **kwargs):
            if state == sm._TicketState.BLOCKED:
                blocked_transitions.append(iss)

        with patch.object(lt, "_transition_safe", side_effect=fake_transition_safe), \
             patch.object(sm.github_client, "add_comment", return_value=None):
            sm._add_blocked_label(
                issue_num=99,
                reason="agent hung",
                sprint_label="sprint-97",
            )

        assert 99 in blocked_transitions, (
            "Issue #99 must have a BLOCKED transition after a HANG — "
            "not just a deferral comment. "
            f"Recorded BLOCKED transitions: {blocked_transitions}"
        )
