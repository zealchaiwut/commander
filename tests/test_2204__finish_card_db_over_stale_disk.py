"""Tests for issue #2204: finish-card must prefer the DB row's issues_json/
end_reason over the on-disk state.json snapshot once the sprint is ingested.

Bug (perf-coach sprint-121): the on-disk sprint-{n}-state.json is written
once at the ORIGINAL run and never updated by reconciliation. The DB row
(issues_json, end_reason) is kept fresh -- e.g. reconcile settled sprint-121
from 5 done tickets (disk snapshot) to 7 done/2 failed (DB, post-settle) with
end_reason="ticket-failures" (also DB-only; the disk sprint.json never had
it). finish-card computed done_count/failed_count from the stale disk issues
and end_reason from a separate stale disk file, so it kept showing
done_count=5/end_reason=null long after the DB had settled to the correct
values -- while /api/board (board_service.py, which reads issues_json from
the DB directly) showed the correct done_count=7/failed_count=2. The
standalone finish-card endpoint is still hit per-sprint by board-render.js,
so its stale values were overwriting the correct aggregate ones in the
browser.
"""
from __future__ import annotations

import json
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
    db_file = tmp_path / "test_2204.db"
    original = db.DB_PATH
    db.DB_PATH = db_file
    db.init_db()
    yield tmp_path
    db.DB_PATH = original


def _seed_diverged_disk_vs_db(project_root):
    """DB is settled (7 done/2 failed, end_reason=ticket-failures); disk is
    the stale original-run snapshot (5 done, no end_reason)."""
    db.record_sprint_start(_LABEL, project=_PROJECT)

    # Settled DB issues_json: 7 done (2 with a failure_reason annotation from
    # an exhausted-but-ultimately-merged fix-loop), matching board_service.py's
    # state/agent_status-based schema.
    settled_issues = [
        {"number": n, "state": "merged", "agent_status": "completed", "failure_reason": None}
        for n in (849, 901, 1306, 1399, 1473)
    ] + [
        {"number": n, "state": "merged", "agent_status": "completed",
         "failure_reason": "Fix-loop exhausted after 2 attempt(s) (attempt 1: LINT_FAIL; attempt 2: LINT_FAIL)"}
        for n in (1420, 1525)
    ]
    db.ingest_sprint_run_artifact(_LABEL, {
        "sprint_label": _LABEL, "wall_clock_secs": 6432, "issues": settled_issues,
    }, project=_PROJECT)
    db.record_sprint_needs_rework(_LABEL, end_reason="ticket-failures", project=_PROJECT)

    # Stale disk snapshot: only 5 issues, disk's own status-based schema, no
    # end_reason key at all (matches the real sprint-121-state.json shape).
    stale_disk_issues = [
        {"number": n, "status": "done", "agent_status": "completed", "failure_reason": None}
        for n in (849, 901, 1306, 1399, 1473)
    ]
    sprints_dir = project_root / ".commander" / "sprints"
    sprints_dir.mkdir(parents=True, exist_ok=True)
    (sprints_dir / f"{_LABEL}-state.json").write_text(json.dumps({
        "sprint_label": _LABEL, "wall_clock_secs": 6431.95, "issues": stale_disk_issues,
    }), encoding="utf-8")
    (sprints_dir.parent / f"sprint-121.json").write_text(json.dumps({
        "status": "completed",  # stale: no end_reason key at all
    }), encoding="utf-8")


class TestFinishCardPrefersDbOverStaleDisk:
    def test_done_count_reflects_settled_db_not_stale_disk(self, tmp_path):
        from fastapi.testclient import TestClient
        client = TestClient(srv.app)
        project_root = tmp_path / "proj"
        _seed_diverged_disk_vs_db(project_root)

        with patch("server._project_root_path", return_value=project_root), \
             patch("server._is_sprint_running", return_value=False), \
             patch("server._has_rework_tickets", return_value=False):
            resp = client.get(f"/api/sprints/{_LABEL}/finish-card", params={"project": _PROJECT})

        assert resp.status_code == 200
        data = resp.json()
        assert data["done_count"] == 7, (
            f"done_count must come from the settled DB row (7), not the "
            f"stale disk snapshot (5); got {data['done_count']!r}"
        )
        assert data["failed_count"] == 2, f"got {data['failed_count']!r}"

    def test_end_reason_reflects_db_not_stale_disk(self, tmp_path):
        from fastapi.testclient import TestClient
        client = TestClient(srv.app)
        project_root = tmp_path / "proj"
        _seed_diverged_disk_vs_db(project_root)

        with patch("server._project_root_path", return_value=project_root), \
             patch("server._is_sprint_running", return_value=False), \
             patch("server._has_rework_tickets", return_value=False):
            resp = client.get(f"/api/sprints/{_LABEL}/finish-card", params={"project": _PROJECT})

        assert resp.status_code == 200
        data = resp.json()
        assert data["end_reason"] == "ticket-failures", (
            f"end_reason must come from the DB row, not the stale disk file "
            f"(which has no end_reason key); got {data['end_reason']!r}"
        )
        assert data["state"] == "has_rework"
