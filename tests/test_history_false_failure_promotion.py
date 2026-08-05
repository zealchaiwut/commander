"""History must not show a false failed sprint when every ticket shipped.

Regression: perf-coach sprint-83 ran 5/5 tickets successfully and merged, but
a mid-run restart left stale failed_tickets in state — History showed FAILED and
hid Bulk complete behind the child-not-finished gate.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

from routers import sprint_history_service as shs  # noqa: E402


def test_issues_all_shipped_requires_every_ticket_merged_or_completed():
    assert shs._issues_all_shipped([
        {"state": "merged", "agent_status": "completed"},
    ])
    assert not shs._issues_all_shipped([
        {"state": "open", "agent_status": "failed"},
    ])


def test_lifecycle_display_promotes_needs_rework_when_all_shipped():
    issues = [{"state": "merged", "agent_status": "completed", "ticket_id": 1}]
    assert shs._lifecycle_display_state("needs_rework", "orphaned", issues) == "ready_to_merge"
    assert shs._lifecycle_display_state("failed", None, issues) == "ready_to_merge"


def test_clear_stale_failure_signals_promotes_record():
    rec = {
        "lifecycle_state": "failed",
        "failed_tickets": [{"ticket_id": 1, "failure_reason": "stale hang"}],
        "failure_reason": "stale hang",
        "issues": [{"state": "merged", "agent_status": "completed", "ticket_id": 1}],
    }
    shs._clear_stale_failure_signals(rec)
    assert rec["lifecycle_state"] == "ready_to_merge"
    assert rec["failed_tickets"] == []
    assert rec["failure_reason"] is None


def test_finalize_lineage_does_not_re_tag_shipped_sprint_as_needs_rework():
    records = [{
        "label": "sprint-83",
        "project": "zealchaiwut/perf-coach",
        "lifecycle_state": "failed",
        "failed_tickets": [{"ticket_id": 1, "failure_reason": "stale"}],
        "failure_reason": "stale",
        "issues": [{"state": "merged", "agent_status": "completed", "ticket_id": 1}],
    }]
    shs._finalize_lineage(records)
    assert records[0]["lifecycle_state"] == "ready_to_merge"
    assert records[0]["failed_tickets"] == []


# ── issue #2199: end_reason="ticket-failures" must survive the promotion ──────
#
# perf-coach sprint-121: every ticket eventually merged (via fix-loop retries,
# some exhausted), but the sprint's own end_reason is the sprint manager's
# explicit "ticket-failures" classification — a real gate failure occurred.
# The blanket "all shipped -> ready_to_merge" promotion above (added for
# sprint-83, whose end_reason was never "ticket-failures") must not discard it.

def test_lifecycle_display_does_not_promote_ticket_failures_end_reason():
    issues = [{"state": "merged", "agent_status": "completed", "ticket_id": 1}]
    assert shs._lifecycle_display_state("needs_rework", "ticket-failures", issues) == "needs_rework"
    assert shs._lifecycle_display_state("failed", "ticket-failures", issues) == "failed"


def test_lifecycle_display_still_promotes_other_end_reasons():
    issues = [{"state": "merged", "agent_status": "completed", "ticket_id": 1}]
    assert shs._lifecycle_display_state("needs_rework", "orphaned", issues) == "ready_to_merge"
    assert shs._lifecycle_display_state("needs_rework", None, issues) == "ready_to_merge"


def test_clear_stale_failure_signals_preserves_ticket_failures():
    rec = {
        "lifecycle_state": "needs_rework",
        "end_reason": "ticket-failures",
        "failed_tickets": [],
        "failure_reason": None,
        "issues": [
            {"state": "merged", "agent_status": "completed", "ticket_id": 1420,
             "failure_reason": "Fix-loop exhausted after 2 attempt(s) (attempt 1: LINT_FAIL; attempt 2: LINT_FAIL)"},
        ],
    }
    shs._clear_stale_failure_signals(rec)
    assert rec["lifecycle_state"] == "needs_rework", (
        "A sprint whose own end_reason is 'ticket-failures' must not be "
        "silently promoted to ready_to_merge just because its tickets "
        "eventually merged after exhausting the fix-loop."
    )


def test_finalize_lineage_preserves_ticket_failures_sprint():
    records = [{
        "label": "sprint-121",
        "project": "zealchaiwut/perf-coach",
        "lifecycle_state": "needs_rework",
        "end_reason": "ticket-failures",
        "failed_tickets": [],
        "failure_reason": None,
        "issues": [
            {"state": "merged", "agent_status": "completed", "ticket_id": 1420},
            {"state": "merged", "agent_status": "completed", "ticket_id": 1525},
        ],
    }]
    shs._finalize_lineage(records)
    assert records[0]["lifecycle_state"] == "needs_rework", (
        f"Expected sprint-121 to stay needs_rework on History, "
        f"got {records[0]['lifecycle_state']!r}"
    )
