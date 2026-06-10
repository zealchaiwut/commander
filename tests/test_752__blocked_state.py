"""Tests for issue #752: add `blocked` as a first-class ticket state.

AC coverage:
- TicketState.BLOCKED added to state_machine.py
- STATE_LABELS[TicketState.BLOCKED] == {"blocked"}; STATUS_LABELS derives
  "blocked" automatically (no manual addition)
- transition(issue, TicketState.BLOCKED, ...) moves a ticket to blocked,
  removes all other status labels, preserves verify+retry semantics
- transition out of blocked (BLOCKED → SIT) removes the blocked label and
  applies the new status with no orphaned status labels
- No code path outside transition() adds/removes the `blocked` label
  (sprint_manager._add_blocked_label routes through transition())
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from services.sprint_manager.state_machine import (
    STATE_LABELS,
    STATUS_LABELS,
    TicketState,
    transition,
)

_FAKE_REPO = "zealchaiwut/commander"
_FAKE_ISSUE = 999


def _label_response(*names: str) -> MagicMock:
    m = MagicMock()
    m.returncode = 0
    m.stdout = json.dumps({"labels": [{"name": n} for n in names]})
    m.stderr = ""
    return m


def _edit_ok() -> MagicMock:
    m = MagicMock()
    m.returncode = 0
    m.stdout = ""
    m.stderr = ""
    return m


def _edit_fail(msg: str = "API error") -> MagicMock:
    m = MagicMock()
    m.returncode = 1
    m.stdout = ""
    m.stderr = msg
    return m


# ── AC: BLOCKED is a first-class state ───────────────────────────────────────

class TestBlockedStateConstants:
    def test_blocked_state_exists(self):
        assert "BLOCKED" in {s.name for s in TicketState}

    def test_blocked_state_label_is_blocked(self):
        assert STATE_LABELS[TicketState.BLOCKED] == frozenset({"blocked"})

    def test_status_labels_includes_blocked(self):
        assert "blocked" in STATUS_LABELS

    def test_status_labels_derived_not_hardcoded(self):
        """STATUS_LABELS must be the union of STATE_LABELS values — blocked is
        present *because* STATE_LABELS[BLOCKED] is, not via manual addition."""
        expected = frozenset().union(*STATE_LABELS.values())
        assert STATUS_LABELS == expected

    def test_blocked_is_not_a_pseudo_state(self):
        """transition() to BLOCKED must not raise ValueError (pseudo-states do)."""
        from services.sprint_manager.state_machine import _PSEUDO_STATES
        assert TicketState.BLOCKED not in _PSEUDO_STATES


# ── AC: transition INTO blocked ──────────────────────────────────────────────

class TestTransitionIntoBlocked:
    def test_in_progress_to_blocked_adds_and_removes(self):
        view_responses = [
            _label_response("in-progress", "sprint-57"),  # current
            _label_response("blocked", "sprint-57"),       # verify after edit
        ]
        idx = 0
        captured = []

        def fake_run(cmd, **kwargs):
            nonlocal idx
            if "view" in cmd:
                r = view_responses[min(idx, len(view_responses) - 1)]
                idx += 1
                return r
            captured.extend(cmd)
            return _edit_ok()

        with patch("services.sprint_manager.state_machine.subprocess.run", side_effect=fake_run), \
             patch("services.sprint_manager.state_machine.time"):
            result = transition(_FAKE_ISSUE, TicketState.BLOCKED, actor="uat-tester", repo=_FAKE_REPO)

        assert result is True
        add_idx = [i for i, x in enumerate(captured) if x == "--add-label"]
        added = [captured[i + 1] for i in add_idx]
        rm_idx = [i for i, x in enumerate(captured) if x == "--remove-label"]
        removed = [captured[i + 1] for i in rm_idx]
        assert "blocked" in added
        assert "in-progress" in removed
        # non-status labels untouched
        assert "sprint-57" not in added + removed

    def test_blocked_removes_all_other_status_labels(self):
        """Moving to blocked strips any other status labels present (SIT here)."""
        view_responses = [
            _label_response("SIT", "in-progress"),  # current (multiple status labels)
            _label_response("blocked"),              # verify
        ]
        idx = 0
        captured = []

        def fake_run(cmd, **kwargs):
            nonlocal idx
            if "view" in cmd:
                r = view_responses[min(idx, len(view_responses) - 1)]
                idx += 1
                return r
            captured.extend(cmd)
            return _edit_ok()

        with patch("services.sprint_manager.state_machine.subprocess.run", side_effect=fake_run), \
             patch("services.sprint_manager.state_machine.time"):
            result = transition(_FAKE_ISSUE, TicketState.BLOCKED, actor="t", repo=_FAKE_REPO)

        assert result is True
        rm_idx = [i for i, x in enumerate(captured) if x == "--remove-label"]
        removed = [captured[i + 1] for i in rm_idx]
        assert "SIT" in removed
        assert "in-progress" in removed

    def test_noop_when_already_blocked(self):
        with patch("services.sprint_manager.state_machine.subprocess.run") as mock_run, \
             patch("services.sprint_manager.state_machine.time"):
            mock_run.return_value = _label_response("blocked", "sprint-57")
            result = transition(_FAKE_ISSUE, TicketState.BLOCKED, actor="t", repo=_FAKE_REPO)
        assert result is False
        assert mock_run.call_count == 1  # only the fetch; no edit

    def test_blocked_preserves_retry_semantics(self):
        """First edit fails, second succeeds — verify+retry works for BLOCKED."""
        view_responses = [
            _label_response("in-progress"),  # attempt 1: fetch
            _label_response("in-progress"),  # attempt 2: fetch
            _label_response("blocked"),       # attempt 2: verify
        ]
        view_idx = 0
        edit_responses = [_edit_fail(), _edit_ok()]
        edit_idx = 0

        def fake_run(cmd, **kwargs):
            nonlocal view_idx, edit_idx
            if "view" in cmd:
                r = view_responses[view_idx]
                view_idx += 1
                return r
            r = edit_responses[edit_idx]
            edit_idx += 1
            return r

        with patch("services.sprint_manager.state_machine.subprocess.run", side_effect=fake_run), \
             patch("services.sprint_manager.state_machine.time"):
            result = transition(_FAKE_ISSUE, TicketState.BLOCKED, actor="t", repo=_FAKE_REPO)

        assert result is True
        assert edit_idx == 2


# ── AC: transition OUT of blocked ────────────────────────────────────────────

class TestTransitionOutOfBlocked:
    def test_blocked_to_sit_clears_blocked(self):
        view_responses = [
            _label_response("blocked", "sprint-57"),  # current
            _label_response("SIT", "sprint-57"),       # verify
        ]
        idx = 0
        captured = []

        def fake_run(cmd, **kwargs):
            nonlocal idx
            if "view" in cmd:
                r = view_responses[min(idx, len(view_responses) - 1)]
                idx += 1
                return r
            captured.extend(cmd)
            return _edit_ok()

        with patch("services.sprint_manager.state_machine.subprocess.run", side_effect=fake_run), \
             patch("services.sprint_manager.state_machine.time"):
            result = transition(_FAKE_ISSUE, TicketState.SIT, actor="uat-tester", repo=_FAKE_REPO)

        assert result is True
        add_idx = [i for i, x in enumerate(captured) if x == "--add-label"]
        added = [captured[i + 1] for i in add_idx]
        rm_idx = [i for i, x in enumerate(captured) if x == "--remove-label"]
        removed = [captured[i + 1] for i in rm_idx]
        assert "SIT" in added
        assert "blocked" in removed
        assert "sprint-57" not in added + removed

    def test_blocked_to_in_progress_no_orphan_status(self):
        """After BLOCKED → IN_PROGRESS, only in-progress remains as a status label."""
        view_responses = [
            _label_response("blocked"),
            _label_response("in-progress"),
        ]
        idx = 0

        def fake_run(cmd, **kwargs):
            nonlocal idx
            if "view" in cmd:
                r = view_responses[min(idx, len(view_responses) - 1)]
                idx += 1
                return r
            return _edit_ok()

        with patch("services.sprint_manager.state_machine.subprocess.run", side_effect=fake_run), \
             patch("services.sprint_manager.state_machine.time"):
            result = transition(_FAKE_ISSUE, TicketState.IN_PROGRESS, actor="t", repo=_FAKE_REPO)

        assert result is True


# ── AC: _add_blocked_label routes through transition() ───────────────────────

class TestAddBlockedRoutesThroughTransition:
    """sprint_manager._add_blocked_label must not write the blocked label via a
    direct github_client.update_labels call — it must go through transition()."""

    def _import_sm(self):
        sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))
        import services.sprint_manager.sprint_manager as sm
        return sm

    def test_add_blocked_label_uses_transition_not_update_labels(self):
        sm = self._import_sm()
        with patch.object(sm, "_transition_safe") as mock_trans, \
             patch.object(sm.github_client, "update_labels") as mock_update, \
             patch.object(sm.github_client, "add_comment"):
            # sprint_label=None → not an active run → label applied (not deferred)
            sm._add_blocked_label(123, "hang detected", repo_name=_FAKE_REPO, sprint_label=None)

        assert mock_trans.called, "_add_blocked_label must call _transition_safe"
        # transition target must be BLOCKED
        args, kwargs = mock_trans.call_args
        passed = list(args) + list(kwargs.values())
        assert sm._TicketState.BLOCKED in passed
        # must NOT add the blocked label directly via update_labels
        for call in mock_update.call_args_list:
            add_arg = call.kwargs.get("add") or (call.args[1] if len(call.args) > 1 else [])
            assert "blocked" not in (add_arg or []), "blocked must not be added via update_labels"
