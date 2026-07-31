"""Tests for issue #1882: count-reconcile marked failed tickets as done.

AC1: _issues_from_agent_runs derives per-ticket state from the LATEST
     definitive outcome in run order (pass→fail ⇒ failed; fail→pass ⇒ done).
AC2: _reconcile_counts never settles a ticket to merged/done while its
     mirrored GitHub labels include needs-rework.
AC3: recomputed settled_done / failure_count reflect the corrected states.
AC4: a ticket re-run and passed in a child sprint is unaffected (child runs
     carry a different sprint_label; the parent unions only its own runs, and
     the mirror guard only fires while the needs-rework label is present).
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
sys.path.insert(0, str(_DASHBOARD_ROOT))

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")

_PROJ = "owner/perf-coach"


@pytest.fixture
def fresh_db(tmp_path):
    # Resolve `db` at fixture time, not import time: earlier tests in a full
    # run may replace sys.modules["db"], and sprint_reconcile_service._db()
    # always reads the current instance — a module bound at import time would
    # patch DB_PATH on a stale copy the service never sees.
    import importlib
    dbm = importlib.import_module("db")
    db_file = tmp_path / "test_1882.db"
    original = dbm.DB_PATH
    dbm.DB_PATH = str(db_file)
    dbm.init_db()
    yield dbm
    dbm.DB_PATH = original


def _run(db, issue, label, agent, outcome, project=_PROJ):
    rid = db.record_agent_start(issue, label, agent, project=project)
    db.record_agent_finish(issue, label, agent, outcome=outcome, run_id=rid)


def _mirror_issue(db, number, labels, state="open"):
    db.upsert_issues(_PROJ, [{
        "number": number,
        "title": f"ticket {number}",
        "state": state,
        "labels": [{"name": name, "color": "ededed"} for name in labels],
        "updatedAt": "2026-07-13T00:00:00Z",
    }])


# ── AC1: latest definitive outcome wins ──────────────────────────────────────


class TestLatestOutcomeWins:
    def test_pass_then_fail_is_failed(self, fresh_db):
        """perf-coach #1361 shape: initial tester pass, later fix-round fails."""
        from routers import sprint_reconcile_service as svc
        label = "sprint-105"
        _run(fresh_db, 1361, label, "coder", "success")
        _run(fresh_db, 1361, label, "tester", "pass")
        _run(fresh_db, 1361, label, "coder", "success")
        _run(fresh_db, 1361, label, "tester", "fail")

        issues = svc._issues_from_agent_runs(label, _PROJ)
        assert issues == [{
            "ticket_id": 1361, "number": 1361,
            "state": "closed", "agent_status": "failed",
        }]

    def test_fail_then_pass_is_done(self, fresh_db):
        """A fix round that ends in a pass settles the ticket done."""
        from routers import sprint_reconcile_service as svc
        label = "sprint-105"
        _run(fresh_db, 1362, label, "tester", "fail")
        _run(fresh_db, 1362, label, "coder", "success")
        _run(fresh_db, 1362, label, "tester", "pass")

        issues = svc._issues_from_agent_runs(label, _PROJ)
        assert issues == [{
            "ticket_id": 1362, "number": 1362,
            "state": "merged", "agent_status": "completed",
        }]

    def test_no_definitive_outcome_is_open(self, fresh_db):
        """A run that died without an outcome stays open (orphan mid-run)."""
        from routers import sprint_reconcile_service as svc
        label = "sprint-105"
        fresh_db.record_agent_start(1366, label, "tester", project=_PROJ)

        issues = svc._issues_from_agent_runs(label, _PROJ)
        assert issues == [{
            "ticket_id": 1366, "number": 1366,
            "state": "open", "agent_status": None,
        }]


# ── AC2 + AC3: mirrored needs-rework label blocks done, counts follow ─────────


class TestNeedsReworkLabelBlocksDone:
    def _seed_sprint(self, db, label, issues):
        db.record_sprint_needs_rework(label, end_reason="orphaned", project=_PROJ)
        db.ingest_sprint_run_artifact(label, {
            "sprint_label": label,
            "project": _PROJ,
            "issues": issues,
            "wall_clock_secs": 100,
        }, project=_PROJ)

    def test_gate_failed_ticket_not_settled_done(self, fresh_db):
        """perf-coach #1364 shape: coder+tester passed, lint gate failed after —
        no failed agent run, but GitHub carries needs-rework."""
        from routers import sprint_reconcile_service as svc
        label = "sprint-105"
        self._seed_sprint(fresh_db, label, [
            {"number": 1364, "title": "t", "status": "failed",
             "agent_status": "failed",
             "failure_reason": "Fix-loop exhausted after 2 attempt(s)"},
        ])
        _run(fresh_db, 1364, label, "coder", "success")
        _run(fresh_db, 1364, label, "tester", "pass")
        _mirror_issue(fresh_db, 1364, ["needs-rework", "sprint-105"])

        row = fresh_db.get_sprint(label, project=_PROJ)
        updated = svc._reconcile_counts(label, row, project=_PROJ)

        row = fresh_db.get_sprint(label, project=_PROJ)
        issues = json.loads(row["issues_json"])
        entry = next(i for i in issues
                     if int(i.get("ticket_id") or i.get("number")) == 1364)
        assert entry["state"] != "merged", (
            "ticket with a live needs-rework label must not settle as merged"
        )
        assert entry["agent_status"] == "failed"
        assert row["summary_settled_done"] == 0, "AC3: done count excludes it"
        assert row["summary_failure_count"] == 1
        assert updated is True or entry["state"] == "closed"

    def test_label_removed_after_child_rerun_allows_done(self, fresh_db):
        """AC4: once the needs-rework label is gone (fixed in a child sprint),
        the parent's passing runs settle the ticket done again."""
        from routers import sprint_reconcile_service as svc
        label = "sprint-105"
        self._seed_sprint(fresh_db, label, [
            {"number": 1364, "title": "t", "status": "failed",
             "agent_status": "failed", "failure_reason": "old failure"},
        ])
        _run(fresh_db, 1364, label, "coder", "success")
        _run(fresh_db, 1364, label, "tester", "pass")
        _mirror_issue(fresh_db, 1364, ["sprint-105"], state="closed")

        svc._reconcile_counts(label, fresh_db.get_sprint(label, project=_PROJ),
                              project=_PROJ)

        row = fresh_db.get_sprint(label, project=_PROJ)
        issues = json.loads(row["issues_json"])
        entry = next(i for i in issues
                     if int(i.get("ticket_id") or i.get("number")) == 1364)
        assert entry["state"] == "merged"
        assert row["summary_settled_done"] == 1

    def test_child_sprint_runs_do_not_leak_into_parent(self, fresh_db):
        """AC4: the parent sprint unions only its own runs — a child sprint's
        failing runs never downgrade the parent's ticket."""
        from routers import sprint_reconcile_service as svc
        parent, child = "sprint-105", "sprint-105.1"
        self._seed_sprint(fresh_db, parent, [
            {"number": 1362, "title": "t", "status": "done",
             "agent_status": "completed", "state": "merged"},
        ])
        _run(fresh_db, 1362, parent, "tester", "pass")
        _run(fresh_db, 1362, child, "tester", "fail")

        svc._reconcile_counts(parent, fresh_db.get_sprint(parent, project=_PROJ),
                              project=_PROJ)

        row = fresh_db.get_sprint(parent, project=_PROJ)
        issues = json.loads(row["issues_json"])
        entry = next(i for i in issues
                     if int(i.get("ticket_id") or i.get("number")) == 1362)
        assert entry["state"] == "merged", "child-sprint runs must not leak"
