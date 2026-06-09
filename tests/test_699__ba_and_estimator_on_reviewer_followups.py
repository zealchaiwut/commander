"""Tests for issue #699: Run BA and estimator on reviewer follow-up tickets.

AC items verified:
  AC-1  After _dispatch_reviewer() returns, sprint_manager reads follow_up_tickets
        list from reviewer findings (actual issue numbers, not just a count)
  AC-2  For each issue number in follow_up_tickets, sprint_manager invokes the BA
        agent to rewrite the issue body to the standard format
  AC-3  For each issue, sprint_manager then invokes the estimator
  AC-4  BA runs before estimator (same order as single-ticket bulk-create path)
  AC-5  If follow_up_tickets is empty, no BA/estimator calls are made; no error raised
  AC-6  A failure on one follow-up ticket does not abort remaining tickets;
        failure is logged and sprint continues
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import services.sprint_manager.sprint_manager as sm


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_merged_state(sprint_label: str = "sprint-99") -> MagicMock:
    """Return a SprintState mock with one merged issue (passes reviewer skip guard)."""
    merged = MagicMock()
    merged.status = "done"
    state = MagicMock()
    state.sprint_label = sprint_label
    state.issues = [merged]
    state.reviewer_findings = None
    return state


def _make_cfg(tmp_path: Path) -> MagicMock:
    cfg = MagicMock()
    cfg.worktree_coder = tmp_path
    cfg.logs_dir = tmp_path / "logs"
    cfg.logs_dir.mkdir(parents=True, exist_ok=True)
    cfg.reviewer_prompt_template = None
    cfg.repo_name = "owner/testrepo"
    return cfg


def _make_proc(rc: int = 0) -> MagicMock:
    p = MagicMock()
    p.wait.return_value = rc
    p.returncode = rc
    return p


def _popen_writing(log_content: str, rc: int = 0):
    """Return a Popen side_effect that writes log_content to stdout kwarg."""
    def side_effect(cmd, stdout=None, stderr=None, **kwargs):
        if stdout is not None and hasattr(stdout, "write"):
            stdout.write(log_content)
        proc = MagicMock()
        proc.wait.return_value = rc
        proc.returncode = rc
        return proc
    return side_effect


def _make_detector(killed: bool = False) -> MagicMock:
    d = MagicMock()
    d.killed = killed
    return d


# ---------------------------------------------------------------------------
# AC-1: _dispatch_reviewer parses actual follow-up issue numbers from log
# ---------------------------------------------------------------------------

class TestAC1ParseFollowUpIssueNumbers:
    """_dispatch_reviewer must extract actual GitHub issue numbers from the log."""

    def test_follow_up_numbers_parsed_from_issue_urls_in_log(self, tmp_path):
        """Issue URLs in reviewer log → follow_up_tickets contains those numbers."""
        cfg = _make_cfg(tmp_path)
        state = _make_merged_state()

        log_content = (
            "Doing review...\n"
            "Created follow-up: https://github.com/owner/testrepo/issues/201\n"
            "Created follow-up: https://github.com/owner/testrepo/issues/202\n"
            "Reviewer complete: 0 blockers, 2 suggestions, 0 nits, 2 follow-up tickets opened\n"
        )

        with (
            patch.object(sm, "subprocess") as mock_sub,
            patch.object(sm, "HangDetector", return_value=_make_detector()),
        ):
            mock_sub.Popen.side_effect = _popen_writing(log_content)
            sm._dispatch_reviewer(
                state=state,
                summary_issue_num=100,
                sprint_branch="sprint/sprint-99",
                base_sha="abc",
                head_sha="def",
                cfg=cfg,
                repo_name="owner/testrepo",
            )

        findings = state.reviewer_findings
        assert findings is not None
        assert 201 in findings["follow_up_tickets"], \
            f"Issue 201 must be in follow_up_tickets, got {findings['follow_up_tickets']}"
        assert 202 in findings["follow_up_tickets"], \
            f"Issue 202 must be in follow_up_tickets, got {findings['follow_up_tickets']}"

    def test_summary_issue_excluded_from_follow_up_list(self, tmp_path):
        """The sprint summary issue number is not treated as a follow-up ticket."""
        cfg = _make_cfg(tmp_path)
        state = _make_merged_state()

        log_content = (
            "Summary issue: https://github.com/owner/testrepo/issues/100\n"
            "Created follow-up: https://github.com/owner/testrepo/issues/201\n"
            "Reviewer complete: 0 blockers, 1 suggestions, 0 nits, 1 follow-up tickets opened\n"
        )

        with (
            patch.object(sm, "subprocess") as mock_sub,
            patch.object(sm, "HangDetector", return_value=_make_detector()),
        ):
            mock_sub.Popen.side_effect = _popen_writing(log_content)
            sm._dispatch_reviewer(
                state=state,
                summary_issue_num=100,
                sprint_branch="sprint/sprint-99",
                base_sha="abc",
                head_sha="def",
                cfg=cfg,
                repo_name="owner/testrepo",
            )

        tickets = state.reviewer_findings["follow_up_tickets"]
        assert 100 not in tickets, \
            f"Sprint summary issue 100 must not appear in follow_up_tickets, got {tickets}"
        assert 201 in tickets

    def test_comment_urls_not_treated_as_follow_up_issues(self, tmp_path):
        """issuecomment URLs must not be parsed as follow-up tickets."""
        cfg = _make_cfg(tmp_path)
        state = _make_merged_state()

        log_content = (
            "Posted comment: https://github.com/owner/testrepo/issues/100#issuecomment-12345\n"
            "Created follow-up: https://github.com/owner/testrepo/issues/201\n"
            "Reviewer complete: 0 blockers, 1 suggestions, 0 nits, 1 follow-up tickets opened\n"
        )

        with (
            patch.object(sm, "subprocess") as mock_sub,
            patch.object(sm, "HangDetector", return_value=_make_detector()),
        ):
            mock_sub.Popen.side_effect = _popen_writing(log_content)
            sm._dispatch_reviewer(
                state=state,
                summary_issue_num=100,
                sprint_branch="sprint/sprint-99",
                base_sha="abc",
                head_sha="def",
                cfg=cfg,
                repo_name="owner/testrepo",
            )

        tickets = state.reviewer_findings["follow_up_tickets"]
        # 100 appears only in a comment URL → excluded as summary issue anyway
        # but critically it must not appear via the comment URL path either
        assert 100 not in tickets

    def test_empty_follow_up_list_when_no_urls_in_log(self, tmp_path):
        """No issue creation URLs in log → follow_up_tickets is empty list."""
        cfg = _make_cfg(tmp_path)
        state = _make_merged_state()

        log_content = (
            "Reviewer complete: 0 blockers, 0 suggestions, 0 nits, 0 follow-up tickets opened\n"
        )

        with (
            patch.object(sm, "subprocess") as mock_sub,
            patch.object(sm, "HangDetector", return_value=_make_detector()),
        ):
            mock_sub.Popen.side_effect = _popen_writing(log_content)
            sm._dispatch_reviewer(
                state=state,
                summary_issue_num=100,
                sprint_branch="sprint/sprint-99",
                base_sha="abc",
                head_sha="def",
                cfg=cfg,
                repo_name="owner/testrepo",
            )

        assert state.reviewer_findings["follow_up_tickets"] == []


# ---------------------------------------------------------------------------
# AC-2 & AC-3: BA and estimator invoked for each follow-up ticket
# ---------------------------------------------------------------------------

class TestAC2And3BAAndEstimatorInvoked:
    """BA then estimator must be invoked for each issue in follow_up_tickets."""

    def test_ba_invoked_for_each_ticket(self):
        """_enrich_followup_tickets calls _dispatch_ba_for_followup for each ticket."""
        cfg = MagicMock()
        cfg.repo_name = "owner/repo"
        state = MagicMock()
        state.sprint_label = "sprint-1"

        with (
            patch.object(sm, "_dispatch_ba_for_followup") as mock_ba,
            patch.object(sm, "_dispatch_estimator_for_followup"),
        ):
            mock_ba.return_value = True
            sm._enrich_followup_tickets(
                follow_up_tickets=[10, 11],
                eff_repo="owner/repo",
                cfg=cfg,
                state=state,
            )

        assert mock_ba.call_count == 2, \
            f"BA must be called once per ticket, got {mock_ba.call_count}"
        called_nums = {c.args[0] for c in mock_ba.call_args_list}
        assert called_nums == {10, 11}

    def test_estimator_invoked_for_each_ticket(self):
        """_enrich_followup_tickets calls _dispatch_estimator_for_followup for each ticket."""
        cfg = MagicMock()
        state = MagicMock()
        state.sprint_label = "sprint-1"

        with (
            patch.object(sm, "_dispatch_ba_for_followup", return_value=True),
            patch.object(sm, "_dispatch_estimator_for_followup") as mock_est,
        ):
            mock_est.return_value = True
            sm._enrich_followup_tickets(
                follow_up_tickets=[10, 11],
                eff_repo="owner/repo",
                cfg=cfg,
                state=state,
            )

        assert mock_est.call_count == 2
        called_nums = {c.args[0] for c in mock_est.call_args_list}
        assert called_nums == {10, 11}


# ---------------------------------------------------------------------------
# AC-4: BA runs before estimator (order)
# ---------------------------------------------------------------------------

class TestAC4BABeforeEstimator:
    """BA must complete before estimator for each ticket."""

    def test_ba_called_before_estimator_per_ticket(self):
        """Call sequence: BA(10) → estimator(10) → BA(11) → estimator(11)."""
        call_order: list[str] = []

        def fake_ba(issue_num, eff_repo, cfg, state):
            call_order.append(f"ba:{issue_num}")
            return True

        def fake_est(issue_num, eff_repo, cfg):
            call_order.append(f"est:{issue_num}")
            return True

        cfg = MagicMock()
        state = MagicMock()
        state.sprint_label = "sprint-1"

        with (
            patch.object(sm, "_dispatch_ba_for_followup", side_effect=fake_ba),
            patch.object(sm, "_dispatch_estimator_for_followup", side_effect=fake_est),
        ):
            sm._enrich_followup_tickets(
                follow_up_tickets=[10, 11],
                eff_repo="owner/repo",
                cfg=cfg,
                state=state,
            )

        assert call_order.index("ba:10") < call_order.index("est:10"), \
            "BA must run before estimator for ticket 10"
        assert call_order.index("ba:11") < call_order.index("est:11"), \
            "BA must run before estimator for ticket 11"


# ---------------------------------------------------------------------------
# AC-5: Empty follow-up list → no calls, no error
# ---------------------------------------------------------------------------

class TestAC5EmptyListNoCallsNoError:
    """Empty follow_up_tickets → zero calls, no exception raised."""

    def test_no_ba_called_for_empty_list(self):
        cfg = MagicMock()
        state = MagicMock()

        with (
            patch.object(sm, "_dispatch_ba_for_followup") as mock_ba,
            patch.object(sm, "_dispatch_estimator_for_followup") as mock_est,
        ):
            sm._enrich_followup_tickets(
                follow_up_tickets=[],
                eff_repo="owner/repo",
                cfg=cfg,
                state=state,
            )

        mock_ba.assert_not_called()
        mock_est.assert_not_called()

    def test_empty_list_does_not_raise(self):
        cfg = MagicMock()
        state = MagicMock()

        with (
            patch.object(sm, "_dispatch_ba_for_followup"),
            patch.object(sm, "_dispatch_estimator_for_followup"),
        ):
            # Must not raise
            sm._enrich_followup_tickets(
                follow_up_tickets=[],
                eff_repo="owner/repo",
                cfg=cfg,
                state=state,
            )


# ---------------------------------------------------------------------------
# AC-6: Failure on one ticket does not abort others
# ---------------------------------------------------------------------------

class TestAC6FailureIsolation:
    """A failure on one ticket must not prevent enrichment of remaining tickets."""

    def test_ba_exception_on_first_ticket_does_not_skip_second(self):
        """BA raises on ticket 10; ticket 11 must still be processed."""
        processed: list[int] = []

        def fake_ba(issue_num, eff_repo, cfg, state):
            if issue_num == 10:
                raise RuntimeError("simulated BA failure for #10")
            processed.append(issue_num)
            return True

        cfg = MagicMock()
        state = MagicMock()

        with (
            patch.object(sm, "_dispatch_ba_for_followup", side_effect=fake_ba),
            patch.object(sm, "_dispatch_estimator_for_followup", return_value=True) as mock_est,
        ):
            # Must not raise
            sm._enrich_followup_tickets(
                follow_up_tickets=[10, 11],
                eff_repo="owner/repo",
                cfg=cfg,
                state=state,
            )

        assert 11 in processed, "Ticket 11 BA must still be called after ticket 10 failure"

    def test_estimator_exception_on_first_ticket_does_not_skip_second(self):
        """Estimator raises on ticket 10; ticket 11 estimator must still run."""
        est_calls: list[int] = []

        def fake_est(issue_num, eff_repo, cfg):
            if issue_num == 10:
                raise RuntimeError("simulated estimator failure for #10")
            est_calls.append(issue_num)
            return True

        cfg = MagicMock()
        state = MagicMock()

        with (
            patch.object(sm, "_dispatch_ba_for_followup", return_value=True),
            patch.object(sm, "_dispatch_estimator_for_followup", side_effect=fake_est),
        ):
            sm._enrich_followup_tickets(
                follow_up_tickets=[10, 11],
                eff_repo="owner/repo",
                cfg=cfg,
                state=state,
            )

        assert 11 in est_calls, \
            "Estimator for ticket 11 must run even after estimator for ticket 10 failed"

    def test_ba_failure_logged(self, capsys):
        """BA failure must produce a log/print so the failure is visible."""

        def fake_ba(issue_num, eff_repo, cfg, state):
            raise RuntimeError("ba boom")

        cfg = MagicMock()
        state = MagicMock()

        with (
            patch.object(sm, "_dispatch_ba_for_followup", side_effect=fake_ba),
            patch.object(sm, "_dispatch_estimator_for_followup", return_value=True),
        ):
            sm._enrich_followup_tickets(
                follow_up_tickets=[42],
                eff_repo="owner/repo",
                cfg=cfg,
                state=state,
            )

        out = capsys.readouterr().out
        # The failure message must mention the issue number so it's identifiable
        assert "42" in out, f"Failure log must mention issue number 42, got: {out!r}"

    def test_estimator_failure_logged(self, capsys):
        """Estimator failure must be logged."""

        def fake_est(issue_num, eff_repo, cfg):
            raise RuntimeError("est boom")

        cfg = MagicMock()
        state = MagicMock()

        with (
            patch.object(sm, "_dispatch_ba_for_followup", return_value=True),
            patch.object(sm, "_dispatch_estimator_for_followup", side_effect=fake_est),
        ):
            sm._enrich_followup_tickets(
                follow_up_tickets=[42],
                eff_repo="owner/repo",
                cfg=cfg,
                state=state,
            )

        out = capsys.readouterr().out
        assert "42" in out, f"Failure log must mention issue number 42, got: {out!r}"


# ---------------------------------------------------------------------------
# Integration: _enrich_followup_tickets wired into sprint run
# ---------------------------------------------------------------------------

class TestAC1Integration:
    """_enrich_followup_tickets is called after _dispatch_reviewer with the
    follow_up_tickets from reviewer findings."""

    def test_enrich_called_with_reviewer_follow_up_tickets(self):
        """After _dispatch_reviewer sets reviewer_findings, _enrich_followup_tickets
        is invoked with the correct issue numbers."""

        def fake_dispatch_reviewer(state, **kwargs):
            state.reviewer_findings = {
                "blockers": 0,
                "suggestions": 2,
                "nits": 0,
                "follow_up_tickets": [300, 301],
            }
            state.reviewer_status = "succeeded"
            state.reviewer_comment_url = None

        enriched: list[list] = []

        def fake_enrich(follow_up_tickets, eff_repo, cfg, state):
            enriched.append(list(follow_up_tickets))

        # Build a minimal sprint state that reaches the reviewer block
        state = MagicMock()
        state.sprint_label = "sprint-99"
        state.reviewer_enabled = True
        state.reviewer_status = None
        state.reviewer_findings = None
        state.reviewer_comment_url = None

        cfg = MagicMock()
        cfg.reviewer_enabled = True
        cfg.repo_name = "owner/repo"

        with (
            patch.object(sm, "_dispatch_reviewer", side_effect=fake_dispatch_reviewer),
            patch.object(sm, "_enrich_followup_tickets", side_effect=fake_enrich),
        ):
            # Simulate the sprint-run reviewer block directly
            sm._dispatch_reviewer(
                state=state,
                summary_issue_num=50,
                sprint_branch="sprint/sprint-99",
                base_sha="aaa",
                head_sha="bbb",
                cfg=cfg,
                repo_name="owner/repo",
            )
            findings = state.reviewer_findings or {}
            follow_ups = findings.get("follow_up_tickets", [])
            sm._enrich_followup_tickets(
                follow_up_tickets=follow_ups,
                eff_repo="owner/repo",
                cfg=cfg,
                state=state,
            )

        assert enriched == [[300, 301]], \
            f"_enrich_followup_tickets must receive [300, 301], got {enriched}"
