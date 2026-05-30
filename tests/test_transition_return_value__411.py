"""Tests for issue #411 — transition() returns bool to distinguish no-op from success.

AC covers:
- transition() returns False when ticket already in target state (no-op)
- transition() returns True when labels are successfully changed
- Return value is bool in both cases
- TransitionError is still raised on failure (not affected by this change)
- Callers can use the return value to branch on no-op vs. success
"""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

for _mod in ("dotenv",):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()
sys.modules["dotenv"].load_dotenv = lambda *a, **kw: None  # type: ignore[attr-defined]

from services.sprint_manager.state_machine import (  # noqa: E402
    TicketState,
    TransitionError,
    transition,
)


def _gh_view_output(labels: list[str]) -> str:
    return json.dumps({"labels": [{"name": lbl} for lbl in labels]})


class TestTransitionReturnValue(unittest.TestCase):

    def test_returns_false_when_already_in_target_state(self):
        """No-op: ticket already has SIT label → returns False."""
        view_response = MagicMock(returncode=0, stdout=_gh_view_output(["SIT", "sprint-30"]), stderr="")
        with patch("subprocess.run", return_value=view_response):
            result = transition(42, TicketState.SIT, actor="tester", repo="owner/repo")
        self.assertIs(result, False)

    def test_return_type_is_bool_on_noop(self):
        """Return value is exactly bool False, not falsy None or 0."""
        view_response = MagicMock(returncode=0, stdout=_gh_view_output(["UAT"]), stderr="")
        with patch("subprocess.run", return_value=view_response):
            result = transition(1, TicketState.UAT, actor="tester", repo="owner/repo")
        self.assertIsInstance(result, bool)
        self.assertFalse(result)

    def test_returns_true_on_successful_transition(self):
        """Successful label change returns True."""
        view_before = MagicMock(returncode=0, stdout=_gh_view_output(["in-progress", "sprint-30"]), stderr="")
        edit_ok = MagicMock(returncode=0, stdout="", stderr="")
        view_after = MagicMock(returncode=0, stdout=_gh_view_output(["SIT", "sprint-30"]), stderr="")

        side_effects = [view_before, edit_ok, view_after]
        with patch("subprocess.run", side_effect=side_effects):
            result = transition(42, TicketState.SIT, actor="coder", repo="owner/repo")
        self.assertIs(result, True)

    def test_return_type_is_bool_on_success(self):
        """Return value is exactly bool True, not truthy int 1."""
        view_before = MagicMock(returncode=0, stdout=_gh_view_output([]), stderr="")
        edit_ok = MagicMock(returncode=0, stdout="", stderr="")
        view_after = MagicMock(returncode=0, stdout=_gh_view_output(["in-progress"]), stderr="")

        with patch("subprocess.run", side_effect=[view_before, edit_ok, view_after]):
            result = transition(5, TicketState.IN_PROGRESS, actor="coder", repo="owner/repo")
        self.assertIsInstance(result, bool)
        self.assertTrue(result)

    def test_noop_with_extra_non_status_labels(self):
        """Non-status labels (sprint, estimated) don't affect no-op detection."""
        labels = ["UAT", "sprint-30", "estimated", "infra"]
        view_response = MagicMock(returncode=0, stdout=_gh_view_output(labels), stderr="")
        with patch("subprocess.run", return_value=view_response):
            result = transition(7, TicketState.UAT, actor="human", repo="owner/repo")
        self.assertIs(result, False)

    def test_transition_error_still_raised_on_failure(self):
        """TransitionError is still raised when gh edit fails; no bool returned."""
        view_response = MagicMock(returncode=0, stdout=_gh_view_output(["in-progress"]), stderr="")
        edit_fail = MagicMock(returncode=1, stdout="", stderr="network error")
        # Provide enough responses to cover all retry attempts (view + edit per retry)
        side_effects = []
        for _ in range(4):  # 1 initial + 3 retries
            side_effects.append(view_response)
            side_effects.append(edit_fail)

        with patch("subprocess.run", side_effect=side_effects):
            with patch("time.sleep"):
                with self.assertRaises(TransitionError):
                    transition(9, TicketState.SIT, actor="coder", repo="owner/repo")

    def test_caller_can_branch_on_return_value(self):
        """Idiomatic caller pattern: if transition(...): do_something()"""
        view_noop = MagicMock(returncode=0, stdout=_gh_view_output(["SIT"]), stderr="")
        with patch("subprocess.run", return_value=view_noop):
            changed = transition(10, TicketState.SIT, actor="coder", repo="owner/repo")

        side_effect_log = []
        if changed:
            side_effect_log.append("metrics_recorded")

        self.assertEqual(side_effect_log, [])

    def test_caller_can_branch_on_true_return_value(self):
        """Idiomatic caller pattern on success: if transition(...): record_metrics()"""
        view_before = MagicMock(returncode=0, stdout=_gh_view_output(["in-progress"]), stderr="")
        edit_ok = MagicMock(returncode=0, stdout="", stderr="")
        view_after = MagicMock(returncode=0, stdout=_gh_view_output(["SIT"]), stderr="")

        with patch("subprocess.run", side_effect=[view_before, edit_ok, view_after]):
            changed = transition(11, TicketState.SIT, actor="coder", repo="owner/repo")

        side_effect_log = []
        if changed:
            side_effect_log.append("metrics_recorded")

        self.assertEqual(side_effect_log, ["metrics_recorded"])

    def test_noop_multiple_states(self):
        """No-op returns False for each non-pseudo target state."""
        state_label_map = {
            TicketState.QUEUED:       "backlog",
            TicketState.IN_PROGRESS:  "in-progress",
            TicketState.SIT:          "SIT",
            TicketState.UAT:          "UAT",
            TicketState.NEEDS_REWORK: "need-rework",
        }
        for state, label in state_label_map.items():
            view_response = MagicMock(
                returncode=0,
                stdout=_gh_view_output([label]),
                stderr="",
            )
            with patch("subprocess.run", return_value=view_response):
                result = transition(99, state, actor="test", repo="owner/repo")
            self.assertIs(result, False, f"Expected False (noop) for state {state}")


if __name__ == "__main__":
    unittest.main()
