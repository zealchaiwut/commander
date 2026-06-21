"""Tests for issue #1460 — Backfill sprints.project for legacy unattributed rows.

AC(a) — Resolution via agent_runs.project for matching sprint_label.
AC(b) — Resolution via disk file discovery (.commander/sprints/<label>-plan.json).
AC(c) — Unresolved row: warning logged, project left empty.
AC(d) — Second run is a no-op (idempotent).
Extra — sprint_history rows with empty project are also backfilled.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"
for _p in (str(_REPO_ROOT), str(_DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import db  # noqa: E402

_PROJ_A = "zealchaiwut/commander"
_PROJ_B = "zealchaiwut/perf-coach"


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path):
    db_file = tmp_path / "test_1460.db"
    original = db.DB_PATH
    db.DB_PATH = str(db_file)
    db.init_db()
    yield
    db.DB_PATH = original


def _insert_sprint_empty_project(label: str) -> None:
    with db.get_conn() as conn:
        db._create_sprint_lifecycle_tables(conn)
        conn.execute(
            "INSERT OR IGNORE INTO sprints (label, project, state, created_at) "
            "VALUES (?, '', 'completed', '2026-01-01T00:00:00Z')",
            (label,),
        )
        conn.commit()


def _insert_agent_run(sprint_label: str, project: str) -> None:
    with db.get_conn() as conn:
        db._create_agent_runs_table(conn)
        conn.execute(
            "INSERT INTO agent_runs (issue_number, sprint_label, agent, started_at, project) "
            "VALUES (1, ?, 'coder', '2026-01-01T00:00:00Z', ?)",
            (sprint_label, project),
        )
        conn.commit()


def _insert_sprint_history_empty_project(label: str) -> None:
    with db.get_conn() as conn:
        db._create_sprint_history_table(conn)
        conn.execute(
            "INSERT INTO sprint_history (label, project, lifecycle_state, issues_json, created_at) "
            "VALUES (?, '', 'completed', '[]', '2026-01-01T00:00:00Z')",
            (label,),
        )
        conn.commit()


def _read_sprint_project(label: str) -> str:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT project FROM sprints WHERE label = ?", (label,)
        ).fetchone()
    return row["project"] if row else ""


def _read_sprint_history_project(label: str) -> str:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT project FROM sprint_history WHERE label = ? LIMIT 1", (label,)
        ).fetchone()
    return row["project"] if row else ""


def _count_empty_sprints() -> int:
    with db.get_conn() as conn:
        return conn.execute(
            "SELECT count(*) FROM sprints WHERE project = ''"
        ).fetchone()[0]


# ── AC(a): resolution via agent_runs.project ─────────────────────────────────

class TestResolveViaAgentRuns:
    """AC(a): empty sprint.project is filled from agent_runs.project."""

    def test_sprint_gets_project_from_agent_runs(self, tmp_path):
        _insert_sprint_empty_project("sprint-50")
        _insert_agent_run("sprint-50", _PROJ_A)

        projects_file = tmp_path / "projects.json"
        projects_file.write_text("[]")

        with db.get_conn() as conn:
            db._backfill_sprint_project(
                conn,
                projects_file=projects_file,
                projects_base=tmp_path,
            )

        assert _read_sprint_project("sprint-50") == _PROJ_A

    def test_sprint_history_gets_project_from_agent_runs(self, tmp_path):
        _insert_sprint_history_empty_project("sprint-50")
        _insert_agent_run("sprint-50", _PROJ_A)

        projects_file = tmp_path / "projects.json"
        projects_file.write_text("[]")

        with db.get_conn() as conn:
            db._backfill_sprint_project(
                conn,
                projects_file=projects_file,
                projects_base=tmp_path,
            )

        assert _read_sprint_history_project("sprint-50") == _PROJ_A

    def test_prefers_agent_runs_over_disk(self, tmp_path):
        """agent_runs strategy wins even when disk file also exists."""
        _insert_sprint_empty_project("sprint-51")
        _insert_agent_run("sprint-51", _PROJ_A)

        # Also place a disk file for a DIFFERENT project
        slug_b = _PROJ_B.split("/")[-1]
        sprint_dir = tmp_path / slug_b / ".commander" / "sprints"
        sprint_dir.mkdir(parents=True)
        (sprint_dir / "sprint-51-plan.json").write_text("{}")

        projects_file = tmp_path / "projects.json"
        projects_file.write_text(json.dumps([{"repo": _PROJ_B}]))

        with db.get_conn() as conn:
            db._backfill_sprint_project(
                conn,
                projects_file=projects_file,
                projects_base=tmp_path,
            )

        assert _read_sprint_project("sprint-51") == _PROJ_A


# ── AC(b): resolution via disk file discovery ─────────────────────────────────

class TestResolveViaDisk:
    """AC(b): empty sprint.project is filled from .commander/sprints/<label>-plan.json."""

    def test_resolves_via_plan_json(self, tmp_path):
        _insert_sprint_empty_project("sprint-52")

        slug = _PROJ_A.split("/")[-1]
        sprint_dir = tmp_path / slug / ".commander" / "sprints"
        sprint_dir.mkdir(parents=True)
        (sprint_dir / "sprint-52-plan.json").write_text("{}")

        projects_file = tmp_path / "projects.json"
        projects_file.write_text(json.dumps([{"repo": _PROJ_A}]))

        with db.get_conn() as conn:
            db._backfill_sprint_project(
                conn,
                projects_file=projects_file,
                projects_base=tmp_path,
            )

        assert _read_sprint_project("sprint-52") == _PROJ_A

    def test_resolves_via_state_json(self, tmp_path):
        _insert_sprint_empty_project("sprint-53")

        slug = _PROJ_A.split("/")[-1]
        sprint_dir = tmp_path / slug / ".commander" / "sprints"
        sprint_dir.mkdir(parents=True)
        (sprint_dir / "sprint-53-state.json").write_text("{}")

        projects_file = tmp_path / "projects.json"
        projects_file.write_text(json.dumps([{"repo": _PROJ_A}]))

        with db.get_conn() as conn:
            db._backfill_sprint_project(
                conn,
                projects_file=projects_file,
                projects_base=tmp_path,
            )

        assert _read_sprint_project("sprint-53") == _PROJ_A

    def test_sprint_history_resolves_via_disk(self, tmp_path):
        _insert_sprint_history_empty_project("sprint-54")

        slug = _PROJ_A.split("/")[-1]
        sprint_dir = tmp_path / slug / ".commander" / "sprints"
        sprint_dir.mkdir(parents=True)
        (sprint_dir / "sprint-54-plan.json").write_text("{}")

        projects_file = tmp_path / "projects.json"
        projects_file.write_text(json.dumps([{"repo": _PROJ_A}]))

        with db.get_conn() as conn:
            db._backfill_sprint_project(
                conn,
                projects_file=projects_file,
                projects_base=tmp_path,
            )

        assert _read_sprint_history_project("sprint-54") == _PROJ_A

    def test_fully_resolvable_db_has_zero_empty(self, tmp_path):
        """AC3: SELECT count(*) FROM sprints WHERE project='' returns 0."""
        for i in range(3):
            label = f"sprint-{60 + i}"
            _insert_sprint_empty_project(label)
            slug = _PROJ_A.split("/")[-1]
            sprint_dir = tmp_path / slug / ".commander" / "sprints"
            sprint_dir.mkdir(parents=True, exist_ok=True)
            (sprint_dir / f"{label}-plan.json").write_text("{}")

        projects_file = tmp_path / "projects.json"
        projects_file.write_text(json.dumps([{"repo": _PROJ_A}]))

        with db.get_conn() as conn:
            db._backfill_sprint_project(
                conn,
                projects_file=projects_file,
                projects_base=tmp_path,
            )

        assert _count_empty_sprints() == 0


# ── AC(c): unresolved → warning logged, row left empty ───────────────────────

class TestUnresolvedWarning:
    """AC(c): unresolvable label emits WARNING containing the label; row stays empty."""

    def test_warning_logged_for_unresolved(self, tmp_path, caplog):
        _insert_sprint_empty_project("sprint-99")

        projects_file = tmp_path / "projects.json"
        projects_file.write_text("[]")

        with caplog.at_level(logging.WARNING, logger="db"):
            with db.get_conn() as conn:
                db._backfill_sprint_project(
                    conn,
                    projects_file=projects_file,
                    projects_base=tmp_path,
                )

        # Row stays empty
        assert _read_sprint_project("sprint-99") == ""
        # Warning contains the label
        assert any("sprint-99" in r.message for r in caplog.records if r.levelno >= logging.WARNING)

    def test_no_warning_when_all_resolved(self, tmp_path, caplog):
        _insert_sprint_empty_project("sprint-80")
        _insert_agent_run("sprint-80", _PROJ_A)

        projects_file = tmp_path / "projects.json"
        projects_file.write_text("[]")

        with caplog.at_level(logging.WARNING, logger="db"):
            with db.get_conn() as conn:
                db._backfill_sprint_project(
                    conn,
                    projects_file=projects_file,
                    projects_base=tmp_path,
                )

        unresolved_warnings = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING and "sprint-80" in r.message
        ]
        assert not unresolved_warnings


# ── AC(d): second run is a no-op ─────────────────────────────────────────────

class TestIdempotent:
    """AC(d): running backfill a second time changes zero rows."""

    def test_second_run_no_op_agent_runs(self, tmp_path):
        _insert_sprint_empty_project("sprint-70")
        _insert_agent_run("sprint-70", _PROJ_A)

        projects_file = tmp_path / "projects.json"
        projects_file.write_text("[]")

        with db.get_conn() as conn:
            db._backfill_sprint_project(conn, projects_file=projects_file, projects_base=tmp_path)

        assert _read_sprint_project("sprint-70") == _PROJ_A

        with db.get_conn() as conn:
            db._backfill_sprint_project(conn, projects_file=projects_file, projects_base=tmp_path)

        assert _read_sprint_project("sprint-70") == _PROJ_A

    def test_second_run_no_op_disk(self, tmp_path):
        _insert_sprint_empty_project("sprint-71")

        slug = _PROJ_A.split("/")[-1]
        sprint_dir = tmp_path / slug / ".commander" / "sprints"
        sprint_dir.mkdir(parents=True)
        (sprint_dir / "sprint-71-plan.json").write_text("{}")

        projects_file = tmp_path / "projects.json"
        projects_file.write_text(json.dumps([{"repo": _PROJ_A}]))

        with db.get_conn() as conn:
            db._backfill_sprint_project(conn, projects_file=projects_file, projects_base=tmp_path)

        assert _read_sprint_project("sprint-71") == _PROJ_A

        # Remove the disk file to confirm the second run doesn't re-query it
        # (the row now has a non-empty project so it's skipped)
        (sprint_dir / "sprint-71-plan.json").unlink()

        with db.get_conn() as conn:
            db._backfill_sprint_project(conn, projects_file=projects_file, projects_base=tmp_path)

        # Project unchanged
        assert _read_sprint_project("sprint-71") == _PROJ_A

    def test_second_run_zero_empty_rows_change(self, tmp_path):
        """After second run on fully-resolved DB, empty count stays 0."""
        _insert_sprint_empty_project("sprint-72")
        _insert_agent_run("sprint-72", _PROJ_A)

        projects_file = tmp_path / "projects.json"
        projects_file.write_text("[]")

        with db.get_conn() as conn:
            db._backfill_sprint_project(conn, projects_file=projects_file, projects_base=tmp_path)
        with db.get_conn() as conn:
            db._backfill_sprint_project(conn, projects_file=projects_file, projects_base=tmp_path)

        assert _count_empty_sprints() == 0
