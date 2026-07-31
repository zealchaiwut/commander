"""Tests for issue #1295: _reconcile_counts must re-derive summary_uat_count
instead of preserving the stored (potentially stale) value.

AC: `_reconcile_counts` re-derives `summary_uat_count` from the merged issues
    list (checking status == 'uat') rather than forwarding the stored value
    verbatim, consistent with how the materialize path derives it.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "services" / "sprint_manager"))
sys.path.insert(0, str(_DASHBOARD_ROOT))

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")

import db as _db_module  # noqa: E402

_TEST_PROJECT = "zealchaiwut/test-project"


@pytest.fixture
def fresh_db(tmp_path):
    db_file = tmp_path / "test_1295.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = str(db_file)
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


class TestRederiveSummaryUatCount:
    """summary_uat_count must be re-derived from the merged issues list, not preserved."""

    def test_stale_uat_count_overwritten_when_drift_detected(self, fresh_db):
        """Stored summary_uat_count=2 is overwritten to 0 when reconcile detects drift.

        Scenario: sprint ended with 2 UAT tickets but they were later approved.
        The stored count stays at 2. A new agent_run causes drift detection.
        After reconcile, summary_uat_count must be 0 (re-derived), not 2 (stale).
        """
        label = "sprint-1295-stale"
        fresh_db.record_sprint_ready_to_merge(label, end_reason="natural", project=_TEST_PROJECT)
        # Seed with normalized issues (no status field) and a stale uat count of 2.
        fresh_db.update_sprint_run_counts(
            label,
            json.dumps([{"ticket_id": 10, "number": 10, "state": "merged"}]),
            summary_settled_done=1,
            summary_uat_count=2,    # stale — tickets approved since sprint ended
            summary_failure_count=0,
            project=_TEST_PROJECT,
        )

        # A new agent_run for an issue not in issues_json → triggers drift
        fresh_db.record_agent_start(11, label, "coder")
        fresh_db.record_agent_finish(11, label, "coder", outcome="completed")

        from routers import sprint_reconcile_service as svc
        updated = svc._reconcile_counts(label, fresh_db.get_sprint(label, project=_TEST_PROJECT), project=_TEST_PROJECT)
        assert updated is True

        row = fresh_db.get_sprint(label, project=_TEST_PROJECT)
        # Normalized entries have no status field → uat_count derived as 0, not 2.
        assert row["summary_uat_count"] == 0

    def test_uat_count_derived_from_entries_with_status_uat(self, fresh_db):
        """summary_uat_count counts merged-list entries that have status='uat'.

        Scenario: issues_json has a raw (pre-normalization) entry that preserves
        status='uat'. After a reconcile pass that adds drift from a new agent_run,
        summary_uat_count must be 1 (derived from that entry), not 0.
        """
        label = "sprint-1295-raw-uat"
        fresh_db.record_sprint_ready_to_merge(label, end_reason="natural", project=_TEST_PROJECT)
        # Seed issues_json with a raw entry that retains status='uat'.
        fresh_db.update_sprint_run_counts(
            label,
            json.dumps([{
                "ticket_id": 20, "number": 20,
                "state": "merged", "status": "uat",  # pre-normalization entry
            }]),
            summary_settled_done=1,
            summary_uat_count=0,   # will be re-derived to 1
            summary_failure_count=0,
            project=_TEST_PROJECT,
        )

        # New agent_run to trigger drift detection
        fresh_db.record_agent_start(21, label, "coder")
        fresh_db.record_agent_finish(21, label, "coder", outcome="completed")

        from routers import sprint_reconcile_service as svc
        updated = svc._reconcile_counts(label, fresh_db.get_sprint(label, project=_TEST_PROJECT), project=_TEST_PROJECT)
        assert updated is True

        row = fresh_db.get_sprint(label, project=_TEST_PROJECT)
        # Entry #20 has status='uat' → uat_count = 1
        assert row["summary_uat_count"] == 1

    def test_stored_uat_count_not_forwarded_verbatim(self, fresh_db):
        """The stored summary_uat_count value is not forwarded verbatim.

        This is the core behavioral change: previously the code explicitly
        preserved the stored value with 'stored_uat = int(row.get(...) or 0)'.
        The fix must NOT do this — any non-zero stored value that isn't backed
        by status='uat' entries in the merged list must be overwritten to 0.
        """
        label = "sprint-1295-noforward"
        fresh_db.record_sprint_ready_to_merge(label, end_reason="natural", project=_TEST_PROJECT)
        fresh_db.update_sprint_run_counts(
            label,
            json.dumps([{"ticket_id": 30, "number": 30, "state": "merged"}]),
            summary_settled_done=1,
            summary_uat_count=7,   # arbitrary stale value with no backing entries
            summary_failure_count=0,
            project=_TEST_PROJECT,
        )

        # Drift: new issue in agent_runs
        fresh_db.record_agent_start(31, label, "coder")
        fresh_db.record_agent_finish(31, label, "coder", outcome="completed")

        from routers import sprint_reconcile_service as svc
        updated = svc._reconcile_counts(label, fresh_db.get_sprint(label, project=_TEST_PROJECT), project=_TEST_PROJECT)
        assert updated is True

        row = fresh_db.get_sprint(label, project=_TEST_PROJECT)
        # Must not equal the stale stored value; must be re-derived to 0
        assert row["summary_uat_count"] != 7
        assert row["summary_uat_count"] == 0

    def test_multiple_uat_entries_counted_correctly(self, fresh_db):
        """When multiple merged-list entries have status='uat', all are counted."""
        label = "sprint-1295-multi-uat"
        fresh_db.record_sprint_ready_to_merge(label, end_reason="natural", project=_TEST_PROJECT)
        fresh_db.update_sprint_run_counts(
            label,
            json.dumps([
                {"ticket_id": 40, "number": 40, "state": "merged", "status": "uat"},
                {"ticket_id": 41, "number": 41, "state": "merged", "status": "uat"},
                {"ticket_id": 42, "number": 42, "state": "merged"},  # no status field
            ]),
            summary_settled_done=3,
            summary_uat_count=0,
            summary_failure_count=0,
            project=_TEST_PROJECT,
        )

        # Drift: extra issue
        fresh_db.record_agent_start(43, label, "coder")
        fresh_db.record_agent_finish(43, label, "coder", outcome="completed")

        from routers import sprint_reconcile_service as svc
        updated = svc._reconcile_counts(label, fresh_db.get_sprint(label, project=_TEST_PROJECT), project=_TEST_PROJECT)
        assert updated is True

        row = fresh_db.get_sprint(label, project=_TEST_PROJECT)
        assert row["summary_uat_count"] == 2  # entries #40 and #41 have status='uat'
