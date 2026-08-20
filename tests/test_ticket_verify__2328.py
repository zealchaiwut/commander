"""AC tests for post-step ticket verification (issue #2328).

Each case below is a real incident from viral-radar sprint-7, not a synthetic
scenario — the strings are taken from the recorded dispatch outcomes.

No live HTTP: label and PR reads are injected.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

from services.sprint_manager.dispatch_runner import DispatchRun, execute_run  # noqa: E402
from services.sprint_manager.ticket_verify import (  # noqa: E402
    labels_are_contradictory,
    report_recommends_rework,
    ticket_advanced,
    verify_step,
)

# Verbatim from run 53af935a73ea, #83's tester — recorded ok=True at the time.
REAL_REWORK_REPORT = (
    "I committed 6 independent verification tests to the branch "
    "(`feature/verdict-rules-feedback-83`, commit `7b5ec35`) and recommended "
    "**needs-rework**, not merging as-is."
)

# Verbatim shape from #80's tester, which passed and merged nothing.
CLEAN_REPORT = "## Verification complete — issue #80\n\nAll acceptance criteria pass."


# --- Failure mode 3: a negative verdict inside a successful run ------------

def test_real_rework_recommendation_is_detected():
    rework, why = report_recommends_rework(REAL_REWORK_REPORT)
    assert rework is True
    assert "recommended rework" in why


@pytest.mark.parametrize("text", [
    "I recommend needs-rework here",
    "Recommending rework before this lands",
    "not merging as-is",
    "I did not merge; the AC is unmet",
    "Declining to merge until the UI exists",
    "This should not be merged yet",
])
def test_rework_phrasings_are_detected(text):
    assert report_recommends_rework(text)[0] is True


@pytest.mark.parametrize("text", [
    CLEAN_REPORT,
    "Merged to develop and moved the ticket to UAT.",
    "No rework needed — all criteria pass.",
    "",
])
def test_clean_reports_are_not_flagged(text):
    """A false positive stops a good run, so the patterns must stay narrow."""
    assert report_recommends_rework(text)[0] is False


# --- Failure mode 2: contradictory labels ---------------------------------

def test_needs_rework_alongside_uat_is_contradictory():
    """#82 carried both. A rerun would have reset merged, verified work."""
    clash, why = labels_are_contradictory(["needs-rework", "UAT", "sprint-7"])
    assert clash is True
    assert "rerun would reset" in why


def test_needs_rework_alone_is_not_contradictory():
    assert labels_are_contradictory(["needs-rework", "sprint-7"])[0] is False


def test_terminal_label_alone_is_fine():
    assert labels_are_contradictory(["UAT", "sprint-7"])[0] is False


# --- Failure mode 1: the lifecycle was skipped ----------------------------

def test_tester_leaving_ticket_on_backlog_has_not_advanced():
    """#80: tester passed, label stayed backlog, PR stayed open."""
    ok, why = ticket_advanced(["backlog", "sprint-7"], "tester")
    assert ok is False
    assert "never reached UAT" in why


def test_coder_leaving_ticket_on_backlog_has_not_advanced():
    ok, why = ticket_advanced(["backlog"], "coder")
    assert ok is False
    assert "still not at SIT" in why


def test_coder_reaching_sit_has_advanced():
    assert ticket_advanced(["SIT"], "coder")[0] is True


def test_tester_reaching_uat_has_advanced():
    assert ticket_advanced(["UAT"], "tester")[0] is True


def test_needs_rework_label_blocks_advance_for_either_step():
    for step in ("coder", "tester"):
        assert ticket_advanced(["needs-rework"], step)[0] is False


# --- verify_step orchestration --------------------------------------------

def _labels(values):
    return lambda issue, repo: list(values)


def test_open_pr_behind_a_passing_tester_is_a_failure():
    ok, why = verify_step(
        step="tester", issue=80, repo="o/r", report=CLEAN_REPORT,
        fetch_labels=_labels(["UAT"]), fetch_open_pr=lambda i, r: 85,
    )
    assert ok is False
    assert "PR #85 is still open" in why


def test_fully_advanced_ticket_passes():
    ok, why = verify_step(
        step="tester", issue=80, repo="o/r", report=CLEAN_REPORT,
        fetch_labels=_labels(["UAT"]), fetch_open_pr=lambda i, r: None,
    )
    assert ok is True and why == ""


def test_rework_report_beats_a_healthy_looking_board():
    """The agent read the AC. Its verdict outranks tidy labels."""
    ok, why = verify_step(
        step="tester", issue=83, repo="o/r", report=REAL_REWORK_REPORT,
        fetch_labels=_labels(["UAT"]), fetch_open_pr=lambda i, r: None,
    )
    assert ok is False
    assert "recommended rework" in why


def test_unreadable_labels_refuse_rather_than_assume():
    def boom(issue, repo):
        raise RuntimeError("gh exploded")

    ok, why = verify_step(
        step="tester", issue=1, repo="o/r", report=CLEAN_REPORT, fetch_labels=boom,
    )
    assert ok is False
    assert "could not read labels" in why


# --- Wired into the runner -------------------------------------------------

def test_failed_verification_stops_the_run(tmp_path):
    """A ticket that did not advance must stop dispatch, not poison dependents."""
    calls = []

    def spawn(step, issue, repo, *, cwd, baseline_note, **kw):
        calls.append((step, issue))
        return True, CLEAN_REPORT

    def verify(*, step, issue, repo, report):
        return (False, "tester passed but the ticket never reached UAT")

    run = execute_run(
        DispatchRun(run_id="v1", sprint_label="s", tickets=[80, 81], repo="o/r"),
        repo_root=tmp_path, cwd=tmp_path, spawn=spawn, verify=verify,
    )

    assert run.status == "failed"
    assert run.failed_issue == 80
    assert 81 not in [i for _s, i in calls]
    assert "did not advance" in run.outcomes[-1].detail


def test_passing_verification_lets_the_run_continue(tmp_path):
    def spawn(step, issue, repo, *, cwd, baseline_note, **kw):
        return True, CLEAN_REPORT

    run = execute_run(
        DispatchRun(run_id="v2", sprint_label="s", tickets=[80], repo="o/r"),
        repo_root=tmp_path, cwd=tmp_path, spawn=spawn,
        verify=lambda **kw: (True, ""),
    )
    assert run.status == "done"


def test_verification_can_be_disabled(tmp_path):
    """verify=None keeps the old behaviour for callers that want it."""
    run = execute_run(
        DispatchRun(run_id="v3", sprint_label="s", tickets=[80], repo="o/r"),
        repo_root=tmp_path, cwd=tmp_path,
        spawn=lambda *a, **k: (True, CLEAN_REPORT), verify=None,
    )
    assert run.status == "done"


def test_verification_is_not_run_after_a_failed_step(tmp_path):
    """A step that already failed must not be re-judged as 'did not advance'."""
    seen = []

    run = execute_run(
        DispatchRun(run_id="v4", sprint_label="s", tickets=[80], repo="o/r"),
        repo_root=tmp_path, cwd=tmp_path,
        spawn=lambda *a, **k: (False, "agent crashed"),
        verify=lambda **kw: (seen.append(1), (True, ""))[1],
    )
    assert run.status == "failed"
    assert seen == [], "verification should not run for an already-failed step"
    assert run.outcomes[-1].detail == "agent crashed"
