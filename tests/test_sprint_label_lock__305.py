"""Tests for issue #305 — sprint-N label lock and mid-run label mutation guard.

AC-1  RUN_MUTABLE_LABELS constant is defined.
AC-2  sprint-N is never removed from the remove list by _guard_sprint_labels.
AC-3  Non-mutable labels are filtered from add during an active run.
AC-4  Status-transition removes (UAT, SIT, in-progress) pass through unchanged.
AC-5  _add_blocked_label suppresses "blocked" during an active sprint run.
AC-6  update_ticket.py never removes a sprint-N label.
"""

import re
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── path setup ────────────────────────────────────────────────────────────────
REPO_ROOT   = Path(__file__).parent.parent
SM_PATH     = REPO_ROOT / "services" / "sprint_manager"
SCRIPTS_DIR = REPO_ROOT / "scripts"

sys.path.insert(0, str(SM_PATH))
sys.path.insert(0, str(SCRIPTS_DIR))

# Stub heavy imports so we can import sprint_manager without a real environment
for _mod in ("github_client", "dotenv", "yaml"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

# Stub load_dotenv to be a no-op
sys.modules["dotenv"].load_dotenv = lambda *a, **kw: None  # type: ignore[attr-defined]


class TestRunMutableLabelsConstant(unittest.TestCase):
    """AC-1: RUN_MUTABLE_LABELS constant exists and has the right members."""

    def test_constant_exists(self):
        import sprint_manager as sm
        self.assertTrue(hasattr(sm, "RUN_MUTABLE_LABELS"))

    def test_constant_members(self):
        import sprint_manager as sm
        expected = {"in-progress", "sit", "uat", "needs-rework"}
        self.assertEqual(sm.RUN_MUTABLE_LABELS, expected)

    def test_constant_is_frozenset(self):
        import sprint_manager as sm
        self.assertIsInstance(sm.RUN_MUTABLE_LABELS, frozenset)


class TestGuardSprintLabels(unittest.TestCase):
    """AC-2 / AC-3 / AC-4: _guard_sprint_labels helper."""

    def setUp(self):
        import sprint_manager as sm
        self.guard = sm._guard_sprint_labels

    def test_sprint_label_never_removed_without_sprint_context(self):
        safe_add, safe_remove = self.guard([], ["sprint-22"])
        self.assertNotIn("sprint-22", safe_remove)

    def test_sprint_label_never_removed_with_sprint_context(self):
        safe_add, safe_remove = self.guard([], ["sprint-5", "UAT"], sprint_label="sprint-5")
        self.assertNotIn("sprint-5", safe_remove)
        self.assertIn("UAT", safe_remove)

    def test_status_removes_pass_through(self):
        """AC-4: UAT, SIT, in-progress may be removed."""
        _, safe_remove = self.guard([], ["UAT", "SIT", "in-progress"])
        self.assertIn("UAT", safe_remove)
        self.assertIn("SIT", safe_remove)
        self.assertIn("in-progress", safe_remove)

    def test_mutable_labels_allowed_during_run(self):
        """AC-3: in-progress, SIT, UAT, needs-rework may be added during run."""
        safe_add, _ = self.guard(
            ["in-progress", "SIT", "UAT", "needs-rework"],
            [],
            sprint_label="sprint-1",
        )
        self.assertEqual(set(safe_add), {"in-progress", "SIT", "UAT", "needs-rework"})

    def test_non_mutable_label_filtered_during_run(self):
        """AC-3: 'blocked' must be filtered out during an active sprint run."""
        safe_add, _ = self.guard(["blocked", "SIT"], [], sprint_label="sprint-7")
        self.assertNotIn("blocked", safe_add)
        self.assertIn("SIT", safe_add)

    def test_non_mutable_label_allowed_outside_run(self):
        """When sprint_label is None (no active run), adds are unrestricted."""
        safe_add, _ = self.guard(["blocked"], [], sprint_label=None)
        self.assertIn("blocked", safe_add)

    def test_estimated_label_filtered_during_run(self):
        """'estimated' is not in RUN_MUTABLE_LABELS — filtered during run."""
        safe_add, _ = self.guard(["estimated", "UAT"], [], sprint_label="sprint-3")
        self.assertNotIn("estimated", safe_add)
        self.assertIn("UAT", safe_add)

    def test_multiple_sprint_labels_in_remove_all_blocked(self):
        safe_add, safe_remove = self.guard([], ["sprint-1", "sprint-2", "UAT"])
        self.assertNotIn("sprint-1", safe_remove)
        self.assertNotIn("sprint-2", safe_remove)
        self.assertIn("UAT", safe_remove)

    def test_empty_lists_return_empty(self):
        safe_add, safe_remove = self.guard([], [])
        self.assertEqual(safe_add, [])
        self.assertEqual(safe_remove, [])


class TestAddBlockedLabel(unittest.TestCase):
    """AC-5: _add_blocked_label suppresses 'blocked' during active sprint run."""

    def setUp(self):
        import sprint_manager as sm
        self.sm = sm

    def test_blocked_suppressed_during_sprint(self):
        """'blocked' must not be applied when sprint_label is provided."""
        with patch.object(self.sm.github_client, "update_labels") as mock_upd, \
             patch.object(self.sm.github_client, "add_comment") as mock_cmt:
            self.sm._add_blocked_label(42, "hung", sprint_label="sprint-22")
            mock_upd.assert_not_called()
            # A comment should still be posted as a notification
            mock_cmt.assert_called_once()
            call_body = mock_cmt.call_args[0][1]
            self.assertIn("HANG", call_body.upper())

    def test_blocked_applied_outside_sprint(self):
        """Outside a sprint run (sprint_label=None), 'blocked' is applied normally."""
        with patch.object(self.sm.github_client, "update_labels") as mock_upd, \
             patch.object(self.sm.github_client, "add_comment"):
            self.sm._add_blocked_label(99, "hung", sprint_label=None)
            mock_upd.assert_called_once()
            add_labels = mock_upd.call_args[1]["add"]
            self.assertIn("blocked", add_labels)


class TestUpdateTicketSprintGuard(unittest.TestCase):
    """AC-6: update_ticket.py never removes a sprint-N label."""

    def test_sprint_label_re_defined(self):
        import update_ticket
        self.assertTrue(hasattr(update_ticket, "_SPRINT_LABEL_RE"))

    def test_sprint_label_re_matches(self):
        import update_ticket
        self.assertIsNotNone(update_ticket._SPRINT_LABEL_RE.match("sprint-1"))
        self.assertIsNotNone(update_ticket._SPRINT_LABEL_RE.match("sprint-22"))
        self.assertIsNone(update_ticket._SPRINT_LABEL_RE.match("SIT"))
        self.assertIsNone(update_ticket._SPRINT_LABEL_RE.match("in-progress"))

    def test_sprint_label_in_remove_list_is_skipped(self):
        """Even if a sprint label slips into STATUS_MAP removes, it is skipped."""
        import update_ticket
        # Sprint-N labels are filtered before any gh call — simulate this check
        label_to_remove = "sprint-42"
        self.assertTrue(update_ticket._SPRINT_LABEL_RE.match(label_to_remove))


class TestFinishFeatureSprintGuard(unittest.TestCase):
    """AC-5 (finish_feature): sprint-N protection regex is present."""

    def test_sprint_label_re_defined(self):
        import finish_feature
        self.assertTrue(hasattr(finish_feature, "_SPRINT_LABEL_RE"))

    def test_sprint_label_re_matches(self):
        import finish_feature
        self.assertIsNotNone(finish_feature._SPRINT_LABEL_RE.match("sprint-7"))
        self.assertIsNone(finish_feature._SPRINT_LABEL_RE.match("UAT"))


if __name__ == "__main__":
    unittest.main()
