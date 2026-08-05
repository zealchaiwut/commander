"""Tests for issue #2200: Board/Estimates/Summaries panes must not silently
discard a sprint's own "ticket-failures" end_reason classification.

Same bug shape as #2197 (reconcile) and #2199 (History): _has_rework_tickets
only checks whether GitHub currently has an OPEN rework-labeled ticket. A
ticket can merge only after exhausting its fix-loop retries -- has_rework
then reads False even though the sprint manager itself classified the run
as end_reason="ticket-failures" at sprint-end. perf-coach sprint-121 is the
live repro: all 7 tickets eventually merged, but two exhausted LINT_FAIL
retries, and the sprint's end_reason is "ticket-failures".
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"
for _p in (str(_REPO_ROOT), str(_DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import db  # noqa: E402
import server as srv  # noqa: E402

_LABEL = "sprint-121"
_PROJECT = "owner/perf-coach"


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path):
    db_file = tmp_path / "test_2200.db"
    original = db.DB_PATH
    db.DB_PATH = db_file
    db.init_db()
    yield tmp_path
    db.DB_PATH = original


def _all_merged_issues() -> list[dict]:
    return [
        {"number": 1420, "title": "t1", "status": "done", "agent_status": "completed",
         "failure_reason": "Fix-loop exhausted after 2 attempt(s) (attempt 1: LINT_FAIL; attempt 2: LINT_FAIL)",
         "state": "merged"},
        {"number": 1525, "title": "t2", "status": "done", "agent_status": "completed",
         "failure_reason": "Fix-loop exhausted after 2 attempt(s) (attempt 1: LINT_FAIL; attempt 2: LINT_FAIL)",
         "state": "merged"},
    ]


def _seed_ticket_failures_sprint(project_root):
    import json as _json
    db.record_sprint_start(_LABEL, project=_PROJECT)
    state = {"sprint_label": _LABEL, "wall_clock_secs": 6000, "issues": _all_merged_issues()}
    db.ingest_sprint_run_artifact(_LABEL, state, project=_PROJECT)
    db.record_sprint_needs_rework(_LABEL, end_reason="ticket-failures", project=_PROJECT)
    # finish-card / outcome still read the disk state file for issue counts
    # even when the DB row is ingested (issue #1161's "own_run_outcome" gate
    # only decides whether to attempt ingestion, not whether disk is read).
    sprints_dir = project_root / ".commander" / "sprints"
    sprints_dir.mkdir(parents=True, exist_ok=True)
    (sprints_dir / f"{_LABEL}-state.json").write_text(_json.dumps(state), encoding="utf-8")


# ── finish-card (Board tab) ────────────────────────────────────────────────

class TestFinishCardPreservesTicketFailures:
    def test_finish_card_shows_has_rework_not_completed(self, tmp_path):
        from fastapi.testclient import TestClient
        client = TestClient(srv.app)
        project_root = tmp_path / "proj"
        _seed_ticket_failures_sprint(project_root)

        with patch("server._project_root_path", return_value=project_root), \
             patch("server._is_sprint_running", return_value=False), \
             patch("server._has_rework_tickets", return_value=False):
            resp = client.get(f"/api/sprints/{_LABEL}/finish-card", params={"project": _PROJECT})

        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "has_rework", (
            "A sprint whose own end_reason is 'ticket-failures' must show "
            f"has_rework on the Board card even when all tickets eventually "
            f"merged. Got state={data['state']!r}"
        )

    def test_finish_card_promotes_other_end_reasons_normally(self, tmp_path):
        """Regression guard: a genuinely clean sprint still shows completed."""
        import json as _json
        from fastapi.testclient import TestClient
        client = TestClient(srv.app)
        project_root = tmp_path / "proj"

        db.record_sprint_start(_LABEL, project=_PROJECT)
        clean_issues = [
            {"number": 1, "title": "t", "status": "done", "agent_status": "completed",
             "failure_reason": None, "state": "merged"},
        ]
        state = {"sprint_label": _LABEL, "wall_clock_secs": 100, "issues": clean_issues}
        db.ingest_sprint_run_artifact(_LABEL, state, project=_PROJECT)
        db.record_sprint_finish(_LABEL, project=_PROJECT)
        sprints_dir = project_root / ".commander" / "sprints"
        sprints_dir.mkdir(parents=True, exist_ok=True)
        (sprints_dir / f"{_LABEL}-state.json").write_text(_json.dumps(state), encoding="utf-8")

        with patch("server._project_root_path", return_value=project_root), \
             patch("server._is_sprint_running", return_value=False), \
             patch("server._has_rework_tickets", return_value=False):
            resp = client.get(f"/api/sprints/{_LABEL}/finish-card", params={"project": _PROJECT})

        assert resp.status_code == 200
        assert resp.json()["state"] == "completed"


# ── outcome (DB-ingested path — Estimates pane) ────────────────────────────

class TestOutcomeIngestedRowPreservesTicketFailures:
    def test_outcome_endpoint_shows_has_rework(self, tmp_path):
        from fastapi.testclient import TestClient
        client = TestClient(srv.app)
        project_root = tmp_path / "proj"
        _seed_ticket_failures_sprint(project_root)

        with patch("routers.estimates._project_root_path", return_value=project_root), \
             patch("routers.estimates._is_sprint_running", return_value=False), \
             patch("routers.estimates._has_rework_tickets", return_value=False):
            resp = client.get(f"/api/sprints/{_LABEL}/outcome", params={"project": _PROJECT})

        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "has_rework", (
            f"Outcome endpoint must preserve ticket-failures classification, got {data['state']!r}"
        )


# ── sprint summaries (Summaries pane) ──────────────────────────────────────

class TestSprintSummariesPreservesTicketFailures:
    def _issue(self):
        return {
            "number": 1589,
            "title": "Sprint 121 Executive Summary",
            "labels": [{"name": "sprint-summary"}, {"name": "sprint-121"}],
            "state": "closed",
            "url": "https://github.com/owner/perf-coach/issues/1589",
            "createdAt": "2026-07-22T00:00:00Z",
        }

    def test_summaries_endpoint_shows_has_rework_not_completed(self, tmp_path):
        from fastapi.testclient import TestClient
        client = TestClient(srv.app)
        project_root = tmp_path / "proj"
        _seed_ticket_failures_sprint(project_root)

        with patch("routers.sprint_summaries.github_client.get_repo_for_operation",
                    return_value=_PROJECT), \
             patch("routers.sprint_summaries.db.get_mirrored_issues", return_value=[self._issue()]), \
             patch("routers.sprint_summaries.github_client.list_open_issues_with_body",
                    return_value=[]), \
             patch("server._project_root_path", return_value=project_root), \
             patch("server._has_rework_tickets", return_value=False):
            resp = client.get("/api/sprints/summaries", params={"project": _PROJECT})

        assert resp.status_code == 200
        data = resp.json()
        matches = [s for s in data["summaries"] if s.get("number") == 1589]
        assert matches, f"Expected summary #1589 in response, got {data}"
        assert matches[0]["outcome"] == "has_rework", (
            "Summaries pane must preserve ticket-failures classification, "
            f"got outcome={matches[0]['outcome']!r}"
        )
