"""Tests for issue #508: atomic transition() as sole label-writer.

AC coverage:
- state_machine.py defines TicketState, STATE_LABELS, STATUS_LABELS, transition()
- transition() computes diff correctly and issues one gh edit call
  (the post-edit verify re-fetch was removed in issue #755 — see
  test_755__write_through_transition.py for the 2-call assertions)
- BACKLOG/DONE are pseudo-states; transition() rejects them with ValueError
- Retry: succeeds on second attempt after one failure
- All retries exhausted → TransitionError
- Diff math: [sprint-25, in-progress, estimated] → SIT removes only in-progress,
  adds SIT, leaves sprint-25/estimated untouched
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from services.sprint_manager.state_machine import (
    STATE_LABELS,
    STATUS_LABELS,
    TicketState,
    TransitionError,
    transition,
)

_FAKE_REPO = "zealchaiwut/commander"
_FAKE_ISSUE = 999


def _label_response(*names: str) -> MagicMock:
    """Fake subprocess.run result for gh api repos/.../issues/N (REST)."""
    m = MagicMock()
    m.returncode = 0
    m.stdout = json.dumps({"labels": [{"name": n} for n in names]})
    m.stderr = ""
    return m


def _is_issue_fetch(cmd: list) -> bool:
    return "api" in cmd and any(isinstance(a, str) and "/issues/" in a for a in cmd)


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


# ── importability & constants ──────────────────────────────────────────────────

class TestConstants:
    def test_ticket_state_has_all_members(self):
        names = {s.name for s in TicketState}
        assert names == {"BACKLOG", "QUEUED", "IN_PROGRESS", "SIT", "UAT", "NEEDS_REWORK", "BLOCKED", "DONE", "UAT_APPROVED"}

    def test_pseudo_states_have_empty_labels(self):
        assert STATE_LABELS[TicketState.BACKLOG] == frozenset()
        assert STATE_LABELS[TicketState.DONE] == frozenset()

    def test_status_labels_is_union_of_non_empty_states(self):
        expected = frozenset().union(
            *[v for k, v in STATE_LABELS.items() if v]
        )
        assert STATUS_LABELS == expected

    def test_status_labels_excludes_sprint_and_estimated(self):
        assert "sprint-25" not in STATUS_LABELS
        assert "estimated" not in STATUS_LABELS

    def test_state_labels_covers_all_states(self):
        for state in TicketState:
            assert state in STATE_LABELS

    def test_backlog_done_not_in_status_labels(self):
        assert not (STATE_LABELS[TicketState.BACKLOG] & STATUS_LABELS)
        assert not (STATE_LABELS[TicketState.DONE] & STATUS_LABELS)


# ── pseudo-state rejection ─────────────────────────────────────────────────────

class TestPseudoStateRejection:
    def test_transition_to_backlog_raises_value_error(self):
        with pytest.raises(ValueError):
            transition(_FAKE_ISSUE, TicketState.BACKLOG, actor="test", repo=_FAKE_REPO)

    def test_transition_to_done_raises_value_error(self):
        with pytest.raises(ValueError):
            transition(_FAKE_ISSUE, TicketState.DONE, actor="test", repo=_FAKE_REPO)

    def test_value_error_message_mentions_pseudo_state(self):
        with pytest.raises(ValueError, match="pseudo-state"):
            transition(_FAKE_ISSUE, TicketState.BACKLOG, actor="test", repo=_FAKE_REPO)


# ── no-op when already in target state ────────────────────────────────────────

class TestNoOp:
    def test_noop_when_already_in_progress(self):
        # fetch returns in-progress, target is IN_PROGRESS → no edit call
        with patch("services.sprint_manager.state_machine.subprocess.run") as mock_run, \
             patch("services.sprint_manager.state_machine.time"):
            mock_run.return_value = _label_response("in-progress", "sprint-37")
            result = transition(
                _FAKE_ISSUE, TicketState.IN_PROGRESS, actor="test", repo=_FAKE_REPO
            )
        assert result is False
        # only the fetch call; no edit call
        assert mock_run.call_count == 1

    def test_noop_when_already_sit(self):
        with patch("services.sprint_manager.state_machine.subprocess.run") as mock_run, \
             patch("services.sprint_manager.state_machine.time"):
            mock_run.return_value = _label_response("SIT", "estimated")
            result = transition(
                _FAKE_ISSUE, TicketState.SIT, actor="test", repo=_FAKE_REPO
            )
        assert result is False

    def test_noop_when_already_uat(self):
        with patch("services.sprint_manager.state_machine.subprocess.run") as mock_run, \
             patch("services.sprint_manager.state_machine.time"):
            mock_run.return_value = _label_response("UAT")
            result = transition(
                _FAKE_ISSUE, TicketState.UAT, actor="test", repo=_FAKE_REPO
            )
        assert result is False


# ── valid state transitions ────────────────────────────────────────────────────

class TestValidTransitions:
    """Every valid state-to-state transition returns True and issues one edit call."""

    def _do_transition(self, from_labels: list[str], target: TicketState) -> tuple[bool, list]:
        target_lbl = list(STATE_LABELS[target])[0] if STATE_LABELS[target] else None
        calls_made = []

        def fake_run(cmd, **kwargs):
            calls_made.append(list(cmd))
            if _is_issue_fetch(cmd):
                # First call: current labels; after edit: labels match target
                if len([c for c in calls_made if _is_issue_fetch(c)]) == 1:
                    return _label_response(*from_labels)
                else:
                    return _label_response(*([target_lbl] if target_lbl else []))
            else:
                return _edit_ok()

        with patch("services.sprint_manager.state_machine.subprocess.run", side_effect=fake_run), \
             patch("services.sprint_manager.state_machine.time"):
            result = transition(
                _FAKE_ISSUE, target, actor="test-actor", repo=_FAKE_REPO
            )
        return result, calls_made

    def test_queued_to_in_progress(self):
        result, calls = self._do_transition(["backlog"], TicketState.IN_PROGRESS)
        assert result is True
        edit_calls = [c for c in calls if "edit" in c]
        assert len(edit_calls) == 1
        assert "--add-label" in edit_calls[0]
        assert "in-progress" in edit_calls[0]
        assert "--remove-label" in edit_calls[0]
        assert "backlog" in edit_calls[0]

    def test_in_progress_to_sit(self):
        result, calls = self._do_transition(["in-progress"], TicketState.SIT)
        assert result is True
        edit_calls = [c for c in calls if "edit" in c]
        assert len(edit_calls) == 1
        assert "SIT" in edit_calls[0]

    def test_sit_to_uat(self):
        result, calls = self._do_transition(["SIT"], TicketState.UAT)
        assert result is True
        edit_calls = [c for c in calls if "edit" in c]
        assert len(edit_calls) == 1
        assert "UAT" in edit_calls[0]

    def test_uat_to_needs_rework(self):
        result, calls = self._do_transition(["UAT"], TicketState.NEEDS_REWORK)
        assert result is True
        edit_calls = [c for c in calls if "edit" in c]
        assert len(edit_calls) == 1
        assert "needs-rework" in edit_calls[0]

    def test_needs_rework_to_queued(self):
        result, calls = self._do_transition(["needs-rework"], TicketState.QUEUED)
        assert result is True
        edit_calls = [c for c in calls if "edit" in c]
        assert len(edit_calls) == 1
        assert "backlog" in edit_calls[0]

    def test_returns_true_not_truthy(self):
        result, _ = self._do_transition(["in-progress"], TicketState.SIT)
        assert result is True


# ── exactly one edit call per attempt ────────────────────────────────────────

class TestSingleEditCall:
    """transition() must combine add/remove into exactly one gh issue edit call."""

    def test_one_edit_call_on_success(self):
        view_responses = [
            _label_response("in-progress", "sprint-25", "estimated"),
            _label_response("SIT", "sprint-25", "estimated"),
        ]
        call_idx = 0

        def fake_run(cmd, **kwargs):
            nonlocal call_idx
            if _is_issue_fetch(cmd):
                resp = view_responses[min(call_idx, len(view_responses) - 1)]
                call_idx += 1
                return resp
            return _edit_ok()

        edit_calls = []
        original_fake = fake_run

        def counting_fake(cmd, **kwargs):
            if "edit" in cmd:
                edit_calls.append(cmd)
            return original_fake(cmd, **kwargs)

        with patch("services.sprint_manager.state_machine.subprocess.run", side_effect=counting_fake), \
             patch("services.sprint_manager.state_machine.time"):
            transition(_FAKE_ISSUE, TicketState.SIT, actor="test", repo=_FAKE_REPO)

        assert len(edit_calls) == 1, f"Expected 1 edit call, got {len(edit_calls)}"


# ── diff math ──────────────────────────────────────────────────────────────────

class TestDiffMath:
    """Transitioning [sprint-25, in-progress, estimated] → SIT:
    removes in-progress, adds SIT, leaves sprint-25/estimated untouched."""

    def test_diff_math_correct_labels(self):
        current_labels = ["sprint-25", "in-progress", "estimated"]

        view_call_count = 0

        def fake_run(cmd, **kwargs):
            nonlocal view_call_count
            if _is_issue_fetch(cmd):
                if view_call_count == 0:
                    view_call_count += 1
                    return _label_response(*current_labels)
                else:
                    view_call_count += 1
                    return _label_response("sprint-25", "SIT", "estimated")
            return _edit_ok()

        captured_edit_cmd = []

        def capturing_fake(cmd, **kwargs):
            if "edit" in cmd:
                captured_edit_cmd.extend(cmd)
            return fake_run(cmd, **kwargs)

        with patch("services.sprint_manager.state_machine.subprocess.run", side_effect=capturing_fake), \
             patch("services.sprint_manager.state_machine.time"):
            result = transition(
                _FAKE_ISSUE, TicketState.SIT, actor="tester", repo=_FAKE_REPO
            )

        assert result is True
        # SIT must be added
        add_idx = [i for i, x in enumerate(captured_edit_cmd) if x == "--add-label"]
        added = [captured_edit_cmd[i + 1] for i in add_idx]
        assert "SIT" in added, f"SIT not in added labels: {added}"

        # in-progress must be removed
        rm_idx = [i for i, x in enumerate(captured_edit_cmd) if x == "--remove-label"]
        removed = [captured_edit_cmd[i + 1] for i in rm_idx]
        assert "in-progress" in removed, f"in-progress not in removed labels: {removed}"

        # sprint-25 and estimated must NOT be touched
        all_label_args = added + removed
        assert "sprint-25" not in all_label_args, "sprint-25 must not be touched"
        assert "estimated" not in all_label_args, "estimated must not be touched"

    def test_diff_math_no_spurious_removes(self):
        """Transition from [SIT, sprint-25, estimated] → SIT is a no-op (no removes)."""
        with patch("services.sprint_manager.state_machine.subprocess.run") as mock_run, \
             patch("services.sprint_manager.state_machine.time"):
            mock_run.return_value = _label_response("SIT", "sprint-25", "estimated")
            result = transition(
                _FAKE_ISSUE, TicketState.SIT, actor="test", repo=_FAKE_REPO
            )
        assert result is False
        # no edit call should happen
        calls_with_edit = [
            c for c in mock_run.call_args_list
            if "edit" in c.args[0]
        ]
        assert len(calls_with_edit) == 0


# ── retry behavior ─────────────────────────────────────────────────────────────

class TestRetry:
    def test_retry_succeeds_on_second_attempt(self):
        """First edit fails (returncode=1), second succeeds."""
        view_responses = [
            _label_response("in-progress"),   # attempt 1: fetch current
            # (no verify call on attempt 1 — edit failed)
            _label_response("in-progress"),   # attempt 2: fetch current
            _label_response("SIT"),           # attempt 2: verify after
        ]
        view_idx = 0
        edit_responses = [_edit_fail(), _edit_ok()]
        edit_idx = 0

        def fake_run(cmd, **kwargs):
            nonlocal view_idx, edit_idx
            if _is_issue_fetch(cmd):
                r = view_responses[view_idx]
                view_idx += 1
                return r
            else:
                r = edit_responses[edit_idx]
                edit_idx += 1
                return r

        sleep_calls = []

        with patch("services.sprint_manager.state_machine.subprocess.run", side_effect=fake_run), \
             patch("services.sprint_manager.state_machine.time") as mock_time:
            mock_time.sleep.side_effect = lambda s: sleep_calls.append(s)
            result = transition(
                _FAKE_ISSUE, TicketState.SIT, actor="test", repo=_FAKE_REPO
            )

        assert result is True
        assert edit_idx == 2, f"Expected 2 edit calls, got {edit_idx}"
        # backoff sleep was called once (after first failure)
        assert len(sleep_calls) == 1
        assert sleep_calls[0] == 1  # first backoff is 1s

    def test_all_retries_exhausted_raises_transition_error(self):
        """All 4 attempts fail → TransitionError."""
        def fake_run(cmd, **kwargs):
            if _is_issue_fetch(cmd):
                return _label_response("in-progress")
            return _edit_fail("network down")

        with patch("services.sprint_manager.state_machine.subprocess.run", side_effect=fake_run), \
             patch("services.sprint_manager.state_machine.time"):
            with pytest.raises(TransitionError):
                transition(
                    _FAKE_ISSUE, TicketState.SIT, actor="test", repo=_FAKE_REPO
                )

    def test_all_retries_exhausted_sleep_sequence(self):
        """Sleep is called with backoff (1, 3, 7) — 3 sleeps for 4 attempts."""
        def fake_run(cmd, **kwargs):
            if _is_issue_fetch(cmd):
                return _label_response("in-progress")
            return _edit_fail()

        sleep_calls = []

        with patch("services.sprint_manager.state_machine.subprocess.run", side_effect=fake_run), \
             patch("services.sprint_manager.state_machine.time") as mock_time:
            mock_time.sleep.side_effect = lambda s: sleep_calls.append(s)
            with pytest.raises(TransitionError):
                transition(
                    _FAKE_ISSUE, TicketState.SIT, actor="test", repo=_FAKE_REPO
                )

        assert sleep_calls == [1, 3, 7]

    def test_edit_success_returns_true_without_verify_refetch(self):
        """Issue #755: the post-edit verify re-fetch was removed. A successful
        edit returns True immediately — no second view call to re-check labels."""
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            if _is_issue_fetch(cmd):
                return _label_response("in-progress")
            return _edit_ok()

        with patch("services.sprint_manager.state_machine.subprocess.run", side_effect=fake_run), \
             patch("services.sprint_manager.state_machine.time"):
            result = transition(
                _FAKE_ISSUE, TicketState.SIT, actor="test", repo=_FAKE_REPO
            )

        assert result is True
        # Exactly one view (pre-edit fetch) and one edit — no verify re-fetch.
        assert len([c for c in calls if _is_issue_fetch(c)]) == 1
        assert len([c for c in calls if "edit" in c]) == 1

    def test_no_transition_error_after_successful_edit(self):
        """Issue #755: with the verify loop gone, a successful edit never raises
        TransitionError even if the on-GitHub state would not be re-confirmed."""
        def fake_run(cmd, **kwargs):
            if _is_issue_fetch(cmd):
                return _label_response("in-progress")
            return _edit_ok()

        with patch("services.sprint_manager.state_machine.subprocess.run", side_effect=fake_run), \
             patch("services.sprint_manager.state_machine.time"):
            result = transition(
                _FAKE_ISSUE, TicketState.SIT, actor="test", repo=_FAKE_REPO
            )
        assert result is True


# ── logging ────────────────────────────────────────────────────────────────────

class TestLogging:
    def test_transition_logs_actor_and_note(self, capsys):
        view_responses = [
            _label_response("in-progress"),
            _label_response("SIT"),
        ]
        view_idx = 0

        def fake_run(cmd, **kwargs):
            nonlocal view_idx
            if _is_issue_fetch(cmd):
                r = view_responses[view_idx]
                view_idx += 1
                return r
            return _edit_ok()

        with patch("services.sprint_manager.state_machine.subprocess.run", side_effect=fake_run), \
             patch("services.sprint_manager.state_machine.time"), \
             patch("services.sprint_manager.state_machine._LOG_AVAILABLE", False):
            transition(
                _FAKE_ISSUE, TicketState.SIT,
                actor="tester-bot", note="post-merge",
                repo=_FAKE_REPO,
            )

        captured = capsys.readouterr()
        assert "tester-bot" in captured.out
        assert "post-merge" in captured.out

    def test_noop_logs_noop_prefix(self, capsys):
        with patch("services.sprint_manager.state_machine.subprocess.run") as mock_run, \
             patch("services.sprint_manager.state_machine.time"), \
             patch("services.sprint_manager.state_machine._LOG_AVAILABLE", False):
            mock_run.return_value = _label_response("SIT")
            transition(
                _FAKE_ISSUE, TicketState.SIT, actor="test", repo=_FAKE_REPO
            )

        captured = capsys.readouterr()
        assert "[noop]" in captured.out


# ── importability from sprint_manager ─────────────────────────────────────────

class TestImportability:
    def test_importable_from_state_machine_module(self):
        from services.sprint_manager.state_machine import (
            STATE_LABELS,
            STATUS_LABELS,
            TicketState,
            TransitionError,
            transition,
        )
        assert TicketState is not None
        assert callable(transition)

    def test_importable_via_services_sprint_manager_package(self):
        import services.sprint_manager.state_machine as sm
        assert hasattr(sm, "TicketState")
        assert hasattr(sm, "STATE_LABELS")
        assert hasattr(sm, "STATUS_LABELS")
        assert hasattr(sm, "transition")
        assert hasattr(sm, "TransitionError")

    def test_no_circular_import_from_sprint_manager(self):
        """Importing sprint_manager must not blow up after state_machine exists."""
        import importlib
        try:
            importlib.import_module("services.sprint_manager.state_machine")
        except ImportError as e:
            pytest.fail(f"Circular import or missing dep: {e}")
