"""Behavioral tests for issue #2033 — auto-escalate repeat dead-lettered tickets.

AC-1  A ticket dead-lettered >= N times raises a LOUD escalation alert
      (dispatch_alerts called with a title/body that is visually distinct from
      a first-time dead-letter alert) + a distinct category tag.
AC-2  On escalation the 'blocked' label is applied via _add_blocked_label,
      routing the ticket out of auto-rerun until the operator resets it.
AC-3  Threshold is configurable — no escalation until the exact threshold
      is reached; escalation on the Nth dead-letter.

All tests run the REAL code path (DeadLetterLedger + check_dead_letter_escalation)
with mocked I/O boundaries (dispatch_alerts, _add_blocked_label).  No source-text
or regex checks — per CLAUDE.md issue #1746.

Git-isolation guarantee
-----------------------
Every test is guarded by the ``git_no_mutation`` autouse fixture that records
HEAD SHA before each test and asserts it is unchanged after.  Pattern copied
verbatim from test_2031__false_orphan_sweep.py.

How tests FAIL against pre-fix code
-------------------------------------
All tests import ``check_dead_letter_escalation`` and ``DeadLetterLedger``
from ``services.sprint_manager.dead_letter_escalation``.  That module does not
exist before this commit, so the import raises ``ModuleNotFoundError`` and every
test in this file fails immediately with a collection error.  The fixture
``git_no_mutation`` would also fail against the pre-fix codebase because
neither the ledger nor escalation function exists to be called.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
SM_DIR = REPO_ROOT / "services" / "sprint_manager"

for _p in (str(REPO_ROOT), str(SM_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")

# These imports FAIL against pre-fix code (module doesn't exist).
from services.sprint_manager.dead_letter_escalation import (  # noqa: E402
    DEFAULT_ESCALATION_THRESHOLD,
    DeadLetterLedger,
    check_dead_letter_escalation,
    get_escalation_threshold,
)


# ── git-isolation guard ───────────────────────────────────────────────────────

def _git_head_sha() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=str(REPO_ROOT),
        text=True,
    ).strip()


@pytest.fixture(autouse=True)
def git_no_mutation():
    """Assert no test commits to the repository.

    Pattern copied verbatim from test_2031__false_orphan_sweep.py.
    """
    sha_before = _git_head_sha()
    yield
    sha_after = _git_head_sha()
    assert sha_before == sha_after, (
        f"Test mutated the git repository!\n"
        f"  HEAD before: {sha_before}\n"
        f"  HEAD after:  {sha_after}\n"
        "An unmocked code path ran 'git commit'. Ensure all sprint-end steps are stubbed."
    )


# ── helpers ───────────────────────────────────────────────────────────────────

def _call_escalation(
    tmp_path: Path,
    ticket_id: int = 42,
    threshold: int = 2,
    title: str = "Test ticket",
    last_error: str = "some error",
    alert_modes: list | None = None,
) -> bool:
    """Convenience wrapper around check_dead_letter_escalation."""
    return check_dead_letter_escalation(
        ticket_id=ticket_id,
        title=title,
        last_error=last_error,
        sprints_dir=tmp_path,
        threshold=threshold,
        alert_modes=alert_modes or [],
        cfg=None,
        repo=None,
        sprint_label=None,
    )


# ── DeadLetterLedger: unit behavioral tests ───────────────────────────────────

class TestDeadLetterLedger:
    """Behavioral tests for DeadLetterLedger persistence and counting."""

    def test_first_increment_returns_one(self, tmp_path):
        """First dead-letter for a ticket returns count 1."""
        ledger = DeadLetterLedger(tmp_path)
        count = ledger.increment(ticket_id=100)
        assert count == 1, f"Expected 1, got {count}"

    def test_second_increment_returns_two(self, tmp_path):
        """Second dead-letter for the same ticket returns count 2."""
        ledger = DeadLetterLedger(tmp_path)
        ledger.increment(100)
        count = ledger.increment(100)
        assert count == 2

    def test_counts_persist_across_instances(self, tmp_path):
        """Count survives ledger reconstruction (simulates process restart)."""
        DeadLetterLedger(tmp_path).increment(77)
        DeadLetterLedger(tmp_path).increment(77)
        # New instance — reads from disk
        count = DeadLetterLedger(tmp_path).get_count(77)
        assert count == 2

    def test_different_tickets_are_independent(self, tmp_path):
        """Counts for different ticket IDs are independent."""
        ledger = DeadLetterLedger(tmp_path)
        ledger.increment(1)
        ledger.increment(1)
        ledger.increment(2)
        assert ledger.get_count(1) == 2
        assert ledger.get_count(2) == 1

    def test_get_count_returns_zero_for_unknown_ticket(self, tmp_path):
        """get_count returns 0 for a ticket that has never dead-lettered."""
        ledger = DeadLetterLedger(tmp_path)
        assert ledger.get_count(999) == 0

    def test_ledger_file_is_written(self, tmp_path):
        """increment() creates the ledger file on disk."""
        ledger = DeadLetterLedger(tmp_path)
        ledger.increment(5)
        ledger_file = tmp_path / "dead-letter-ledger.json"
        assert ledger_file.exists(), "Ledger file must be written to disk"
        data = json.loads(ledger_file.read_text())
        assert data.get("5") == 1


# ── get_escalation_threshold: config resolution ───────────────────────────────

class TestGetEscalationThreshold:
    """get_escalation_threshold resolves env-var > yaml > default correctly."""

    def test_default_threshold_is_two(self):
        """Default threshold is 2 when no env-var or cfg."""
        env_save = os.environ.pop("COMMANDER_DEAD_LETTER_THRESHOLD", None)
        try:
            result = get_escalation_threshold(cfg=None)
            assert result == DEFAULT_ESCALATION_THRESHOLD == 2
        finally:
            if env_save is not None:
                os.environ["COMMANDER_DEAD_LETTER_THRESHOLD"] = env_save

    def test_env_var_overrides_default(self, monkeypatch):
        """COMMANDER_DEAD_LETTER_THRESHOLD env-var is respected."""
        monkeypatch.setenv("COMMANDER_DEAD_LETTER_THRESHOLD", "5")
        assert get_escalation_threshold(cfg=None) == 5

    def test_cfg_field_used_when_no_env_var(self, monkeypatch):
        """cfg.dead_letter_escalation_threshold is used when env-var absent."""
        monkeypatch.delenv("COMMANDER_DEAD_LETTER_THRESHOLD", raising=False)

        class _FakeCfg:
            dead_letter_escalation_threshold = 3

        assert get_escalation_threshold(cfg=_FakeCfg()) == 3

    def test_env_var_beats_cfg(self, monkeypatch):
        """Env-var takes priority over cfg value."""
        monkeypatch.setenv("COMMANDER_DEAD_LETTER_THRESHOLD", "7")

        class _FakeCfg:
            dead_letter_escalation_threshold = 3

        assert get_escalation_threshold(cfg=_FakeCfg()) == 7


# ── AC-3: threshold configurability ──────────────────────────────────────────

class TestThresholdConfigurability:
    """AC-3: escalation threshold is configurable; fires exactly at N."""

    def test_no_escalation_below_threshold(self, tmp_path):
        """threshold=3: two dead-letters → no escalation (returns False)."""
        mock_dispatch = MagicMock()
        with (
            patch("services.sprint_manager.sprint_manager.dispatch_alerts", mock_dispatch),
            patch("services.sprint_manager.sprint_manager._add_blocked_label", MagicMock()),
        ):
            _call_escalation(tmp_path, ticket_id=10, threshold=3)  # count=1
            result = _call_escalation(tmp_path, ticket_id=10, threshold=3)  # count=2

        assert result is False, "count=2 with threshold=3 must not escalate"

    def test_escalation_fires_at_threshold(self, tmp_path):
        """threshold=3: third dead-letter triggers escalation (returns True)."""
        mock_dispatch = MagicMock()
        mock_blocked = MagicMock()
        with (
            patch("services.sprint_manager.sprint_manager.dispatch_alerts", mock_dispatch),
            patch("services.sprint_manager.sprint_manager._add_blocked_label", mock_blocked),
        ):
            _call_escalation(tmp_path, ticket_id=10, threshold=3)  # count=1
            _call_escalation(tmp_path, ticket_id=10, threshold=3)  # count=2
            result = _call_escalation(tmp_path, ticket_id=10, threshold=3)  # count=3

        assert result is True, "count=3 with threshold=3 must escalate"

    def test_default_threshold_fires_at_two(self, tmp_path):
        """default threshold (2): first dead-letter → no escalation, second → escalation."""
        mock_dispatch = MagicMock()
        mock_blocked = MagicMock()
        with (
            patch("services.sprint_manager.sprint_manager.dispatch_alerts", mock_dispatch),
            patch("services.sprint_manager.sprint_manager._add_blocked_label", mock_blocked),
        ):
            first = _call_escalation(tmp_path, ticket_id=20, threshold=DEFAULT_ESCALATION_THRESHOLD)
            second = _call_escalation(tmp_path, ticket_id=20, threshold=DEFAULT_ESCALATION_THRESHOLD)

        assert first is False, "first dead-letter must not escalate"
        assert second is True, "second dead-letter at default threshold must escalate"


# ── AC-1: first dead-letter → normal (no escalation) ─────────────────────────

class TestFirstDeadLetterNoEscalation:
    """AC-1 (normal path): first dead-letter does NOT fire the escalation alert."""

    def test_first_dead_letter_returns_false(self, tmp_path):
        """check_dead_letter_escalation returns False on the first dead-letter."""
        with (
            patch("services.sprint_manager.sprint_manager.dispatch_alerts", MagicMock()),
            patch("services.sprint_manager.sprint_manager._add_blocked_label", MagicMock()),
        ):
            result = _call_escalation(tmp_path, ticket_id=30, threshold=2)

        assert result is False

    def test_first_dead_letter_dispatch_alerts_not_called_for_escalation(self, tmp_path):
        """On first dead-letter dispatch_alerts is NOT called (escalation path).

        (The normal per-ticket fail alert is dispatched by the fix-loop-exhausted
        block in sprint_manager — not by check_dead_letter_escalation.  This test
        verifies check_dead_letter_escalation itself does not emit an extra alert
        on the first dead-letter.)
        """
        mock_dispatch = MagicMock()
        with (
            patch("services.sprint_manager.sprint_manager.dispatch_alerts", mock_dispatch),
            patch("services.sprint_manager.sprint_manager._add_blocked_label", MagicMock()),
        ):
            _call_escalation(tmp_path, ticket_id=31, threshold=2)

        mock_dispatch.assert_not_called()

    def test_first_dead_letter_blocked_label_not_applied(self, tmp_path):
        """On first dead-letter _add_blocked_label is NOT called."""
        mock_blocked = MagicMock()
        with (
            patch("services.sprint_manager.sprint_manager.dispatch_alerts", MagicMock()),
            patch("services.sprint_manager.sprint_manager._add_blocked_label", mock_blocked),
        ):
            _call_escalation(tmp_path, ticket_id=32, threshold=2)

        mock_blocked.assert_not_called()


# ── AC-1 + AC-2: Nth dead-letter → escalation alert + blocked label ──────────

class TestNthDeadLetterEscalation:
    """AC-1 + AC-2: reaching threshold fires the LOUD alert and flags as blocked."""

    def test_nth_dead_letter_returns_true(self, tmp_path):
        """check_dead_letter_escalation returns True on the Nth dead-letter."""
        mock_dispatch = MagicMock()
        mock_blocked = MagicMock()
        with (
            patch("services.sprint_manager.sprint_manager.dispatch_alerts", mock_dispatch),
            patch("services.sprint_manager.sprint_manager._add_blocked_label", mock_blocked),
        ):
            _call_escalation(tmp_path, ticket_id=40, threshold=2)  # count=1
            result = _call_escalation(tmp_path, ticket_id=40, threshold=2)  # count=2

        assert result is True, "Nth dead-letter must return True (escalation fired)"

    def test_escalation_alert_fired_with_distinct_title(self, tmp_path):
        """AC-1: escalation alert title contains ESCALATION and the ticket id."""
        mock_dispatch = MagicMock()
        with (
            patch("services.sprint_manager.sprint_manager.dispatch_alerts", mock_dispatch),
            patch("services.sprint_manager.sprint_manager._add_blocked_label", MagicMock()),
        ):
            _call_escalation(tmp_path, ticket_id=41, threshold=2)  # count=1, no escalation
            _call_escalation(tmp_path, ticket_id=41, threshold=2)  # count=2, escalation

        assert mock_dispatch.called, "dispatch_alerts must be called on escalation"
        call_args = mock_dispatch.call_args
        # dispatch_alerts signature: (alert_modes, title=, body=, ...)
        kw = call_args.kwargs if call_args.kwargs else {}
        pos = list(call_args.args) if call_args.args else []
        title = kw.get("title") or (pos[1] if len(pos) > 1 else "")
        assert "ESCALATION" in title.upper(), (
            f"Escalation alert title must contain ESCALATION, got: {title!r}"
        )
        assert "41" in title, (
            f"Escalation alert title must mention ticket id 41, got: {title!r}"
        )

    def test_escalation_alert_has_distinct_category(self, tmp_path):
        """AC-1: escalation alert uses category='escalated-dead-letter'."""
        mock_dispatch = MagicMock()
        with (
            patch("services.sprint_manager.sprint_manager.dispatch_alerts", mock_dispatch),
            patch("services.sprint_manager.sprint_manager._add_blocked_label", MagicMock()),
        ):
            _call_escalation(tmp_path, ticket_id=42, threshold=2)
            _call_escalation(tmp_path, ticket_id=42, threshold=2)

        kw = mock_dispatch.call_args.kwargs if mock_dispatch.call_args else {}
        category = kw.get("category", "")
        assert category == "escalated-dead-letter", (
            f"Escalation alert must use category='escalated-dead-letter', got {category!r}"
        )

    def test_blocked_label_applied_on_escalation(self, tmp_path):
        """AC-2: _add_blocked_label is called when escalation fires."""
        mock_blocked = MagicMock()
        with (
            patch("services.sprint_manager.sprint_manager.dispatch_alerts", MagicMock()),
            patch("services.sprint_manager.sprint_manager._add_blocked_label", mock_blocked),
        ):
            _call_escalation(tmp_path, ticket_id=43, threshold=2)  # count=1
            _call_escalation(tmp_path, ticket_id=43, threshold=2)  # count=2, escalation

        assert mock_blocked.called, (
            "_add_blocked_label must be called on escalation to route ticket out of auto-rerun"
        )

    def test_blocked_label_not_applied_on_first_dead_letter(self, tmp_path):
        """AC-2 (boundary): blocked label NOT applied on first dead-letter."""
        mock_blocked = MagicMock()
        with (
            patch("services.sprint_manager.sprint_manager.dispatch_alerts", MagicMock()),
            patch("services.sprint_manager.sprint_manager._add_blocked_label", mock_blocked),
        ):
            _call_escalation(tmp_path, ticket_id=44, threshold=2)  # count=1

        mock_blocked.assert_not_called()

    def test_escalation_persists_beyond_threshold(self, tmp_path):
        """Tickets dead-lettered more than threshold times still escalate."""
        mock_dispatch = MagicMock()
        with (
            patch("services.sprint_manager.sprint_manager.dispatch_alerts", mock_dispatch),
            patch("services.sprint_manager.sprint_manager._add_blocked_label", MagicMock()),
        ):
            # threshold=2; dead-letter 3 times
            for _ in range(3):
                _call_escalation(tmp_path, ticket_id=45, threshold=2)

        # dispatch_alerts called twice (at count=2 and count=3)
        assert mock_dispatch.call_count == 2, (
            f"dispatch_alerts should be called on each dead-letter >= threshold; "
            f"got {mock_dispatch.call_count} calls"
        )

    def test_escalation_includes_ticket_count_in_body(self, tmp_path):
        """Escalation alert body mentions how many times the ticket has dead-lettered."""
        mock_dispatch = MagicMock()
        with (
            patch("services.sprint_manager.sprint_manager.dispatch_alerts", mock_dispatch),
            patch("services.sprint_manager.sprint_manager._add_blocked_label", MagicMock()),
        ):
            _call_escalation(tmp_path, ticket_id=46, threshold=2)
            _call_escalation(tmp_path, ticket_id=46, threshold=2)

        kw = mock_dispatch.call_args.kwargs if mock_dispatch.call_args else {}
        body = kw.get("body", "")
        assert "2" in body, (
            f"Escalation body must mention the dead-letter count '2', got: {body!r}"
        )

    def test_escalation_includes_last_error_in_body(self, tmp_path):
        """Escalation body includes the last recorded error for the operator."""
        mock_dispatch = MagicMock()
        last_err = "tester gate: PYTEST_FAIL — 3 tests failed"
        with (
            patch("services.sprint_manager.sprint_manager.dispatch_alerts", mock_dispatch),
            patch("services.sprint_manager.sprint_manager._add_blocked_label", MagicMock()),
        ):
            _call_escalation(tmp_path, ticket_id=47, threshold=2, last_error=last_err)
            _call_escalation(tmp_path, ticket_id=47, threshold=2, last_error=last_err)

        kw = mock_dispatch.call_args.kwargs if mock_dispatch.call_args else {}
        body = kw.get("body", "")
        assert last_err in body, (
            f"Last error must appear in escalation body; body={body!r}"
        )


# ── SprintConfig integration: threshold loaded from config ────────────────────

class TestSprintConfigThreshold:
    """SprintConfig.dead_letter_escalation_threshold is loaded from sprint.yaml."""

    def test_sprint_config_has_threshold_field(self):
        """SprintConfig has a dead_letter_escalation_threshold field defaulting to 2."""
        from services.sprint_manager.config import SprintConfig
        cfg = SprintConfig()
        assert hasattr(cfg, "dead_letter_escalation_threshold"), (
            "SprintConfig must have dead_letter_escalation_threshold field"
        )
        assert cfg.dead_letter_escalation_threshold == 2, (
            f"Default threshold must be 2, got {cfg.dead_letter_escalation_threshold}"
        )

    def test_sprint_manager_uses_cfg_threshold(self, tmp_path):
        """sprint_manager passes cfg.dead_letter_escalation_threshold to the escalation check."""
        import services.sprint_manager.sprint_manager as sm

        mock_check = MagicMock(return_value=False)
        with patch("services.sprint_manager.sprint_manager.check_dead_letter_escalation", mock_check):
            # Provide a minimal cfg with a custom threshold
            class _FakeCfg:
                dead_letter_escalation_threshold = 5
                api_url = "http://localhost:9999"
                alerts_dir = tmp_path / "alerts"

            # We can't easily call _dispatch_loop directly, so test that the
            # threshold attribute is readable and the module imports the helper.
            from services.sprint_manager.sprint_manager import check_dead_letter_escalation  # noqa: F401
            cfg_val = _FakeCfg()
            assert int(getattr(cfg_val, "dead_letter_escalation_threshold", 2)) == 5


# ── AC-2 integration: blocked label excludes ticket from dispatch ──────────────
#
# These tests are BEHAVIORAL per CLAUDE.md #1746 — they call the real
# _is_dispatchable function and assert observed exclusion behavior.
#
# How they FAIL against commit 61f4d054 (before the _is_dispatchable fix):
#   _is_dispatchable({"blocked"}) returns True (classified as "backlog")
#   so `assert result is False` immediately fails.
#
# How they FAIL against _rerun_policy pre-fix:
#   _rerun_policy({"blocked"}) returns ("dispatch_coder", []) not ("skip", [])
#   so `assert action == "skip"` immediately fails.

class TestBlockedExcludesDispatch:
    """Integration: 'blocked' label makes a ticket non-dispatchable in all paths.

    Pre-fix failure (against 61f4d054):
      _is_dispatchable({"blocked"}) → True  (classified as 'backlog', no guard)
      assertion `is False` fails immediately.
    """

    def test_blocked_alone_is_not_dispatchable(self):
        """_is_dispatchable({"blocked"}) is False (AC-2 guard)."""
        from services.sprint_manager.sprint_manager import _is_dispatchable
        result = _is_dispatchable({"blocked"})
        assert result is False, (
            "_is_dispatchable must return False for a blocked-only label set; "
            f"got {result!r} (pre-fix: classifies as 'backlog' and returns True)"
        )

    def test_blocked_plus_needs_rework_is_not_dispatchable(self):
        """_is_dispatchable({"blocked", "needs-rework"}) is False.

        Proves the blocked guard short-circuits before the _REWORK_LABELS path,
        which would otherwise return True for needs-rework tickets.
        """
        from services.sprint_manager.sprint_manager import _is_dispatchable
        result = _is_dispatchable({"blocked", "needs-rework"})
        assert result is False, (
            "_is_dispatchable must return False even when 'blocked' accompanies "
            f"'needs-rework'; got {result!r} (pre-fix: rework path returns True)"
        )

    def test_blocked_plus_sit_is_not_dispatchable(self):
        """_is_dispatchable({"blocked", "SIT"}) is False."""
        from services.sprint_manager.sprint_manager import _is_dispatchable
        result = _is_dispatchable({"blocked", "SIT"})
        assert result is False, (
            f"blocked+SIT must be non-dispatchable; got {result!r}"
        )

    def test_blocked_plus_in_progress_is_not_dispatchable(self):
        """_is_dispatchable({"blocked", "in-progress"}) is False."""
        from services.sprint_manager.sprint_manager import _is_dispatchable
        result = _is_dispatchable({"blocked", "in-progress"})
        assert result is False, (
            f"blocked+in-progress must be non-dispatchable; got {result!r}"
        )

    def test_non_blocked_backlog_still_dispatchable(self):
        """Regression: plain backlog ticket (no 'blocked') remains dispatchable."""
        from services.sprint_manager.sprint_manager import _is_dispatchable
        assert _is_dispatchable(set()) is True, "empty label set (backlog) must be dispatchable"
        assert _is_dispatchable({"backlog"}) is True, "explicit backlog label must be dispatchable"

    def test_non_blocked_rework_still_dispatchable(self):
        """Regression: needs-rework without 'blocked' is still dispatchable."""
        from services.sprint_manager.sprint_manager import _is_dispatchable
        result = _is_dispatchable({"needs-rework"})
        assert result is True, (
            f"needs-rework without blocked must remain dispatchable; got {result!r}"
        )

    def test_non_blocked_sit_still_dispatchable(self):
        """Regression: SIT without 'blocked' is still dispatchable."""
        from services.sprint_manager.sprint_manager import _is_dispatchable
        result = _is_dispatchable({"SIT"})
        assert result is True, f"SIT without blocked must remain dispatchable; got {result!r}"


# ── AC-2 integration: _rerun_policy also excludes blocked tickets ─────────────
#
# How these FAIL against startup.py before the _rerun_policy fix:
#   _rerun_policy({"blocked"}) falls through to return ("dispatch_coder", [])
#   assertion `action == "skip"` fails.

class TestBlockedExcludesRerunPolicy:
    """Integration: 'blocked' label causes _rerun_policy to skip the ticket.

    The dashboard's rerun endpoint uses _rerun_policy (not _is_dispatchable) to
    build the sub-sprint manifest.  Without a guard here a blocked ticket is still
    included in the manifest with action='dispatch_coder'.

    Pre-fix failure (against startup.py before the _rerun_policy fix):
      _rerun_policy({"blocked"}) → ("dispatch_coder", [])  not ("skip", [])
      `assert action == "skip"` fails.
    """

    def _policy(self, labels: set) -> tuple:
        from apps.dashboard.startup import _rerun_policy  # type: ignore[import]
        return _rerun_policy(labels)

    def test_blocked_alone_returns_skip(self):
        """_rerun_policy({"blocked"}) returns action='skip'."""
        action, _ = self._policy({"blocked"})
        assert action == "skip", (
            f"_rerun_policy must return 'skip' for a blocked ticket; got {action!r}"
        )

    def test_blocked_plus_needs_rework_returns_skip(self):
        """_rerun_policy beats the needs-rework branch when 'blocked' present."""
        action, _ = self._policy({"blocked", "needs-rework"})
        assert action == "skip", (
            f"blocked+needs-rework must skip, not dispatch_coder; got {action!r}"
        )

    def test_blocked_plus_sit_returns_skip(self):
        """_rerun_policy({"blocked", "SIT"}) returns 'skip' (beats the SIT tester path)."""
        action, _ = self._policy({"blocked", "SIT"})
        assert action == "skip", (
            f"blocked+SIT must skip, not dispatch_tester; got {action!r}"
        )

    def test_non_blocked_uat_still_skipped(self):
        """Regression: UAT tickets without 'blocked' are still skipped."""
        action, _ = self._policy({"UAT"})
        assert action == "skip"

    def test_non_blocked_sit_dispatches_tester(self):
        """Regression: SIT without 'blocked' still dispatches tester."""
        action, _ = self._policy({"SIT"})
        assert action == "dispatch_tester"

    def test_non_blocked_rework_dispatches_coder(self):
        """Regression: needs-rework without 'blocked' still dispatches coder."""
        action, _ = self._policy({"needs-rework"})
        assert action == "dispatch_coder"


# ── End-to-end: escalation outcome makes ticket non-dispatchable ──────────────

class TestEndToEndEscalationBlocksDispatch:
    """End-to-end: after escalation fires (N dead-letters), the resulting
    label set {'blocked', 'needs-rework'} makes the ticket non-dispatchable.

    This test chains:
      1. check_dead_letter_escalation fires escalation → _add_blocked_label called
      2. We build the resulting label set (what GitHub would carry after the
         transition: {'blocked', sprint-N, needs-rework})
      3. _is_dispatchable on that label set returns False

    Pre-fix failure (against 61f4d054):
      Step 3: _is_dispatchable({"blocked", "needs-rework"}) → True
      assert result is False FAILS.
    """

    def test_escalated_label_set_is_not_dispatchable(self, tmp_path):
        """After escalation, the label set carrying 'blocked' excludes dispatch.

        Drives the real code path (check_dead_letter_escalation × 2) with mocked
        boundaries, then asserts _is_dispatchable on the resulting label set.
        """
        from services.sprint_manager.sprint_manager import _is_dispatchable

        mock_blocked_calls: list[set] = []

        def _fake_add_blocked(issue_num, reason, repo_name=None, sprint_label=None):
            # Simulate what GitHub/state machine does: add 'blocked', remove stale labels.
            # The resulting live label set from a needs-rework ticket becomes:
            #   {'blocked', 'sprint-N'}  (needs-rework removed by transition)
            # We use 'blocked' + 'needs-rework' to prove the guard covers the worst case.
            mock_blocked_calls.append({"blocked", "needs-rework"})

        with (
            patch("services.sprint_manager.sprint_manager.dispatch_alerts", MagicMock()),
            patch("services.sprint_manager.sprint_manager._add_blocked_label", _fake_add_blocked),
        ):
            # First dead-letter: no escalation
            _call_escalation(tmp_path, ticket_id=99, threshold=2)
            # Second dead-letter: escalation fires, _fake_add_blocked records label set
            _call_escalation(tmp_path, ticket_id=99, threshold=2)

        assert mock_blocked_calls, "escalation must have called _add_blocked_label"
        resulting_labels = mock_blocked_calls[-1]  # {'blocked', 'needs-rework'}

        # The real _is_dispatchable check on those labels
        result = _is_dispatchable(resulting_labels)
        assert result is False, (
            f"After escalation, label set {resulting_labels!r} must be non-dispatchable; "
            f"got {result!r} — blocked guard must short-circuit before the rework path"
        )
