"""Issue #2049 — Reconcile and materialize must produce identical settled_done counts.

AC1: One canonical definition of settled/done — _compute_summary_counts from
     sprint_artifact_service is the single source; reconcile calls it directly.
AC2: Reconcile and materialize produce identical counts for identical input —
     verified with the concrete failure case and a parametric sweep.
AC4: The pill's settled count is not labelled "done" in the HTML (renamed to
     "settled" to distinguish it from the donut's done+uat completion metric).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO = Path(__file__).resolve().parents[1]
_DASHBOARD = _REPO / "apps" / "dashboard"
for _p in (str(_DASHBOARD), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from routers.sprint_artifact_service import _compute_summary_counts  # noqa: E402


# ── AC2: concrete failure case from the issue ────────────────────────────────

def test_uat_tickets_count_as_settled():
    """6 UAT + 4 needs-rework (agent_status=failed) must all be settled.

    Concrete failure: old reconcile formula returned 0; materialize returned 10.
    """
    issues = (
        [{"status": "uat", "agent_status": "completed", "failure_reason": None}] * 6
        + [{"status": "needs-rework", "agent_status": "failed", "failure_reason": "gate"}] * 4
    )
    counts = _compute_summary_counts(issues)
    assert counts["summary_settled_done"] == 10, (
        "All 10 tickets (6 UAT + 4 needs-rework/failed) must be settled"
    )


# ── AC2: reconcile path calls _compute_summary_counts ────────────────────────

def _make_row(issues: list[dict], label: str = "sprint-999") -> dict:
    return {
        "state": "completed",  # must be in _TERMINAL_STATES for _reconcile_counts to run
        "issues_json": json.dumps(issues),
        "project": "zealchaiwut/commander",
        "label": label,
    }


def _run_reconcile_counts(issues: list[dict]) -> dict:
    """Run _reconcile_counts and return the kwargs passed to update_sprint_run_counts."""
    import routers.sprint_reconcile_service as rs  # noqa: PLC0415

    row = _make_row(issues)
    label = "sprint-999"
    project = "zealchaiwut/commander"

    captured: dict = {}

    def fake_update(lbl, issues_json, settled_done, uat_count, failure_count, project=None):
        captured["settled_done"] = settled_done
        captured["uat_count"] = uat_count
        captured["failure_count"] = failure_count

    fake_db = MagicMock()
    fake_db.get_sprint_agent_runs.return_value = []
    fake_db.get_mirrored_issue.return_value = None  # no needs-rework labels
    fake_db.update_sprint_run_counts.side_effect = fake_update
    fake_db.update_sprint_reconciliation.return_value = None

    with patch.object(rs, "_db", return_value=fake_db):
        # Inject issues directly into by_id via a mock that returns no ar_issues
        # so the existing issues_json drives the entire merged list.
        with patch.object(rs, "_issues_from_agent_runs", return_value=[]):
            # _reconcile_counts short-circuits when ar_issues is empty.
            # Trigger via a by_id drift: inject one artificial ar_issue with
            # a definitively non-open state so `changed` is set.
            first = issues[0]
            tid = first.get("ticket_id") or first.get("number") or 1
            fake_ar = [{"ticket_id": tid, "number": tid, "state": "merged", "agent_status": "completed"}]
            with patch.object(rs, "_issues_from_agent_runs", return_value=fake_ar):
                rs._reconcile_counts(label, row, project)

    return captured


@pytest.mark.parametrize("issues", [
    # All UAT
    [{"number": i, "ticket_id": i, "status": "uat", "agent_status": "completed", "failure_reason": None}
     for i in range(1, 7)],
    # Mix: uat + needs-rework/failed
    (
        [{"number": i, "ticket_id": i, "status": "uat", "agent_status": "completed", "failure_reason": None}
         for i in range(1, 7)]
        + [{"number": i, "ticket_id": i, "status": "needs-rework", "agent_status": "failed", "failure_reason": "gate"}
           for i in range(7, 11)]
    ),
    # All done/completed
    [{"number": i, "ticket_id": i, "status": "done", "agent_status": "completed", "failure_reason": None}
     for i in range(1, 6)],
    # Mix: done + pending (not settled)
    [{"number": 1, "ticket_id": 1, "status": "done", "agent_status": "completed", "failure_reason": None},
     {"number": 2, "ticket_id": 2, "status": "pending", "agent_status": None, "failure_reason": None}],
])
def test_reconcile_and_materialize_agree(issues):
    """AC2: For any issue list, reconcile and materialize yield identical counts."""
    materialize = _compute_summary_counts(issues)
    reconcile = _run_reconcile_counts(issues)

    assert reconcile["settled_done"] == materialize["summary_settled_done"], (
        f"Reconcile settled_done={reconcile['settled_done']} differs from "
        f"materialize summary_settled_done={materialize['summary_settled_done']}"
    )
    assert reconcile["uat_count"] == materialize["summary_uat_count"]
    assert reconcile["failure_count"] == materialize["summary_failure_count"]


# ── AC1: dead startup.py copy is gone ────────────────────────────────────────

def test_startup_does_not_define_settled_done_from_columns():
    """AC1: The dead duplicate _settled_done_from_columns in startup.py is deleted.

    Source-text exception (#2162 / #1746): this is a deletion guard — the
    function no longer exists, so there is nothing to import and call. A
    source-regex check is the only viable tool for confirming removal.
    """
    src = (_DASHBOARD / "startup.py").read_text(encoding="utf-8")
    assert "def _settled_done_from_columns(" not in src, (
        "Dead copy of _settled_done_from_columns still present in startup.py"
    )


# ── AC4: pill label is "settled", not "done" ─────────────────────────────────

def test_pill_settled_count_not_labelled_done():
    """AC4: The pill's settled count must be labelled 'settled', not 'done'.

    The donut center already uses 'done' for done+uat (completion metric).
    The pill uses the throughput/settled metric — labelling it 'done' is
    misleading because it includes needs-rework and failed tickets.

    Source-text exception (#2162 / #1746): doneHtml is constructed inside
    inline JS in project.html, not in an ES module. A DOM/render-based Node
    test would require a full bundler + server environment. The label is a
    static template-literal string, not dynamic logic, so a substring check
    is both necessary and sufficient here.
    """
    src = (_DASHBOARD / "static" / "project.html").read_text(encoding="utf-8")
    # doneHtml carries the pill's settled count; must say "settled" not "done"
    assert "${store.done} settled" in src, (
        "pill's settled count must be labelled 'settled' (not 'done')"
    )
    assert "${store.done} done" not in src, (
        "pill must not re-use 'done' label for the settled (throughput) count"
    )
