"""Sprint-lifecycle redesign P1: unified lifecycle enum.

Design contract: docs/architecture/sprint-lifecycle.md
Tracking:        docs/milestones/sprint-lifecycle-redesign.md (P1)

One enum — draft / planned / running / ready_to_merge / needs_rework /
partial_finished (derived) / completed / deleted — used by the DB, plan.json,
and both panes. `cancelled` is never written again: every bad ending (ticket
failure, crash, orphan PID, user cancel) lands in needs_rework with the "why"
kept as end_reason. Legacy rows render through a display mapping (forward-only
migration, no DB rewrite).

AC-1: db.canonical_lifecycle maps legacy values (cancelled→needs_rework,
      failed→needs_rework, finished→completed, planning→draft).
AC-2: the sprints-table CHECK accepts the new states; pre-P1 databases are
      migrated in place (table rebuild) without losing rows.
AC-3: record_sprint_needs_rework / record_sprint_ready_to_merge write the new
      states; record_sprint_cancel and record_sprint_fail (legacy aliases)
      write needs_rework.
AC-4: orphan-PID reconciliation writes needs_rework + end_reason="process
      lost" (plan.json and DB) — no `cancelled` write site remains.
AC-5: the cancel endpoint writes needs_rework ("stopped by user"), posts the
      reason to the sprint summary issue, and never labels tickets
      need-rework.
AC-6: history rows map legacy states through the display mapping and the
      failed-state heuristics (_infer_failed_lifecycle) are retired; the one
      promotion kept is the recorded-fact rule (failed tickets ⇒
      needs_rework).
AC-7: partial_finished is derived server-side from children's states; a parent
      whose children are all completed flips to completed.
AC-8: the sprint manager writes the terminal state at the source:
      ready_to_merge when every ticket passed, needs_rework otherwise, and
      needs_rework + "stopped by user" on SIGTERM.
AC-9: both panes render the new vocabulary (History chips/verbs, board draft
      plan-state) and the bundle is rebuilt.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_DASHBOARD_ROOT))

os.environ.setdefault("DB_PATH", str(_REPO_ROOT / "commander.db"))

import db as _db_module  # noqa: E402

_SERVER_SRC = (_DASHBOARD_ROOT / "server.py").read_text(encoding="utf-8")
_SM_SRC = (
    _REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"
).read_text(encoding="utf-8")
_PROJECT_HTML = (
    _DASHBOARD_ROOT / "static" / "project.html"
).read_text(encoding="utf-8")
_BOARD_RENDER_SRC = (
    _DASHBOARD_ROOT / "static" / "src" / "sprint-board" / "board-render.js"
).read_text(encoding="utf-8")
_BUNDLE_SRC = (
    _DASHBOARD_ROOT / "static" / "dist" / "bundle.js"
).read_text(encoding="utf-8")


@pytest.fixture
def fresh_db(tmp_path):
    """Isolated SQLite DB; yields the patched db module."""
    db_file = tmp_path / "test_p1.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    yield _db_module
    _db_module.DB_PATH = original


# ── AC-1: canonical lifecycle mapping ─────────────────────────────────────────

class TestCanonicalLifecycle:
    @pytest.mark.parametrize("raw,expected", [
        ("cancelled", "needs_rework"),
        ("canceled", "needs_rework"),
        ("failed", "needs_rework"),
        ("has_rework", "needs_rework"),
        ("finished", "completed"),
        ("complete", "completed"),
        ("completed", "completed"),
        ("planning", "draft"),
        ("running", "running"),
        ("deleted", "deleted"),
        ("ready_to_merge", "ready_to_merge"),
        ("needs_rework", "needs_rework"),
        ("partial_finished", "partial_finished"),
        (None, "unknown"),
        ("", "unknown"),
    ])
    def test_mapping(self, raw, expected):
        assert _db_module.canonical_lifecycle(raw) == expected


# ── AC-2: schema accepts new states + in-place migration ─────────────────────

class TestSchemaMigration:
    def test_new_states_accepted(self, fresh_db):
        with fresh_db.get_conn() as conn:
            fresh_db._create_sprint_lifecycle_tables(conn)
            for state in ("draft", "planned", "ready_to_merge", "needs_rework"):
                conn.execute(
                    "INSERT INTO sprints (label, state) VALUES (?, ?)",
                    (f"sprint-{state}", state),
                )
            conn.commit()

    def test_pre_p1_check_constraint_is_migrated(self, fresh_db):
        # Build the pre-P1 table by hand, with a legacy row in it.
        with sqlite3.connect(fresh_db.DB_PATH) as conn:
            conn.execute(
                """
                CREATE TABLE sprints (
                    label        TEXT PRIMARY KEY,
                    project      TEXT NOT NULL DEFAULT '',
                    state        TEXT NOT NULL DEFAULT 'planning'
                                 CHECK(state IN (
                                     'planning', 'running', 'completed',
                                     'cancelled', 'failed'
                                 )),
                    created_at   TEXT,
                    started_at   TEXT,
                    ended_at     TEXT,
                    end_reason   TEXT,
                    parent_label TEXT
                )
                """
            )
            conn.execute(
                "INSERT INTO sprints (label, project, state) "
                "VALUES ('sprint-old', 'p', 'cancelled')"
            )
            conn.commit()

        # Any lifecycle write triggers the migration; needs_rework must land.
        fresh_db.record_sprint_needs_rework("sprint-new", end_reason="process lost")

        legacy = fresh_db.get_sprint("sprint-old")
        assert legacy is not None and legacy["state"] == "cancelled", (
            "forward-only migration must keep legacy rows verbatim"
        )
        assert fresh_db.get_sprint("sprint-new")["state"] == "needs_rework"


# ── AC-3: new writers + legacy aliases ────────────────────────────────────────

class TestTerminalWriters:
    def test_needs_rework_writer(self, fresh_db):
        fresh_db.record_sprint_start("sprint-1", project="p")
        fresh_db.record_sprint_needs_rework("sprint-1", end_reason="ticket-failures")
        row = fresh_db.get_sprint("sprint-1")
        assert row["state"] == "needs_rework"
        assert row["end_reason"] == "ticket-failures"

    def test_ready_to_merge_writer(self, fresh_db):
        fresh_db.record_sprint_start("sprint-2", project="p")
        fresh_db.record_sprint_ready_to_merge("sprint-2", end_reason="natural")
        assert fresh_db.get_sprint("sprint-2")["state"] == "ready_to_merge"

    def test_cancel_alias_writes_needs_rework(self, fresh_db):
        fresh_db.record_sprint_cancel("sprint-3")
        row = fresh_db.get_sprint("sprint-3")
        assert row["state"] == "needs_rework"
        assert row["end_reason"] == "stopped by user"

    def test_fail_alias_writes_needs_rework(self, fresh_db):
        fresh_db.record_sprint_fail("sprint-4", end_reason="no-dispatchable-tickets")
        assert fresh_db.get_sprint("sprint-4")["state"] == "needs_rework"


# ── AC-4: orphan-PID reconciliation writes needs_rework ──────────────────────

class TestOrphanReconcile:
    def test_is_sprint_running_reconciles_dead_plan(self, fresh_db, tmp_path):
        from server import _is_sprint_running

        project_root = tmp_path / "proj"
        sprints_dir = project_root / ".commander" / "sprints"
        sprints_dir.mkdir(parents=True)
        plan_path = sprints_dir / "sprint-9-plan.json"
        plan_path.write_text(
            json.dumps({"state": "running", "tickets": [1]}), encoding="utf-8"
        )
        # No PID file at all → the running claim is dead.
        assert _is_sprint_running(project_root, "sprint-9") is False
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        assert plan["state"] == "needs_rework"
        assert plan["end_reason"] == "process lost"

    def test_no_cancelled_write_sites_left_in_server(self):
        assert '_plan_json_set_state(project_root, sprint_label, "cancelled"' \
            not in _SERVER_SRC
        assert 'data["state"] = "cancelled"' not in _SERVER_SRC
        assert 'data["status"] = "cancelled"' not in _SERVER_SRC

    def test_not_running_plan_states_cover_new_terminals(self):
        from server import _NOT_RUNNING_PLAN_STATES
        for state in ("ready_to_merge", "needs_rework", "draft", "planned",
                      "completed", "cancelled", "planning"):
            assert state in _NOT_RUNNING_PLAN_STATES


# ── AC-5: cancel endpoint behaviour (source contract) ─────────────────────────

class TestCancelEndpoint:
    def _kill_sprint_body(self) -> str:
        m = re.search(
            r"def kill_sprint\(.*?(?=\n@app\.|\ndef [a-z_]+\()", _SERVER_SRC,
            re.DOTALL,
        )
        assert m, "kill_sprint not found in server.py"
        return m.group(0)

    def test_writes_needs_rework_with_reason(self):
        body = self._kill_sprint_body()
        assert '"needs_rework"' in body
        assert "stopped by user" in body
        assert '"cancelled"' not in body or 'in ("cancelled", "needs_rework")' in body

    def test_posts_reason_to_summary_issue(self):
        body = self._kill_sprint_body()
        assert "_read_sprint_summary_url" in body
        assert "add_comment" in body

    def test_never_labels_tickets_need_rework(self):
        # A user cancel must not mutate any ticket labels — no GitHub label
        # call of any kind in the endpoint.
        body = self._kill_sprint_body()
        assert "update_labels" not in body
        assert "add_labels" not in body


# ── AC-6 / AC-7: history mapping, retired heuristics, partial_finished ───────

def _rec(label, state, **kw):
    base = {
        "label": label, "project": "p", "lifecycle_state": state,
        "end_reason": kw.pop("end_reason", None), "duration": 100,
        "tokens": None, "estimate_accuracy": None, "pr_number": None,
        "summary_path": None, "summary_issue_url": None,
        "summary_issue_num": None, "reconciliation": None,
        "issues": kw.pop("issues", [{"ticket_id": 1}]),
        "failed_tickets": kw.pop("failed_tickets", []),
        "failure_reason": None, "post_sprint": {"note": "x"},
        "_sort_key": label,
    }
    base.update(kw)
    return base


class TestHistoryLifecycle:
    def test_normalize_maps_legacy(self):
        from routers.sprint_history_service import _normalize_state
        assert _normalize_state("cancelled") == "needs_rework"
        assert _normalize_state("failed") == "needs_rework"
        assert _normalize_state("planning") == "draft"
        assert _normalize_state("finished") == "completed"

    def test_infer_failed_heuristics_retired(self):
        import routers.sprint_history_service as svc
        assert not hasattr(svc, "_infer_failed_lifecycle"), (
            "the failed-state heuristics must be retired — needs_rework is "
            "written at the source now"
        )

    def test_failed_tickets_promote_to_needs_rework(self, tmp_path):
        from routers.sprint_history_service import _finalize_records
        rec = _rec("sprint-5", "ready_to_merge",
                   failed_tickets=[{"ticket_id": 7, "failure_reason": "boom"}])
        _finalize_records([rec], tmp_path)
        assert rec["lifecycle_state"] == "needs_rework"
        assert rec["failure_reason"] == "boom"

    def test_failed_tickets_do_not_downgrade_completed(self, tmp_path):
        from routers.sprint_history_service import _finalize_records
        rec = _rec("sprint-5", "completed",
                   failed_tickets=[{"ticket_id": 7, "failure_reason": "boom"}])
        _finalize_records([rec], tmp_path)
        assert rec["lifecycle_state"] == "completed"

    def test_parent_with_unfinished_child_is_partial_finished(self, tmp_path):
        from routers.sprint_history_service import _finalize_records
        parent = _rec("sprint-68", "needs_rework")
        child = _rec("sprint-68.1", "needs_rework")
        _finalize_records([parent, child], tmp_path)
        assert parent["lifecycle_state"] == "partial_finished"
        assert parent["partial_children"] == ["sprint-68.1"]
        assert child["lifecycle_state"] == "needs_rework"

    def test_parent_flips_completed_when_children_settle(self, tmp_path):
        from routers.sprint_history_service import _finalize_records
        parent = _rec("sprint-68", "needs_rework")
        child = _rec("sprint-68.1", "completed")
        _finalize_records([parent, child], tmp_path)
        assert parent["lifecycle_state"] == "completed"

    def test_running_parent_never_partial(self, tmp_path):
        from routers.sprint_history_service import _finalize_records
        parent = _rec("sprint-70", "running")
        child = _rec("sprint-70.1", "needs_rework")
        _finalize_records([parent, child], tmp_path)
        assert parent["lifecycle_state"] == "running"


# ── AC-8: sprint manager writes terminal state at the source ─────────────────

class TestSprintManagerSource:
    def test_sigterm_writes_needs_rework(self):
        m = re.search(
            r"def _sigterm_handler\(.*?raise SystemExit\(130\)", _SM_SRC, re.DOTALL
        )
        assert m, "_sigterm_handler not found"
        body = m.group(0)
        assert '"needs_rework"' in body
        assert "stopped by user" in body
        assert '"cancelled"' not in body

    def test_clean_exit_writes_unified_terminals(self):
        assert '"ready_to_merge"' in _SM_SRC
        assert '"ticket-failures"' in _SM_SRC
        # The old unconditional completed/natural clean-exit write is gone.
        assert '_plan_json_set_state_sm(\n            args.label, "completed"' \
            not in _SM_SRC

    def test_db_mirror_has_new_branches(self):
        m = re.search(
            r"def _sprint_db_set_state_sm\(.*?\n\ndef ", _SM_SRC, re.DOTALL
        )
        assert m, "_sprint_db_set_state_sm not found"
        body = m.group(0)
        assert "record_sprint_ready_to_merge" in body
        assert "record_sprint_needs_rework" in body


# ── AC-9: pane sources render the new vocabulary ──────────────────────────────

class TestPaneSources:
    def test_history_chip_map_has_new_states(self):
        # Enum keys in board/history lifecycle maps (sprint-lifecycle.md).
        for needle in ("needs_rework", "ready_to_merge", "partial_finished"):
            assert needle in _PROJECT_HTML
        # Human-readable labels in history chips and sprint card badges.
        # History chips use short labels ('Partial'); card badges use ALL CAPS.
        for needle in (
            "Ready to merge", "Partial", "PARTIAL FINISHED",
            "NEEDS REWORK", "Needs rework",
        ):
            assert needle in _PROJECT_HTML, f"missing lifecycle label: {needle!r}"

    def test_resume_verb_removed(self):
        assert "_histResumeSprint" not in _PROJECT_HTML

    def test_board_accepts_draft_plan_state(self):
        assert "planState === 'draft'" in _BOARD_RENDER_SRC

    def test_bundle_is_rebuilt(self):
        # esbuild normalizes quotes, so match both spellings.
        assert ("planState === 'draft'" in _BUNDLE_SRC
                or 'planState === "draft"' in _BUNDLE_SRC), (
            "static/dist/bundle.js is stale — run `npm run build`"
        )
