"""History ledger PR + Summary links must populate from the durable `sprints`
table, not only the state file (issue: links missing on the ledger card).

The finish flow writes `pr_number` / `summary_issue_url` / `summary_path` to the
durable lifecycle row; older state.json files often lack them. The lifecycle
record builder must prefer the row columns so the card's PR/Summary quick-links
render reliably.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "apps" / "dashboard"))

# No DB_PATH is set here on purpose: _record_from_lifecycle never touches the DB
# in these tests (its enrichment + plan readers are monkeypatched), and setting a
# real DB_PATH at import time would pollute sibling tests that inject a fake DB.
from routers import sprint_history_service as svc  # noqa: E402


def test_lifecycle_record_prefers_durable_summary_and_pr(tmp_path, monkeypatch):
    # State file lacks pr/summary; the durable row carries both.
    monkeypatch.setattr(svc, "_enrich_from_state", lambda label, dirs: {
        "duration": None, "tokens": None, "estimate_accuracy": None,
        "pr_number": None, "summary_path": None,
        "summary_issue_url": None, "summary_issue_num": None,
        "reconciliation": None, "issues": [], "failed_tickets": [],
        "failure_reason": None, "end_reason": None, "plan_status": None,
        "post_sprint": None,
    })
    monkeypatch.setattr(svc, "_read_plan_file", lambda dirs, label: {})

    row = {
        "label": "sprint-68", "project": "owner/repo", "state": "completed",
        "started_at": None, "ended_at": None,
        "pr_number": 906,
        "summary_issue_url": "https://github.com/owner/repo/issues/905",
        "summary_path": "/x/sprint-68-summary.md",
        "run_ingested_at": None,
    }
    rec = svc._record_from_lifecycle(row, tmp_path)
    assert rec["pr_number"] == 906
    assert rec["summary_issue_url"].endswith("/issues/905")
    assert rec["summary_issue_num"] == 905          # derived from the URL
    assert rec["summary_path"] == "/x/sprint-68-summary.md"


def test_lifecycle_record_falls_back_to_state(tmp_path, monkeypatch):
    # Durable row empty → fall back to the state-file enrichment.
    monkeypatch.setattr(svc, "_enrich_from_state", lambda label, dirs: {
        "duration": None, "tokens": None, "estimate_accuracy": None,
        "pr_number": 12, "summary_path": "/s.md",
        "summary_issue_url": "https://github.com/o/r/issues/34",
        "summary_issue_num": 34,
        "reconciliation": None, "issues": [], "failed_tickets": [],
        "failure_reason": None, "end_reason": None, "plan_status": None,
        "post_sprint": None,
    })
    monkeypatch.setattr(svc, "_read_plan_file", lambda dirs, label: {})
    row = {"label": "sprint-9", "project": "o/r", "state": "completed",
           "pr_number": None, "summary_issue_url": None, "summary_path": None,
           "run_ingested_at": None}
    rec = svc._record_from_lifecycle(row, tmp_path)
    assert rec["pr_number"] == 12
    assert rec["summary_issue_num"] == 34
    assert rec["summary_path"] == "/s.md"
