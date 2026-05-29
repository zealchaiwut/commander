"""Tests for issue #381 — atomic transition() state machine.

AC covers:
- TicketState enum, STATE_LABELS, STATUS_LABELS, TransitionError exported
- transition() computes diff via (STATUS_LABELS & current) - desired
- Single gh issue edit call combining --add-label and --remove-label
- Verifies labels after edit; raises TransitionError if mismatch
- Retries up to 3 times with backoff (1, 3, 7) before raising
- Logs every transition with from-state, to-state, actor, optional note
- No internal safeguards (caller's responsibility)
- BACKLOG and DONE are pseudo-states; transition() rejects them as targets
- Module importable with no circular imports
- Diff math: [sprint-25, in-progress, estimated] → SIT removes in-progress,
  adds SIT, leaves sprint-25 and estimated untouched
"""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

# Stub dotenv so services.logging can load without a real .env
for _mod in ("dotenv",):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()
sys.modules["dotenv"].load_dotenv = lambda *a, **kw: None  # type: ignore[attr-defined]

from services.sprint_manager.state_machine import (  # noqa: E402
    STATE_LABELS,
    STATUS_LABELS,
    TicketState,
    TransitionError,
    transition,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _gh_view_output(labels: list[str]) -> str:
    return json.dumps({"labels": [{"name": lbl} for lbl in labels]})


def _make_run_side_effects(*label_sequences, edit_ok: bool = True):
    """Return side_effect list for subprocess.run calls.

    Each label_sequence is the list of GitHub labels to return for a
    'gh issue view' call.  'gh issue edit' always succeeds (returncode=0)
    unless edit_ok=False.
    """
    effects = []
    for labels in label_sequences:
        if labels == "__edit__":
            rc = 0 if edit_ok else 1
            effects.append(MagicMock(returncode=rc, stdout="", stderr="edit failed"))
        else:
            effects.append(MagicMock(returncode=0, stdout=_gh_view_output(labels), stderr=""))
    return effects


# ---------------------------------------------------------------------------
# Basic structure tests
# ---------------------------------------------------------------------------

class TestModuleExports(unittest.TestCase):
    def test_ticket_state_enum_exists(self):
        self.assertTrue(issubclass(TicketState, Exception.__class__.__mro__[-1].__subclasshook__.__class__) or True)
        import enum
        self.assertIsInstance(TicketState, enum.EnumMeta)

    def test_backlog_and_done_defined(self):
        self.assertIn(TicketState.BACKLOG, TicketState)
        self.assertIn(TicketState.DONE, TicketState)

    def test_all_states_defined(self):
        for state in (
            TicketState.QUEUED, TicketState.IN_PROGRESS,
            TicketState.SIT, TicketState.UAT, TicketState.NEEDS_REWORK,
        ):
            self.assertIn(state, TicketState)

    def test_state_labels_has_all_states(self):
        for state in TicketState:
            self.assertIn(state, STATE_LABELS)

    def test_status_labels_is_frozenset(self):
        self.assertIsInstance(STATUS_LABELS, frozenset)

    def test_status_labels_union_of_state_labels(self):
        expected = frozenset().union(*STATE_LABELS.values())
        self.assertEqual(STATUS_LABELS, expected)

    def test_status_labels_excludes_sprint_and_estimated(self):
        self.assertNotIn("sprint-25", STATUS_LABELS)
        self.assertNotIn("estimated", STATUS_LABELS)
        self.assertNotIn("bug", STATUS_LABELS)
        self.assertNotIn("enhancement", STATUS_LABELS)

    def test_transition_error_is_exception(self):
        self.assertTrue(issubclass(TransitionError, Exception))


# ---------------------------------------------------------------------------
# Pseudo-state rejection
# ---------------------------------------------------------------------------

class TestPseudoStateRejection(unittest.TestCase):
    def test_transition_rejects_backlog(self):
        with self.assertRaises(ValueError):
            transition(1, TicketState.BACKLOG, actor="test", repo="owner/repo")

    def test_transition_rejects_done(self):
        with self.assertRaises(ValueError):
            transition(1, TicketState.DONE, actor="test", repo="owner/repo")


# ---------------------------------------------------------------------------
# Diff math
# ---------------------------------------------------------------------------

class TestDiffMath(unittest.TestCase):
    """AC: diff math — (STATUS_LABELS & current) - desired."""

    def test_diff_sprint25_inprogress_estimated_to_sit(self):
        """[sprint-25, in-progress, estimated] → SIT: removes in-progress, adds SIT."""
        current = frozenset({"sprint-25", "in-progress", "estimated"})
        desired = STATE_LABELS[TicketState.SIT]

        current_status = STATUS_LABELS & current
        to_remove = current_status - desired
        to_add = desired - current_status

        self.assertEqual(to_remove, {"in-progress"})
        self.assertEqual(to_add, {"SIT"})

    def test_sprint_label_not_in_status_labels(self):
        current = frozenset({"sprint-25", "in-progress"})
        current_status = STATUS_LABELS & current
        self.assertNotIn("sprint-25", current_status)

    def test_estimated_not_in_status_labels(self):
        current = frozenset({"estimated", "SIT"})
        current_status = STATUS_LABELS & current
        self.assertNotIn("estimated", current_status)


# ---------------------------------------------------------------------------
# Valid transitions via subprocess mock
# ---------------------------------------------------------------------------

class TestValidTransitions(unittest.TestCase):
    """Every valid state-to-state transition succeeds."""

    def _run_transition(self, from_labels, target_state):
        """Call transition() with mocked subprocess and return (add_args, remove_args)."""
        desired = STATE_LABELS[target_state]
        after_labels = list(desired) + ["sprint-25"]

        side_effects = [
            MagicMock(returncode=0, stdout=_gh_view_output(from_labels), stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),           # edit
            MagicMock(returncode=0, stdout=_gh_view_output(after_labels), stderr=""),  # verify
        ]

        with patch("services.sprint_manager.state_machine.subprocess.run",
                   side_effect=side_effects) as mock_run:
            transition(42, target_state, actor="tester", repo="owner/repo")

        edit_call = mock_run.call_args_list[1]
        cmd = edit_call[0][0]
        return cmd

    def _extract_labels(self, cmd, flag):
        labels = []
        for i, arg in enumerate(cmd):
            if arg == flag and i + 1 < len(cmd):
                labels.append(cmd[i + 1])
        return set(labels)

    def test_queued_to_in_progress(self):
        cmd = self._run_transition(["backlog", "sprint-25"], TicketState.IN_PROGRESS)
        self.assertIn("--add-label", cmd)
        added = self._extract_labels(cmd, "--add-label")
        removed = self._extract_labels(cmd, "--remove-label")
        self.assertIn("in-progress", added)
        self.assertIn("backlog", removed)

    def test_in_progress_to_sit(self):
        cmd = self._run_transition(["in-progress", "sprint-25"], TicketState.SIT)
        added = self._extract_labels(cmd, "--add-label")
        removed = self._extract_labels(cmd, "--remove-label")
        self.assertIn("SIT", added)
        self.assertIn("in-progress", removed)

    def test_sit_to_uat(self):
        cmd = self._run_transition(["SIT", "sprint-25"], TicketState.UAT)
        added = self._extract_labels(cmd, "--add-label")
        removed = self._extract_labels(cmd, "--remove-label")
        self.assertIn("UAT", added)
        self.assertIn("SIT", removed)

    def test_uat_to_needs_rework(self):
        cmd = self._run_transition(["UAT", "sprint-25"], TicketState.NEEDS_REWORK)
        added = self._extract_labels(cmd, "--add-label")
        removed = self._extract_labels(cmd, "--remove-label")
        self.assertIn("need-rework", added)
        self.assertIn("UAT", removed)

    def test_needs_rework_to_queued(self):
        cmd = self._run_transition(["need-rework", "sprint-25"], TicketState.QUEUED)
        added = self._extract_labels(cmd, "--add-label")
        removed = self._extract_labels(cmd, "--remove-label")
        self.assertIn("backlog", added)
        self.assertIn("need-rework", removed)

    def test_sprint_label_never_removed(self):
        """sprint-25 must not appear in --remove-label."""
        cmd = self._run_transition(["in-progress", "sprint-25"], TicketState.SIT)
        removed = self._extract_labels(cmd, "--remove-label")
        self.assertNotIn("sprint-25", removed)

    def test_estimated_label_never_removed(self):
        """estimated must not appear in --remove-label."""
        cmd = self._run_transition(
            ["in-progress", "sprint-25", "estimated"], TicketState.SIT
        )
        removed = self._extract_labels(cmd, "--remove-label")
        self.assertNotIn("estimated", removed)


# ---------------------------------------------------------------------------
# No-op
# ---------------------------------------------------------------------------

class TestNoOp(unittest.TestCase):
    def test_noop_when_already_in_target_state(self):
        """No gh issue edit call when ticket already has the target labels."""
        current = list(STATE_LABELS[TicketState.SIT]) + ["sprint-25"]
        side_effects = [
            MagicMock(returncode=0, stdout=_gh_view_output(current), stderr=""),
        ]

        with patch("services.sprint_manager.state_machine.subprocess.run",
                   side_effect=side_effects) as mock_run:
            transition(42, TicketState.SIT, actor="tester", repo="owner/repo")

        # Only one call (the view), no edit call
        self.assertEqual(mock_run.call_count, 1)
        cmd = mock_run.call_args_list[0][0][0]
        self.assertIn("view", cmd)


# ---------------------------------------------------------------------------
# Single call combining add and remove
# ---------------------------------------------------------------------------

class TestSingleEditCall(unittest.TestCase):
    def test_single_gh_issue_edit_call(self):
        """Exactly one gh issue edit call combining --add-label and --remove-label."""
        from_labels = ["in-progress", "sprint-25"]
        after_labels = ["SIT", "sprint-25"]

        side_effects = [
            MagicMock(returncode=0, stdout=_gh_view_output(from_labels), stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout=_gh_view_output(after_labels), stderr=""),
        ]

        with patch("services.sprint_manager.state_machine.subprocess.run",
                   side_effect=side_effects) as mock_run:
            transition(42, TicketState.SIT, actor="coder", repo="owner/repo")

        edit_calls = [
            c for c in mock_run.call_args_list
            if "edit" in c[0][0]
        ]
        self.assertEqual(len(edit_calls), 1)
        cmd = edit_calls[0][0][0]
        self.assertIn("--add-label", cmd)
        self.assertIn("--remove-label", cmd)


# ---------------------------------------------------------------------------
# Retry logic
# ---------------------------------------------------------------------------

class TestRetryLogic(unittest.TestCase):
    def test_retry_succeeds_on_second_attempt(self):
        """Retry succeeds on second attempt after a single failure."""
        from_labels = ["in-progress", "sprint-25"]
        after_labels = ["SIT", "sprint-25"]

        side_effects = [
            # attempt 1: fetch labels
            MagicMock(returncode=0, stdout=_gh_view_output(from_labels), stderr=""),
            # attempt 1: edit fails
            MagicMock(returncode=1, stdout="", stderr="rate limited"),
            # sleep(1) happens here...
            # attempt 2: fetch labels
            MagicMock(returncode=0, stdout=_gh_view_output(from_labels), stderr=""),
            # attempt 2: edit succeeds
            MagicMock(returncode=0, stdout="", stderr=""),
            # attempt 2: verify
            MagicMock(returncode=0, stdout=_gh_view_output(after_labels), stderr=""),
        ]

        with patch("services.sprint_manager.state_machine.subprocess.run",
                   side_effect=side_effects), \
             patch("services.sprint_manager.state_machine.time.sleep") as mock_sleep:
            transition(42, TicketState.SIT, actor="coder", repo="owner/repo")

        # Should have slept once (backoff before retry)
        mock_sleep.assert_called_once_with(1)

    def test_transition_error_after_all_retries_exhausted(self):
        """TransitionError raised when all retries are exhausted."""
        from_labels = ["in-progress", "sprint-25"]

        # Every edit call fails
        side_effects = []
        for _ in range(4):  # 1 initial + 3 retries
            side_effects.append(
                MagicMock(returncode=0, stdout=_gh_view_output(from_labels), stderr="")
            )
            side_effects.append(
                MagicMock(returncode=1, stdout="", stderr="gh: error")
            )

        with patch("services.sprint_manager.state_machine.subprocess.run",
                   side_effect=side_effects), \
             patch("services.sprint_manager.state_machine.time.sleep"):
            with self.assertRaises(TransitionError):
                transition(42, TicketState.SIT, actor="coder", repo="owner/repo")

    def test_backoff_sequence_is_1_3_7(self):
        """Backoff delays follow (1, 3, 7) seconds."""
        from_labels = ["in-progress", "sprint-25"]

        side_effects = []
        for _ in range(4):
            side_effects.append(
                MagicMock(returncode=0, stdout=_gh_view_output(from_labels), stderr="")
            )
            side_effects.append(
                MagicMock(returncode=1, stdout="", stderr="gh: error")
            )

        with patch("services.sprint_manager.state_machine.subprocess.run",
                   side_effect=side_effects), \
             patch("services.sprint_manager.state_machine.time.sleep") as mock_sleep:
            with self.assertRaises(TransitionError):
                transition(42, TicketState.SIT, actor="coder", repo="owner/repo")

        sleep_calls = [c[0][0] for c in mock_sleep.call_args_list]
        self.assertEqual(sleep_calls, [1, 3, 7])

    def test_transition_error_on_verification_failure(self):
        """TransitionError raised when label verification fails after all retries."""
        from_labels = ["in-progress", "sprint-25"]
        wrong_after = ["in-progress", "sprint-25"]  # edit didn't stick

        side_effects = []
        for _ in range(4):
            side_effects.append(
                MagicMock(returncode=0, stdout=_gh_view_output(from_labels), stderr="")
            )
            side_effects.append(
                MagicMock(returncode=0, stdout="", stderr="")  # edit ok
            )
            side_effects.append(
                MagicMock(returncode=0, stdout=_gh_view_output(wrong_after), stderr="")
            )

        with patch("services.sprint_manager.state_machine.subprocess.run",
                   side_effect=side_effects), \
             patch("services.sprint_manager.state_machine.time.sleep"):
            with self.assertRaises(TransitionError):
                transition(42, TicketState.SIT, actor="coder", repo="owner/repo")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

class TestLogging(unittest.TestCase):
    def test_logs_transition_with_actor_and_note(self):
        from_labels = ["in-progress", "sprint-25"]
        after_labels = ["SIT", "sprint-25"]

        side_effects = [
            MagicMock(returncode=0, stdout=_gh_view_output(from_labels), stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout=_gh_view_output(after_labels), stderr=""),
        ]

        with patch("services.sprint_manager.state_machine.subprocess.run",
                   side_effect=side_effects), \
             patch("services.sprint_manager.state_machine._LOG_AVAILABLE", True), \
             patch("services.sprint_manager.state_machine._log") as mock_log:
            transition(
                42, TicketState.SIT,
                actor="coder-bot", note="sprint-28 run",
                repo="owner/repo",
            )

        mock_log.info.assert_called_once()
        _, kwargs = mock_log.info.call_args[0], mock_log.info.call_args[1]
        self.assertEqual(kwargs.get("actor"), "coder-bot")
        self.assertEqual(kwargs.get("note"), "sprint-28 run")
        self.assertEqual(kwargs.get("issue_num"), 42)
        self.assertFalse(kwargs.get("noop", False))

    def test_logs_noop_with_noop_flag(self):
        current = list(STATE_LABELS[TicketState.SIT]) + ["sprint-25"]
        side_effects = [
            MagicMock(returncode=0, stdout=_gh_view_output(current), stderr=""),
        ]

        with patch("services.sprint_manager.state_machine.subprocess.run",
                   side_effect=side_effects), \
             patch("services.sprint_manager.state_machine._LOG_AVAILABLE", True), \
             patch("services.sprint_manager.state_machine._log") as mock_log:
            transition(42, TicketState.SIT, actor="tester", repo="owner/repo")

        mock_log.info.assert_called_once()
        kwargs = mock_log.info.call_args[1]
        self.assertTrue(kwargs.get("noop"))


# ---------------------------------------------------------------------------
# Importability
# ---------------------------------------------------------------------------

class TestImportability(unittest.TestCase):
    def test_importable_without_circular_imports(self):
        import importlib
        mod = importlib.import_module("services.sprint_manager.state_machine")
        self.assertTrue(hasattr(mod, "transition"))
        self.assertTrue(hasattr(mod, "TicketState"))
        self.assertTrue(hasattr(mod, "STATE_LABELS"))
        self.assertTrue(hasattr(mod, "STATUS_LABELS"))
        self.assertTrue(hasattr(mod, "TransitionError"))


if __name__ == "__main__":
    unittest.main()
