"""Sprint-lifecycle redesign P4: UI polish.

Design contract: docs/architecture/sprint-lifecycle.md
Tracking:        docs/milestones/sprint-lifecycle-redesign.md (P4)

Board uses unified lifecycle badges (no Cancelled variant, no run-stats/log on
the board). History shows partial_finished child links instead of empty ticket
rows, refreshes on sprint_finished / sprint_reconciled SSE, and run-stats emits
``crash`` only for needs_rework sprints.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"
for _p in (str(_REPO_ROOT), str(_DASHBOARD_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
_PROJECT_HTML = (_DASHBOARD_ROOT / "static" / "project.html").read_text(encoding="utf-8")
_BOARD_RENDER_SRC = (
    _DASHBOARD_ROOT / "static" / "src" / "sprint-board" / "board-render.js"
).read_text(encoding="utf-8")
_BUNDLE_SRC = (_DASHBOARD_ROOT / "static" / "dist" / "bundle.js").read_text(encoding="utf-8")
_RUN_STATS_SRC = (
    _DASHBOARD_ROOT / "routers" / "run_stats_service.py"
).read_text(encoding="utf-8")


def _seed_agent_runs(db_module, rows: list[dict]) -> None:
    with db_module.get_conn() as conn:
        db_module._create_agent_runs_table(conn)
        for r in rows:
            conn.execute(
                "INSERT INTO agent_runs "
                "(issue_number, sprint_label, agent, started_at, finished_at, "
                " duration_seconds, outcome, total_tokens, attempt_kind) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    r.get("issue_number", 1),
                    r["sprint_label"],
                    r["agent"],
                    r.get("started_at", "2026-06-12T10:00:00+00:00"),
                    r.get("finished_at"),
                    r.get("duration_seconds"),
                    r.get("outcome", "passed"),
                    r.get("total_tokens"),
                    r.get("attempt_kind", "initial"),
                ),
            )
        conn.commit()


@pytest.fixture()
def stats_db(tmp_path, monkeypatch):
    db_path = tmp_path / "p4_run_stats.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    import db as db_module
    from routers import run_stats_service
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    importlib.reload(run_stats_service)
    return run_stats_service, db_module


# ── Board: unified badges, no cancelled / run-stats ───────────────────────────

class TestBoardPane:
    def test_state_meta_uses_unified_lifecycle(self):
        for needle in ("needs_rework", "ready_to_merge", "partial_finished", "NEEDS REWORK"):
            assert needle in _PROJECT_HTML

    def test_cancelled_card_class_removed(self):
        assert ".smgmt-sprint-card.smgmt-cancelled" not in _PROJECT_HTML

    def test_board_does_not_render_outcome_log(self):
        assert "_smgmtOutcomeLogHtml(label, outcome)" not in _BOARD_RENDER_SRC
        assert "const logHtml = ''" in _BOARD_RENDER_SRC

    def test_finish_card_drops_cancelled_variant(self):
        assert "_sfcCancelledHtml" not in _BOARD_RENDER_SRC
        assert "state === 'cancelled'" not in _BOARD_RENDER_SRC.split("_smgmtFinishCardInnerHtml")[1][:800]

    def test_bundle_is_rebuilt(self):
        assert "needs_rework" in _BUNDLE_SRC, (
            "static/dist/bundle.js is stale — run `npm run build`"
        )
        assert 'const logHtml = ""' in _BUNDLE_SRC

    def test_post_run_linger_keeps_running_view_greyscaled(self):
        assert "_SMGMT_LINGER_MS" in _PROJECT_HTML
        assert "60 * 60 * 1000" in _PROJECT_HTML
        assert "_smgmtLingerStart" in _PROJECT_HTML
        assert "smgmt-linger" in _PROJECT_HTML
        assert "grayscale" in _PROJECT_HTML
        assert "_smgmtLingerStart" in _BOARD_RENDER_SRC


# ── History: partial_finished + auto-refresh ────────────────────────────────

class TestHistoryPane:
    def test_partial_children_helper(self):
        assert "_histPartialChildrenHtml" in _PROJECT_HTML
        assert "partial_children" in _PROJECT_HTML

    def test_sse_wires_sprint_finished_and_reconciled(self):
        assert "_projConnectSse" in _PROJECT_HTML
        assert "sprint_finished" in _PROJECT_HTML
        assert "sprint_reconciled" in _PROJECT_HTML
        body = _PROJECT_HTML.split("function _projConnectSse")[1][:700]
        assert "_histLoadLedger(repo)" in body

    def test_auto_refresh_also_on_board_poll(self):
        body = _PROJECT_HTML.split("async function _smgmtArDoRefresh")[1][:400]
        assert "_histLoadLedger" in body


# ── Run-stats: crash only on needs_rework ─────────────────────────────────────

class TestRunStatsCrash:
    def test_backend_gates_crash_on_needs_rework(self):
        assert 'lifecycle == "needs_rework"' in _RUN_STATS_SRC

    def test_crash_absent_for_ready_to_merge(self, stats_db):
        svc, db = stats_db
        db.record_sprint_start("sprint-1", project="o/r")
        db.record_sprint_ready_to_merge("sprint-1")
        _seed_agent_runs(db, [{
            "sprint_label": "sprint-1", "issue_number": 1, "agent": "coder",
            "started_at": "2026-06-12T10:00:00+00:00",
            "finished_at": "2026-06-12T10:05:00+00:00",
            "duration_seconds": 300, "outcome": "success",
        }])
        stats = svc.sprint_run_stats("sprint-1", project="o/r")
        assert stats["has_runs"] is True
        assert stats.get("crash") is None

    def test_crash_present_for_needs_rework(self, stats_db):
        svc, db = stats_db
        db.record_sprint_start("sprint-2", project="o/r")
        db.record_sprint_needs_rework("sprint-2", end_reason="ticket-failures")
        _seed_agent_runs(db, [{
            "sprint_label": "sprint-2", "issue_number": 2, "agent": "coder",
            "started_at": "2026-06-12T10:00:00+00:00",
            "finished_at": "2026-06-12T10:05:00+00:00",
            "duration_seconds": 300, "outcome": "failed",
        }])
        stats = svc.sprint_run_stats("sprint-2", project="o/r")
        assert stats["has_runs"] is True
        assert stats.get("crash") is not None
        assert stats["crash"]["ticket"] == 2
