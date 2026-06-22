"""Tests for issue #1463: Scope all sprint DB writes to (label, project).

AC1  — transition_sprint_state ON CONFLICT target is (label, project).
AC2  — INSERT in transition_sprint_state always supplies a non-null project.
AC3  — _set_sprint_terminal scopes its UPDATE to (label, project).
AC4  — record_sprint_start / finish / ready_to_merge / needs_rework each scope to (label, project).
AC5  — update_sprint_run_counts scopes to (label, project).
AC6  — ingest_sprint_run_artifact scopes to (label, project).
AC8  — DELETE in 'deleted' transition uses WHERE label = ? AND project = ?.
AC10 — A write where project is empty or null does not affect any existing row.
AC11 — Writing sprint-66 for perf-coach never mutates commander's sprint-66 row.
AC12 — record_sprint_finish('sprint-66', project=A) updates only project A's row.
AC13 — Deleting a sprint removes exactly the (label, project) row.
AC14 — Cross-project isolation tests covering start, finish, needs_rework, delete.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"
for _p in (str(_REPO_ROOT), str(_DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import db  # noqa: E402

_COMMANDER = "zealchaiwut/commander"
_PERF_COACH = "zealchaiwut/perf-coach"
_LABEL = "sprint-66"


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path):
    """Fresh SQLite DB per test — no shared state."""
    db_file = tmp_path / "test_1463.db"
    original = db.DB_PATH
    db.DB_PATH = str(db_file)
    db.init_db()
    yield
    db.DB_PATH = original


def _count_rows(label: str) -> int:
    with db.get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM sprints WHERE label = ?", (label,)
        ).fetchone()[0]


def _get_row(label: str, project: str) -> dict | None:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM sprints WHERE label = ? AND project = ?",
            (label, project),
        ).fetchone()
    return dict(row) if row else None


def _seed_running(label: str, project: str) -> None:
    """Seed a running sprint row directly, bypassing state-machine guards."""
    with db.get_conn() as conn:
        db._create_sprint_lifecycle_tables(conn)
        conn.execute(
            """
            INSERT INTO sprints (label, project, state, created_at)
            VALUES (?, ?, 'running', '2026-01-01T00:00:00Z')
            ON CONFLICT(label, project) DO UPDATE SET state = 'running'
            """,
            (label, project),
        )
        conn.commit()


# ── AC1 / AC2: composite PK + running INSERT ──────────────────────────────────

class TestTransitionSprintStateRunning:
    """AC1/AC2: ON CONFLICT(label, project) and non-null project in INSERT."""

    def test_two_projects_can_coexist_same_label(self):
        """Both commander and perf-coach can have sprint-66 simultaneously."""
        db.record_sprint_start(_LABEL, project=_COMMANDER)
        db.record_sprint_start(_LABEL, project=_PERF_COACH)
        assert _count_rows(_LABEL) == 2, (
            "Two rows (one per project) must coexist for the same label"
        )

    def test_running_insert_scoped_to_project(self):
        """record_sprint_start for one project does not affect the other."""
        db.record_sprint_start(_LABEL, project=_COMMANDER)
        db.record_sprint_start(_LABEL, project=_PERF_COACH)

        cmd_row = _get_row(_LABEL, _COMMANDER)
        perf_row = _get_row(_LABEL, _PERF_COACH)
        assert cmd_row is not None, "commander row must exist"
        assert perf_row is not None, "perf-coach row must exist"
        assert cmd_row["project"] == _COMMANDER
        assert perf_row["project"] == _PERF_COACH

    def test_re_run_idempotent_within_same_project(self):
        """A second record_sprint_start for the same (label, project) updates in place."""
        db.record_sprint_start(_LABEL, project=_COMMANDER)
        db.record_sprint_start(_LABEL, project=_COMMANDER)
        assert _count_rows(_LABEL) == 1, "Second start must not create a duplicate row"


# ── AC3 / AC11 / AC12: _set_sprint_terminal project scoping ──────────────────

class TestSetSprintTerminalProjectScoped:
    """AC3/AC11/AC12: _set_sprint_terminal and record_sprint_finish only touch one project."""

    def setup_method(self):
        _seed_running(_LABEL, _COMMANDER)
        _seed_running(_LABEL, _PERF_COACH)

    def test_finish_only_updates_target_project(self):
        """AC12: record_sprint_finish for project A does not affect project B."""
        db.record_sprint_finish(_LABEL, project=_COMMANDER)
        cmd = _get_row(_LABEL, _COMMANDER)
        perf = _get_row(_LABEL, _PERF_COACH)
        assert cmd["state"] in ("completed", "ready_to_merge", "needs_rework"), (
            "commander row must have transitioned away from 'running'"
        )
        assert perf is not None, "perf-coach row must still exist"
        assert perf["state"] == "running", (
            "perf-coach row must remain 'running' — commander finish must not touch it"
        )

    def test_needs_rework_only_updates_target_project(self):
        """AC4: record_sprint_needs_rework for project A does not affect project B."""
        db.record_sprint_needs_rework(_LABEL, project=_COMMANDER)
        cmd = _get_row(_LABEL, _COMMANDER)
        perf = _get_row(_LABEL, _PERF_COACH)
        assert cmd["state"] == "needs_rework", (
            "commander row must transition to needs_rework"
        )
        assert perf is not None, "perf-coach row must still exist"
        assert perf["state"] == "running", (
            "perf-coach row must remain 'running' after commander needs_rework"
        )

    def test_ready_to_merge_only_updates_target_project(self):
        """AC4: record_sprint_ready_to_merge scoped to one project."""
        db.record_sprint_ready_to_merge(_LABEL, project=_COMMANDER)
        cmd = _get_row(_LABEL, _COMMANDER)
        perf = _get_row(_LABEL, _PERF_COACH)
        assert cmd["state"] == "ready_to_merge", "commander must be ready_to_merge"
        assert perf["state"] == "running", "perf-coach must remain running"

    def test_perf_coach_write_does_not_mutate_commander(self):
        """AC11: writing sprint-66 for perf-coach never mutates commander's row."""
        db.record_sprint_needs_rework(_LABEL, project=_PERF_COACH)
        cmd = _get_row(_LABEL, _COMMANDER)
        assert cmd is not None, "commander row must still exist"
        assert cmd["state"] == "running", (
            "commander sprint-66 must remain 'running' after perf-coach write"
        )


# ── AC8: deleted transition ───────────────────────────────────────────────────

class TestDeleteTransitionProjectScoped:
    """AC8/AC13: delete transition removes exactly the (label, project) row."""

    def setup_method(self):
        _seed_running(_LABEL, _COMMANDER)
        _seed_running(_LABEL, _PERF_COACH)

    def test_delete_removes_only_target_project_row(self):
        """AC13: deleting sprint-66 for commander removes only commander's row."""
        result = db.transition_sprint_state(
            _LABEL, "deleted", actor="manager", project=_COMMANDER
        )
        assert result.accepted, "delete transition must be accepted"
        cmd = _get_row(_LABEL, _COMMANDER)
        perf = _get_row(_LABEL, _PERF_COACH)
        assert cmd is None, "commander sprint-66 row must be gone after delete"
        assert perf is not None, "perf-coach sprint-66 row must still exist"

    def test_delete_other_project_leaves_first_intact(self):
        """AC13: deleting perf-coach's row does not affect commander's row."""
        db.transition_sprint_state(
            _LABEL, "deleted", actor="manager", project=_PERF_COACH
        )
        cmd = _get_row(_LABEL, _COMMANDER)
        perf = _get_row(_LABEL, _PERF_COACH)
        assert cmd is not None, "commander row must survive deletion of perf-coach row"
        assert perf is None, "perf-coach row must be gone"


# ── AC5: update_sprint_run_counts project scoping ─────────────────────────────

class TestUpdateSprintRunCountsProjectScoped:
    """AC5: update_sprint_run_counts scopes by (label, project)."""

    def setup_method(self):
        _seed_running(_LABEL, _COMMANDER)
        _seed_running(_LABEL, _PERF_COACH)

    def test_run_counts_update_only_target_project(self):
        """update_sprint_run_counts with project=commander does not touch perf-coach."""
        import json
        issues = json.dumps([{"ticket_id": 1, "state": "done"}])
        db.update_sprint_run_counts(
            _LABEL, issues, 1, 0, 0, project=_COMMANDER
        )
        cmd = _get_row(_LABEL, _COMMANDER)
        perf = _get_row(_LABEL, _PERF_COACH)
        assert cmd["issues_json"] == issues, "commander issues_json must be updated"
        assert perf.get("issues_json") in (None, "[]", ""), (
            "perf-coach issues_json must remain untouched"
        )

    def test_empty_project_is_noop(self):
        """AC10: update_sprint_run_counts with project='' must not alter any row."""
        import json
        original_cmd = _get_row(_LABEL, _COMMANDER)
        original_perf = _get_row(_LABEL, _PERF_COACH)

        db.update_sprint_run_counts(
            _LABEL, json.dumps([{"ticket_id": 99}]), 1, 0, 0, project=""
        )
        cmd_after = _get_row(_LABEL, _COMMANDER)
        perf_after = _get_row(_LABEL, _PERF_COACH)
        assert (cmd_after or {}).get("issues_json") == (original_cmd or {}).get("issues_json"), (
            "Empty project write must not update commander row"
        )
        assert (perf_after or {}).get("issues_json") == (original_perf or {}).get("issues_json"), (
            "Empty project write must not update perf-coach row"
        )


# ── AC6: ingest_sprint_run_artifact project scoping ───────────────────────────

class TestIngestSprintRunArtifactProjectScoped:
    """AC6: ingest_sprint_run_artifact scopes by (label, project)."""

    def setup_method(self):
        _seed_running(_LABEL, _COMMANDER)
        _seed_running(_LABEL, _PERF_COACH)

    def test_ingest_updates_only_target_project(self):
        """Ingest for commander does not touch perf-coach's row."""
        state = {
            "issues": [],
            "total_tokens_in": 700,
            "total_tokens_out": 300,
            "wall_clock_secs": 60,
            "reconciliation": None,
            "summary_issue_url": None,
            "pr_number": None,
        }
        db.ingest_sprint_run_artifact(_LABEL, state, project=_COMMANDER)
        cmd = _get_row(_LABEL, _COMMANDER)
        perf = _get_row(_LABEL, _PERF_COACH)
        assert cmd is not None
        # commander row must have tokens ingested (700+300=1000); perf-coach untouched
        assert cmd.get("tokens") == 1000, "commander row must have ingested tokens"
        assert (perf or {}).get("tokens") is None, (
            "perf-coach row must not receive commander's ingest tokens"
        )


# ── AC10: empty-project writes are no-ops ─────────────────────────────────────

class TestEmptyProjectWriteIsNoop:
    """AC10: A write where project is empty does not affect any existing row."""

    def test_record_sprint_needs_rework_empty_project_noop(self):
        """needs_rework with empty project must not transition any existing row."""
        _seed_running(_LABEL, _COMMANDER)
        before = _get_row(_LABEL, _COMMANDER)
        db.record_sprint_needs_rework(_LABEL, project="")
        after = _get_row(_LABEL, _COMMANDER)
        assert (after or {}).get("state") == (before or {}).get("state"), (
            "Empty-project needs_rework must not modify commander row"
        )


# ── Composite PK schema check ──────────────────────────────────────────────────

class TestCompositeKeySchemaExists:
    """Verify the sprints table has PRIMARY KEY (label, project)."""

    def test_composite_pk(self):
        """The sprints table's PK columns are label AND project (not label alone)."""
        with db.get_conn() as conn:
            db._create_sprint_lifecycle_tables(conn)
            info = conn.execute("PRAGMA table_info(sprints)").fetchall()
        pk_cols = sorted(r[1] for r in info if r[5] > 0)
        assert pk_cols == ["label", "project"], (
            f"Expected composite PK (label, project), got PK cols: {pk_cols}"
        )
