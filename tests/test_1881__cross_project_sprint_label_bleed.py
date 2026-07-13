"""Tests for issue #1881: cross-project sprint-label bleed.

Acceptance criteria covered:
- AC1: agent_runs_for_sprint with a project whose scoped read is empty falls
  back ONLY to legacy blank/NULL-project rows — never rows owned by a
  different project.
- AC2: lazy-ingest in _record_from_lifecycle skips a state file whose
  `project` field differs from the row's project.
- AC3: the post-ingest refresh is project-scoped (a same-labelled row from
  another project is never swapped in).
- AC4: legacy blank-project rows still render (fallback preserved).
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"
_ROUTERS_DIR = _DASHBOARD_DIR / "routers"

for _p in (str(_REPO_ROOT), str(_DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import db  # noqa: E402

_PROJECT_A = "owner/perf-coach"
_PROJECT_B = "owner/commander"
_LABEL = "sprint-104.1"


def _load_svc():
    """Load sprint_history_service with a stub routers package."""
    if "routers" not in sys.modules:
        stub = types.ModuleType("routers")
        stub.__path__ = [str(_ROUTERS_DIR)]
        sys.modules["routers"] = stub
    mod_name = "routers.sprint_history_service"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, _ROUTERS_DIR / "sprint_history_service.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path):
    # Re-resolve `db` at setup time: earlier tests in a full run may replace
    # sys.modules["db"], and the service under test always imports the current
    # instance — patching DB_PATH on an import-time binding would target a
    # stale copy the service never sees.
    global db
    db = importlib.import_module("db")
    db_file = tmp_path / "test_1881.db"
    original = db.DB_PATH
    db.DB_PATH = str(db_file)
    db.init_db()
    yield tmp_path
    db.DB_PATH = original


def _seed_run(project, issue=1668, outcome="success"):
    run_id = db.record_agent_start(
        issue_number=issue, sprint_label=_LABEL, agent="coder", project=project,
    )
    db.record_agent_finish(
        issue_number=issue, sprint_label=_LABEL, agent="coder",
        outcome=outcome, run_id=run_id,
    )


def _write_state_file(sprints_dir: Path, label: str, state: dict) -> None:
    sprints_dir.mkdir(parents=True, exist_ok=True)
    (sprints_dir / f"{label}-state.json").write_text(
        json.dumps(state), encoding="utf-8"
    )


# ── AC1: agent_runs fallback never crosses projects ──────────────────────────


class TestAgentRunsFallbackScoping:
    def test_other_projects_rows_not_returned(self):
        """Project B owns runs for the label; project A's read returns []."""
        _seed_run(_PROJECT_B)
        rows = db.agent_runs_for_sprint(_LABEL, project=_PROJECT_A)
        assert rows == [], (
            f"project-scoped read for {_PROJECT_A} must not fall back to "
            f"{_PROJECT_B}'s rows; got {rows}"
        )

    def test_own_rows_returned(self):
        """Scoped read still returns the project's own rows."""
        _seed_run(_PROJECT_A, issue=1355)
        _seed_run(_PROJECT_B, issue=1668)
        rows = db.agent_runs_for_sprint(_LABEL, project=_PROJECT_A)
        assert [r["issue_number"] for r in rows] == [1355]

    def test_legacy_blank_project_rows_still_render(self):
        """AC4: rows with NULL/blank project are returned by the fallback."""
        _seed_run(None, issue=999)  # legacy pre-migration row
        rows = db.agent_runs_for_sprint(_LABEL, project=_PROJECT_A)
        assert [r["issue_number"] for r in rows] == [999]

    def test_unscoped_read_unchanged(self):
        """No project argument keeps the label-only behavior."""
        _seed_run(_PROJECT_B, issue=1668)
        rows = db.agent_runs_for_sprint(_LABEL)
        assert [r["issue_number"] for r in rows] == [1668]


# ── AC2: lazy-ingest skips a foreign project's state file ─────────────────────


class TestLazyIngestForeignStateSkipped:
    def test_foreign_state_file_not_ingested(self, tmp_path):
        """State file names project B; row belongs to project A → no ingest."""
        svc = _load_svc()
        sprints_dir = tmp_path / "sprints"
        _write_state_file(sprints_dir, _LABEL, {
            "sprint_label": _LABEL,
            "project": _PROJECT_B,
            "wall_clock_secs": 907,
            "issues": [{
                "number": 1668, "title": "foreign ticket", "status": "done",
                "agent_status": "completed", "state": "merged",
            }],
        })
        db.record_sprint_start(_LABEL, project=_PROJECT_A)
        row = db.get_sprint(_LABEL, project=_PROJECT_A)
        assert not row.get("run_ingested_at")

        ingest_calls = []
        with patch.object(
            db, "ingest_sprint_run_artifact",
            side_effect=lambda *a, **k: ingest_calls.append((a, k)),
        ):
            svc._record_from_lifecycle(row, sprints_dir)

        assert ingest_calls == [], (
            "a state file whose project differs from the row's project must "
            "never be ingested"
        )
        refreshed = db.get_sprint(_LABEL, project=_PROJECT_A)
        assert not refreshed.get("run_ingested_at")

    def test_own_state_file_still_ingested(self, tmp_path):
        """Matching project (or legacy no-project state) keeps lazy-ingest working."""
        svc = _load_svc()
        sprints_dir = tmp_path / "sprints"
        _write_state_file(sprints_dir, _LABEL, {
            "sprint_label": _LABEL,
            "project": _PROJECT_A,
            "wall_clock_secs": 300,
            "issues": [{
                "number": 1355, "title": "own ticket", "status": "done",
                "agent_status": "completed", "state": "merged",
            }],
        })
        db.record_sprint_start(_LABEL, project=_PROJECT_A)
        row = db.get_sprint(_LABEL, project=_PROJECT_A)

        svc._record_from_lifecycle(row, sprints_dir)

        refreshed = db.get_sprint(_LABEL, project=_PROJECT_A)
        assert refreshed.get("run_ingested_at"), "own-project state must still ingest"
        issues = json.loads(refreshed.get("issues_json") or "[]")
        assert [i.get("number") or i.get("ticket_id") for i in issues] == [1355]

    def test_legacy_state_without_project_still_ingested(self, tmp_path):
        """A pre-migration state file with no project field keeps ingesting."""
        svc = _load_svc()
        sprints_dir = tmp_path / "sprints"
        _write_state_file(sprints_dir, _LABEL, {
            "sprint_label": _LABEL,
            "wall_clock_secs": 120,
            "issues": [{
                "number": 42, "title": "legacy", "status": "done",
                "agent_status": "completed", "state": "merged",
            }],
        })
        db.record_sprint_start(_LABEL, project=_PROJECT_A)
        row = db.get_sprint(_LABEL, project=_PROJECT_A)

        svc._record_from_lifecycle(row, sprints_dir)

        refreshed = db.get_sprint(_LABEL, project=_PROJECT_A)
        assert refreshed.get("run_ingested_at")


# ── AC3: post-ingest refresh is project-scoped ────────────────────────────────


class TestPostIngestRefreshScoped:
    def test_refresh_reads_own_projects_row(self, tmp_path):
        """Both projects have a row for the label; the refreshed row after
        ingest must be project A's, not whichever get_sprint(label) finds."""
        svc = _load_svc()
        sprints_dir = tmp_path / "sprints"
        _write_state_file(sprints_dir, _LABEL, {
            "sprint_label": _LABEL,
            "project": _PROJECT_A,
            "wall_clock_secs": 300,
            "issues": [{
                "number": 1355, "title": "own ticket", "status": "done",
                "agent_status": "completed", "state": "merged",
            }],
        })
        # Project B's row first so an unscoped label read would return it.
        db.record_sprint_start(_LABEL, project=_PROJECT_B)
        db.record_sprint_start(_LABEL, project=_PROJECT_A)
        row = db.get_sprint(_LABEL, project=_PROJECT_A)

        rec = svc._record_from_lifecycle(row, sprints_dir)

        assert rec.get("project") == _PROJECT_A, (
            f"record built from wrong project's row: {rec.get('project')}"
        )
