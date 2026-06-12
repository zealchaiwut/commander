"""Sprint-lifecycle redesign P3: reconciliation (GitHub → API → disk-as-cache).

Design contract: docs/architecture/sprint-lifecycle.md
Tracking:        docs/milestones/sprint-lifecycle-redesign.md (P3)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "services" / "sprint_manager"))
sys.path.insert(0, str(_DASHBOARD_ROOT))

os.environ.setdefault("DB_PATH", str(_REPO_ROOT / "commander.db"))

import db as _db_module  # noqa: E402

_SM_SRC = (
    _REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"
).read_text(encoding="utf-8")
_SERVER_SRC = (_DASHBOARD_ROOT / "server.py").read_text(encoding="utf-8")
_HISTORY_SRC = (
    _DASHBOARD_ROOT / "routers" / "sprint_history_service.py"
).read_text(encoding="utf-8")

import sprint_manager as sm  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path):
    db_file = tmp_path / "test_p3.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


class TestPerLabelStateFiles:
    def test_state_path_uses_full_label(self):
        p = sm._state_path(68, "sprint-68.6")
        assert p.name == "sprint-68.6-state.json"

    def test_summary_path_uses_full_label(self):
        p = sm._summary_path(68, "sprint-68.6")
        assert p.name.startswith("sprint-68.6-summary-")


class TestArtifactService:
    def test_resolve_prefers_label_file(self, tmp_path):
        from routers import sprint_artifact_service as sas

        sprints = tmp_path / "sprints"
        sprints.mkdir()
        (sprints / "sprint-68-state.json").write_text('{"sprint_label":"sprint-68.2"}')
        child = sprints / "sprint-68.6-state.json"
        child.write_text('{"sprint_label":"sprint-68.6"}')
        assert sas.resolve_state_path(sprints, "sprint-68.6") == child

    def test_legacy_base_file_when_label_matches(self, tmp_path):
        from routers import sprint_artifact_service as sas

        sprints = tmp_path / "sprints"
        sprints.mkdir()
        base = sprints / "sprint-68-state.json"
        base.write_text('{"sprint_label":"sprint-68.6"}')
        assert sas.resolve_state_path(sprints, "sprint-68.6") == base

    def test_legacy_base_file_rejected_for_sibling(self, tmp_path):
        from routers import sprint_artifact_service as sas

        sprints = tmp_path / "sprints"
        sprints.mkdir()
        (sprints / "sprint-68-state.json").write_text('{"sprint_label":"sprint-68.2"}')
        assert sas.resolve_state_path(sprints, "sprint-68.6") is None


class TestDbIngest:
    def test_ingest_stores_artifacts(self, fresh_db):
        state = {
            "sprint_label": "sprint-68.6",
            "issues": [
                {"number": 894, "title": "fix", "status": "done", "agent_status": "completed"},
            ],
            "total_tokens_in": 100,
            "total_tokens_out": 50,
            "wall_clock_secs": 120.5,
            "summary_issue_url": "https://github.com/o/r/issues/1",
        }
        fresh_db.record_sprint_ready_to_merge("sprint-68.6", end_reason="natural")
        fresh_db.ingest_sprint_run_artifact("sprint-68.6", state, project="o/r")
        row = fresh_db.get_sprint("sprint-68.6")
        assert row["run_ingested_at"]
        assert row["state"] == "ready_to_merge"
        issues = json.loads(row["issues_json"])
        assert issues[0]["ticket_id"] == 894
        assert row["tokens"] == 150
        assert row["wall_clock_secs"] == 120

    def test_ingest_does_not_clobber_lifecycle_state(self, fresh_db):
        fresh_db.record_sprint_needs_rework("sprint-68.3", end_reason="ticket-failures")
        fresh_db.ingest_sprint_run_artifact(
            "sprint-68.3",
            {"sprint_label": "sprint-68.3", "issues": [], "wall_clock_secs": 1},
        )
        row = fresh_db.get_sprint("sprint-68.3")
        assert row["state"] == "needs_rework"
        assert row["run_ingested_at"]


class TestOutcomeDbFirst:
    def test_outcome_reads_ingested_row(self, fresh_db, tmp_path):
        if "server" in sys.modules:
            del sys.modules["server"]
        import server as srv

        state = {
            "sprint_label": "sprint-68.6",
            "issues": [{"number": 1, "title": "t", "status": "done"}],
            "wall_clock_secs": 10,
        }
        fresh_db.record_sprint_ready_to_merge("sprint-68.6")
        fresh_db.ingest_sprint_run_artifact("sprint-68.6", state, project="o/r")

        project_root = tmp_path / "proot"
        (project_root / ".commander" / "sprints").mkdir(parents=True)

        with (
            patch.object(srv, "_project_root_path", return_value=project_root),
            patch.object(srv, "_is_sprint_running", return_value=False),
            patch.object(srv, "_has_rework_tickets", return_value=False),
            patch.object(srv.db, "get_sprint", fresh_db.get_sprint),
        ):
            out = srv._outcome_from_ingested_row(
                fresh_db.get_sprint("sprint-68.6"), "sprint-68.6", "o/r",
            )
        assert out["lifecycle"] == "ready_to_merge"
        assert out["counts"]["done"] == 1
        assert out["wall_clock_secs"] == 10


class TestHistoryDbEnrichment:
    def test_record_from_lifecycle_uses_db_when_ingested(self, fresh_db, tmp_path):
        from routers import sprint_history_service as shs

        fresh_db.record_sprint_start("sprint-68.6", project="o/r")
        fresh_db.ingest_sprint_run_artifact(
            "sprint-68.6",
            {
                "sprint_label": "sprint-68.6",
                "issues": [{"number": 2, "title": "x", "status": "done"}],
                "wall_clock_secs": 99,
                "total_tokens_in": 1,
                "total_tokens_out": 2,
            },
            project="o/r",
        )
        row = fresh_db.get_sprint("sprint-68.6")
        rec = shs._record_from_lifecycle(row, tmp_path / "sprints")
        assert rec["duration"] == 99
        assert rec["tokens"] == 3
        assert len(rec["issues"]) == 1


class TestSourceContracts:
    def test_sprint_manager_ingest_hook(self):
        assert "_sprint_db_ingest_run_sm" in _SM_SRC
        assert 'f"{sprint_label}-state.json"' in _SM_SRC

    def test_outcome_db_first(self):
        assert "_outcome_from_ingested_row" in _SERVER_SRC
        assert "run_ingested_at" in _SERVER_SRC.split("def get_sprint_outcome")[1].split("@app.")[0]

    def test_history_prefers_db_artifacts(self):
        assert "run_ingested_at" in _HISTORY_SRC.split("def _record_from_lifecycle")[1].split("def _record_from_files")[0]

    def test_background_reconcile_on_history(self):
        hist_router = (_DASHBOARD_ROOT / "routers" / "sprint_history.py").read_text()
        assert "BackgroundTasks" in hist_router
        assert "reconcile_project_background" in hist_router
