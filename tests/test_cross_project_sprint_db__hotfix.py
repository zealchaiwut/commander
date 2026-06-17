"""Hotfix: sprint lifecycle rows must not leak across projects sharing a label."""
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
import server as srv  # noqa: E402

_LABEL = "sprint-64"
_COMMANDER = "zealchaiwut/commander"
_PERF_COACH = "zealchaiwut/perf-coach"


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path):
    db_file = tmp_path / "cross_project_sprint.db"
    original = db.DB_PATH
    db.DB_PATH = str(db_file)
    db.init_db()
    yield
    db.DB_PATH = original


def _minimal_state() -> dict:
    return {
        "issues": [
            {
                "ticket_id": 839,
                "title": "Commander-only ticket",
                "agent_status": "completed",
                "state": "merged",
            }
        ]
    }


def test_get_sprint_hides_other_project_row():
    """perf-coach must not read commander's ingested sprint-64 row."""
    db.record_sprint_start(_LABEL, project=_COMMANDER)
    db.ingest_sprint_run_artifact(_LABEL, _minimal_state(), project=_COMMANDER)

    commander_row = db.get_sprint(_LABEL, project=_COMMANDER)
    perf_row = db.get_sprint(_LABEL, project=_PERF_COACH)

    assert commander_row is not None
    assert commander_row["project"] == _COMMANDER
    assert perf_row is None


def test_finish_card_no_data_for_other_project(tmp_path, monkeypatch):
    """Finish card on perf-coach must not surface commander's ingested outcome."""
    db.record_sprint_start(_LABEL, project=_COMMANDER)
    db.ingest_sprint_run_artifact(_LABEL, _minimal_state(), project=_COMMANDER)

    project_root = tmp_path / "perf-coach"
    (project_root / ".commander" / "sprints").mkdir(parents=True)

    monkeypatch.setattr(srv, "_project_root_path", lambda _repo: project_root)
    monkeypatch.setattr(srv, "_is_sprint_running", lambda *_a, **_k: False)

    card = srv.get_sprint_finish_card(_LABEL, _PERF_COACH)
    assert card["state"] == "no_data"


def test_has_own_run_outcome_is_project_scoped(tmp_path):
    db.record_sprint_start(_LABEL, project=_COMMANDER)
    db.ingest_sprint_run_artifact(_LABEL, _minimal_state(), project=_COMMANDER)
    project_root = tmp_path / "proj"

    assert srv._sprint_has_own_run_outcome(project_root, _LABEL, _COMMANDER) is True
    assert srv._sprint_has_own_run_outcome(project_root, _LABEL, _PERF_COACH) is False
