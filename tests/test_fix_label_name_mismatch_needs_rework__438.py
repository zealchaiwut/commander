"""Tests for issue #438: label name mismatch fix — 'need-rework' → 'needs-rework'."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest
from services.sprint_manager.state_machine import STATE_LABELS, STATUS_LABELS, TicketState


class TestNeedsReworkLabelName(unittest.TestCase):
    """AC1: STATE_LABELS[NEEDS_REWORK] contains 'needs-rework', not 'need-rework'."""

    def test_needs_rework_label_is_needs_rework_with_s(self):
        label_set = STATE_LABELS[TicketState.NEEDS_REWORK]
        self.assertIn("needs-rework", label_set)

    def test_needs_rework_label_does_not_contain_typo(self):
        label_set = STATE_LABELS[TicketState.NEEDS_REWORK]
        self.assertNotIn("need-rework", label_set)

    def test_needs_rework_label_set_contains_exactly_one_label(self):
        label_set = STATE_LABELS[TicketState.NEEDS_REWORK]
        self.assertEqual(len(label_set), 1)

    def test_status_labels_contains_needs_rework_with_s(self):
        self.assertIn("needs-rework", STATUS_LABELS)

    def test_status_labels_does_not_contain_typo(self):
        self.assertNotIn("need-rework", STATUS_LABELS)


class TestTransitionTestFileConsistency(unittest.TestCase):
    """AC2: test_transition_return_value__411.py uses 'needs-rework', not 'need-rework'."""

    def test_transition_test_file_uses_correct_label(self):
        test_file = os.path.join(
            os.path.dirname(__file__), "test_transition_return_value__411.py"
        )
        with open(test_file) as f:
            content = f.read()
        self.assertNotIn(
            '"need-rework"', content,
            "test_transition_return_value__411.py still contains typo 'need-rework'"
        )
        self.assertIn(
            '"needs-rework"', content,
            "test_transition_return_value__411.py must use 'needs-rework'"
        )


if __name__ == "__main__":
    unittest.main()
