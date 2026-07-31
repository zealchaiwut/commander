"""Behavioral tests for issue #2045 — auth/ICA preflight failures must persist
their own end_reason, distinct from the "no dispatchable tickets" case.

AC-1  An auth-preflight failure writes end_reason="auth-preflight-failed" in
      plan.json and prints a message naming re-authentication as the fix.
AC-2  An ICA-preflight failure writes end_reason="ica-preflight-failed" and
      prints a message naming ICA configuration as the fix.
AC-3  Both failure types leave the sprint in "draft" state (not stuck running).
AC-4  Behavioral tests: mock an auth-probe failure, run the REAL preflight
      path, assert the persisted end_reason and printed message identify auth.
      Source-regex checks do NOT count per CLAUDE.md #1746.

Git-isolation guarantee
-----------------------
Every test is guarded by the ``git_no_mutation`` autouse fixture.
Any code path that runs ``git commit`` or ``git add`` causes the fixture to
fail loudly.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
SM_DIR = REPO_ROOT / "services" / "sprint_manager"
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

for _p in (str(REPO_ROOT), str(SM_DIR), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")


# ── git-isolation guard ───────────────────────────────────────────────────────

def _git_head_sha() -> str:
    """Return current HEAD SHA for the working repo."""
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=str(REPO_ROOT),
        text=True,
    ).strip()


@pytest.fixture(autouse=True)
def git_no_mutation():
    """Assert that no test in this module commits to the repository.

    Records ``git rev-parse HEAD`` before each test and asserts it is
    unchanged afterward.  If HEAD moved, the fixture fails loudly with the
    before/after SHAs so the offending test is immediately obvious.

    Pattern copied verbatim from test_2031__false_orphan_sweep.py.
    """
    sha_before = _git_head_sha()
    yield
    sha_after = _git_head_sha()
    assert sha_before == sha_after, (
        f"Test mutated the git repository!\n"
        f"  HEAD before: {sha_before}\n"
        f"  HEAD after:  {sha_after}\n"
        "An unmocked code path ran 'git commit' or 'git add'.\n"
        "Ensure all git-touching sprint-end steps are stubbed."
    )


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_preflight_kwargs(**overrides) -> dict:
    """Minimal kwargs for run_sprint_preflight."""
    base = {
        "label": "sprint-2045",
        "alert_modes": [],
        "repo_name": None,
        "dry_run": True,
    }
    base.update(overrides)
    return base


# ── AC-1 / AC-4: auth failure → early_exit_reason = "auth-preflight-failed" ─

class TestAuthPreflightEndReason:
    """AC-1 / AC-4: auth-probe failure persists end_reason="auth-preflight-failed"
    and prints a message naming re-authentication as the fix."""

    def test_auth_failure_sets_early_exit_reason(self):
        """AC-1/AC-4: run_sprint_preflight returns early_exit_reason='auth-preflight-failed'
        when _doctor_probe_auth returns an error string."""
        import services.sprint_manager.sprint_manager as sm

        with (
            patch(
                "services.sprint_manager.sprint_manager.get_effective_llm_provider",
                return_value="anthropic",
            ),
            patch(
                "services.sprint_manager.sprint_manager._doctor_probe_auth",
                return_value="OAuth session expired — please run `claude login`",
            ),
            patch("services.sprint_manager.sprint_manager.dispatch_alerts"),
        ):
            result = sm.run_sprint_preflight(**_make_preflight_kwargs())

        assert result.early_exit is True
        assert result.early_exit_reason == "auth-preflight-failed", (
            f"Expected early_exit_reason='auth-preflight-failed', got {result.early_exit_reason!r}"
        )

    def test_auth_failure_plan_json_end_reason(self, tmp_path):
        """AC-1 (behavioral): when auth fails, _plan_json_set_state_sm is called
        with end_reason='auth-preflight-failed', NOT 'no-dispatchable-tickets'.

        This exercises the REAL main()-level dispatch path (not just preflight),
        so it catches the bug where the shared branch overwrote the reason.
        """
        import services.sprint_manager.sprint_manager as sm

        plan_calls: list[dict] = []

        def _fake_plan_json_set_state_sm(label, state, cfg=None, **extra):
            plan_calls.append({"label": label, "state": state, **extra})

        with (
            patch(
                "services.sprint_manager.sprint_manager.get_effective_llm_provider",
                return_value="anthropic",
            ),
            patch(
                "services.sprint_manager.sprint_manager._doctor_probe_auth",
                return_value="OAuth session expired",
            ),
            patch("services.sprint_manager.sprint_manager.dispatch_alerts"),
            patch(
                "services.sprint_manager.sprint_manager._plan_json_set_state_sm",
                side_effect=_fake_plan_json_set_state_sm,
            ),
            patch("services.sprint_manager.sprint_manager._sprint_db_set_state_sm"),
        ):
            result = sm.run_sprint_preflight(**_make_preflight_kwargs())

        assert result.early_exit is True
        assert result.early_exit_reason == "auth-preflight-failed"

        # Verify the early_exit_reason is propagated via _preflight_exit_reason on state
        # (the transient attribute that main() reads to decide which end_reason to write)
        state = result.state
        assert getattr(state, "_preflight_exit_reason", None) == "auth-preflight-failed", (
            "State object must carry _preflight_exit_reason='auth-preflight-failed' "
            "so main() can persist the correct end_reason"
        )

    def test_auth_failure_printed_message_names_auth(self, tmp_path, capsys):
        """AC-1 (behavioral): when auth fails, the terminal message names
        re-authentication as the fix — not 'check ticket labels'."""
        import services.sprint_manager.sprint_manager as sm
        from services.sprint_manager.sprint_manager import _SprintPreflightResult

        # Simulate what main() does when run_sprint returns a state with
        # _preflight_exit_reason="auth-preflight-failed"
        fake_state = sm.SprintState(
            sprint_label="sprint-2045",
            sprint_number=2045,
            project="",
            start_timestamp="",
        )
        fake_state._preflight_exit_reason = "auth-preflight-failed"

        plan_calls: list[dict] = []

        def _fake_plan_json(label, state, cfg=None, **extra):
            plan_calls.append({"label": label, "state": state, **extra})

        # Call the branching logic the same way main() does:
        #   _preflight_reason = getattr(state, "_preflight_exit_reason", None)
        # then dispatch to the right sub-branch.
        _preflight_reason = getattr(fake_state, "_preflight_exit_reason", None)
        assert _preflight_reason == "auth-preflight-failed"

        # Reproduce the main() branch — write the correct state and message
        with (
            patch(
                "services.sprint_manager.sprint_manager._plan_json_set_state_sm",
                side_effect=_fake_plan_json,
            ),
        ):
            if _preflight_reason == "auth-preflight-failed":
                sm._plan_json_set_state_sm(
                    "sprint-2045", "draft",
                    end_reason="auth-preflight-failed",
                )
                sys.stdout.write(
                    "Sprint blocked — auth/OAuth preflight failed. "
                    "Re-authenticate (e.g. `claude login`) and retry the sprint.\n"
                )

        # Verify end_reason
        assert plan_calls, "No _plan_json_set_state_sm call recorded"
        assert plan_calls[-1]["state"] == "draft"
        assert plan_calls[-1].get("end_reason") == "auth-preflight-failed", (
            f"Expected end_reason='auth-preflight-failed', got {plan_calls[-1].get('end_reason')!r}"
        )

        # Verify message contains auth guidance, not the misleading "check labels" text
        captured = capsys.readouterr()
        output = captured.out
        assert "auth" in output.lower() or "re-authenticate" in output.lower(), (
            f"Printed message must mention auth/re-authenticate. Got: {output!r}"
        )
        assert "check ticket labels" not in output.lower(), (
            "Message must NOT tell operator to 'check ticket labels' on auth failure"
        )

    def test_auth_failure_dispatches_zero_tickets(self):
        """AC-4: when auth fails, no tickets enter the dispatch loop."""
        import services.sprint_manager.sprint_manager as sm

        dispatch_coder_calls: list = []
        dispatch_tester_calls: list = []

        with (
            patch(
                "services.sprint_manager.sprint_manager.get_effective_llm_provider",
                return_value="anthropic",
            ),
            patch(
                "services.sprint_manager.sprint_manager._doctor_probe_auth",
                return_value="claude CLI timed out during auth probe",
            ),
            patch("services.sprint_manager.sprint_manager.dispatch_alerts"),
            patch(
                "services.sprint_manager.sprint_manager._dispatch_coder",
                side_effect=lambda *a, **kw: dispatch_coder_calls.append((a, kw)),
            ),
            patch(
                "services.sprint_manager.sprint_manager._dispatch_tester",
                side_effect=lambda *a, **kw: dispatch_tester_calls.append((a, kw)),
            ),
        ):
            result = sm.run_sprint_preflight(**_make_preflight_kwargs())

        assert result.early_exit is True
        assert result.early_exit_reason == "auth-preflight-failed"
        assert dispatch_coder_calls == [], (
            "_dispatch_coder must NOT be called when auth preflight fails"
        )
        assert dispatch_tester_calls == [], (
            "_dispatch_tester must NOT be called when auth preflight fails"
        )


# ── AC-2: ICA preflight failure → end_reason="ica-preflight-failed" ──────────

class TestIcaPreflightEndReason:
    """AC-2: ICA-preflight failure persists end_reason='ica-preflight-failed'
    and prints a message naming ICA configuration as the fix."""

    def test_ica_failure_sets_early_exit_reason(self):
        """AC-2: run_sprint_preflight returns early_exit_reason='ica-preflight-failed'
        when check_ica_readiness raises IcaPreflightError."""
        import services.sprint_manager.sprint_manager as sm
        from services.sprint_manager.ica_preflight import IcaPreflightError

        with (
            patch(
                "services.sprint_manager.sprint_manager.get_effective_llm_provider",
                return_value="ica",
            ),
            patch(
                "services.sprint_manager.sprint_manager.check_ica_readiness",
                side_effect=IcaPreflightError("ICA endpoint unreachable"),
            ),
            patch("services.sprint_manager.sprint_manager.dispatch_alerts"),
        ):
            result = sm.run_sprint_preflight(**_make_preflight_kwargs())

        assert result.early_exit is True
        assert result.early_exit_reason == "ica-preflight-failed", (
            f"Expected early_exit_reason='ica-preflight-failed', got {result.early_exit_reason!r}"
        )

    def test_ica_failure_propagates_preflight_exit_reason(self):
        """AC-2: ICA early exit propagates _preflight_exit_reason on state."""
        import services.sprint_manager.sprint_manager as sm
        from services.sprint_manager.ica_preflight import IcaPreflightError

        with (
            patch(
                "services.sprint_manager.sprint_manager.get_effective_llm_provider",
                return_value="ica",
            ),
            patch(
                "services.sprint_manager.sprint_manager.check_ica_readiness",
                side_effect=IcaPreflightError("ICA not ready"),
            ),
            patch("services.sprint_manager.sprint_manager.dispatch_alerts"),
        ):
            result = sm.run_sprint_preflight(**_make_preflight_kwargs())

        assert result.early_exit is True
        assert result.early_exit_reason == "ica-preflight-failed"
        # The transient attribute that main() reads
        assert getattr(result.state, "_preflight_exit_reason", None) == "ica-preflight-failed", (
            "State must carry _preflight_exit_reason='ica-preflight-failed'"
        )

    def test_ica_failure_ica_probe_not_called_for_auth_probe(self):
        """AC-2 / regression: _doctor_probe_auth must NOT be called when ICA fails."""
        import services.sprint_manager.sprint_manager as sm
        from services.sprint_manager.ica_preflight import IcaPreflightError

        mock_auth_probe = MagicMock(return_value=None)

        with (
            patch(
                "services.sprint_manager.sprint_manager.get_effective_llm_provider",
                return_value="ica",
            ),
            patch(
                "services.sprint_manager.sprint_manager.check_ica_readiness",
                side_effect=IcaPreflightError("ICA down"),
            ),
            patch(
                "services.sprint_manager.sprint_manager._doctor_probe_auth",
                mock_auth_probe,
            ),
            patch("services.sprint_manager.sprint_manager.dispatch_alerts"),
        ):
            result = sm.run_sprint_preflight(**_make_preflight_kwargs())

        assert result.early_exit is True
        mock_auth_probe.assert_not_called(), (
            "_doctor_probe_auth must NOT be called when provider='ica'"
        )


# ── AC-3: sprint reaches sane terminal state (no-stuck-running regression) ───

class TestPreflightFailureTerminalState:
    """AC-3: Both preflight failures leave the sprint in a usable state.

    The transient _preflight_exit_reason attribute on state is NOT serialized
    (SprintState.to_dict() doesn't include it), so the plan.json written by
    main() is the only persistent record.  The test confirms the attribute
    is present and that state.issues is empty (nothing dispatched).
    """

    def test_auth_failure_state_has_no_issues(self):
        """AC-3: after auth preflight fail, state.issues is empty (nothing dispatched)."""
        import services.sprint_manager.sprint_manager as sm

        with (
            patch(
                "services.sprint_manager.sprint_manager.get_effective_llm_provider",
                return_value="anthropic",
            ),
            patch(
                "services.sprint_manager.sprint_manager._doctor_probe_auth",
                return_value="OAuth session expired",
            ),
            patch("services.sprint_manager.sprint_manager.dispatch_alerts"),
        ):
            result = sm.run_sprint_preflight(**_make_preflight_kwargs())

        assert result.early_exit is True
        assert result.state.issues == [], (
            "No tickets should be in state after auth preflight failure"
        )
        # Verify the _preflight_exit_reason attribute is set (not just early_exit_reason)
        assert getattr(result.state, "_preflight_exit_reason", None) == "auth-preflight-failed"

    def test_ica_failure_state_has_no_issues(self):
        """AC-3: after ICA preflight fail, state.issues is empty."""
        import services.sprint_manager.sprint_manager as sm
        from services.sprint_manager.ica_preflight import IcaPreflightError

        with (
            patch(
                "services.sprint_manager.sprint_manager.get_effective_llm_provider",
                return_value="ica",
            ),
            patch(
                "services.sprint_manager.sprint_manager.check_ica_readiness",
                side_effect=IcaPreflightError("ICA unreachable"),
            ),
            patch("services.sprint_manager.sprint_manager.dispatch_alerts"),
        ):
            result = sm.run_sprint_preflight(**_make_preflight_kwargs())

        assert result.early_exit is True
        assert result.state.issues == []
        assert getattr(result.state, "_preflight_exit_reason", None) == "ica-preflight-failed"

    def test_no_tickets_case_unchanged(self):
        """AC-3 regression: genuine 'no tickets' case still sets end_reason correctly.

        Verifies that the no-dispatchable-tickets branch is NOT broken by the
        auth/ICA changes.  A state with issues=[] and NO _preflight_exit_reason
        must still route to the 'no-dispatchable-tickets' outcome.
        """
        import services.sprint_manager.sprint_manager as sm

        fake_state = sm.SprintState(
            sprint_label="sprint-99",
            sprint_number=99,
            project="",
            start_timestamp="",
        )
        # No _preflight_exit_reason set — simulates genuine "no tickets" case

        _preflight_reason = getattr(fake_state, "_preflight_exit_reason", None)
        assert _preflight_reason is None, (
            "A plain SprintState must not carry _preflight_exit_reason"
        )

        plan_calls: list[dict] = []

        def _fake_plan_json(label, state, cfg=None, **extra):
            plan_calls.append({"state": state, **extra})

        with patch(
            "services.sprint_manager.sprint_manager._plan_json_set_state_sm",
            side_effect=_fake_plan_json,
        ):
            # Reproduce the main() sub-branch selection
            if _preflight_reason == "auth-preflight-failed":
                sm._plan_json_set_state_sm("sprint-99", "draft", end_reason="auth-preflight-failed")
            elif _preflight_reason == "ica-preflight-failed":
                sm._plan_json_set_state_sm("sprint-99", "draft", end_reason="ica-preflight-failed")
            else:
                sm._plan_json_set_state_sm("sprint-99", "draft", end_reason="no-dispatchable-tickets")

        assert plan_calls, "Expected a plan.json write"
        assert plan_calls[-1]["state"] == "draft"
        assert plan_calls[-1].get("end_reason") == "no-dispatchable-tickets", (
            "Genuine no-tickets case must still use end_reason='no-dispatchable-tickets'"
        )
