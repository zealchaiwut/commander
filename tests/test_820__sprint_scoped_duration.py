"""Tests for issue #820 — scope agent_runs duration enrichment by sprint_label.

Each test is anchored to a specific acceptance criterion (AC1–AC5).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
ROUTERS_DIR = DASHBOARD_DIR / "routers"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))
sys.path.insert(0, str(ROUTERS_DIR))

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")

import db as _db_module  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path):
    """Isolated SQLite DB; patches db module in place."""
    db_file = tmp_path / "test_820.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


# ── AC1: agent_runs_for_issue filters correctly by sprint_label ──────────────

def test_ac1_sprint_label_filter_returns_only_matching_runs(fresh_db):
    """AC1: agent_runs_for_issue(num, sprint_label) returns only runs for that sprint."""
    fresh_db.record_agent_start(820, "sprint-55", "coder")
    fresh_db.record_agent_finish(820, "sprint-55", "coder", duration_seconds=100)
    fresh_db.record_agent_start(820, "sprint-97", "coder")
    fresh_db.record_agent_finish(820, "sprint-97", "coder", duration_seconds=200)

    runs_55 = fresh_db.agent_runs_for_issue(820, "sprint-55")
    runs_97 = fresh_db.agent_runs_for_issue(820, "sprint-97")

    assert len(runs_55) == 1
    assert runs_55[0]["duration_seconds"] == 100
    assert len(runs_97) == 1
    assert runs_97[0]["duration_seconds"] == 200


def test_ac1_wrong_sprint_label_returns_empty(fresh_db):
    """AC1: agent_runs_for_issue with a non-matching sprint_label returns []."""
    fresh_db.record_agent_start(820, "sprint-97", "coder")
    fresh_db.record_agent_finish(820, "sprint-97", "coder", duration_seconds=150)

    runs = fresh_db.agent_runs_for_issue(820, "sprint-55")
    assert runs == []


# ── AC2: events API passes sprint_label from event detail ────────────────────

def test_ac2_activity_service_uses_sprint_label_in_enrichment():
    """AC2: activity_service.py passes sprint_label from event detail to agent_runs_for_issue."""
    src = (ROUTERS_DIR / "activity_service.py").read_text()
    assert "sprint_label" in src, (
        "activity_service.py must use sprint_label in the enrichment block"
    )
    assert "agent_runs_for_issue" in src, (
        "activity_service.py must still call agent_runs_for_issue"
    )


def test_ac2_logs_service_includes_sprint_label_in_agent_finished_detail():
    """AC2: logs_service.py includes sprint_label in agent_finished event detail for sprint branches."""
    src = (ROUTERS_DIR / "logs_service.py").read_text()
    assert "sprint_label" in src, (
        "logs_service.py must include sprint_label when creating agent_finished events"
    )


def test_ac2_sprint_branch_yields_sprint_label_in_event(fresh_db):
    """AC2: receive_agent_event for a sprint/* branch records sprint_label in agent_finished detail."""
    for mod in list(sys.modules.keys()):
        if "logs_service" in mod:
            del sys.modules[mod]

    import db as db_mod
    db_mod.DB_PATH = fresh_db.DB_PATH

    import logs_service as logs_svc

    # Patch _agent_project_from_name to return a project so the event is recorded.
    with patch.object(logs_svc, "_agent_project_from_name", return_value="owner/myproj"):
        event = logs_svc.AgentEvent(
            session_id="sess-sprint-820",
            event_type="agent_stop",
            working_dir="/home/user/dev/myproj",
            status="done",
            name="coder·myproj·sprint/sprint-97·#sess82",
        )
        logs_svc.receive_agent_event(event)

    with fresh_db.get_conn() as conn:
        rows = conn.execute(
            "SELECT detail FROM events WHERE type='agent_finished'"
        ).fetchall()
    assert rows, "agent_finished event must be written"
    detail = json.loads(rows[0]["detail"])
    assert "sprint_label" in detail, (
        "agent_finished detail must include sprint_label for sprint/* branches; "
        f"got detail: {detail}"
    )
    assert detail["sprint_label"] == "sprint-97", (
        f"Expected sprint_label='sprint-97', got {detail.get('sprint_label')!r}"
    )


def test_ac2_feature_branch_omits_sprint_label_in_event(fresh_db):
    """AC2: receive_agent_event for a feature/* branch omits sprint_label from agent_finished detail."""
    for mod in list(sys.modules.keys()):
        if "logs_service" in mod:
            del sys.modules[mod]

    import db as db_mod
    db_mod.DB_PATH = fresh_db.DB_PATH

    import logs_service as logs_svc

    with patch.object(logs_svc, "_agent_project_from_name", return_value="owner/myproj"):
        event = logs_svc.AgentEvent(
            session_id="sess-feat-820",
            event_type="agent_stop",
            working_dir="/home/user/dev/myproj",
            status="done",
            name="coder·myproj·feature/820-slug·#sessfeat",
        )
        logs_svc.receive_agent_event(event)

    with fresh_db.get_conn() as conn:
        rows = conn.execute(
            "SELECT detail FROM events WHERE type='agent_finished'"
        ).fetchall()
    assert rows
    detail = json.loads(rows[0]["detail"])
    assert detail.get("sprint_label") is None, (
        "feature/* branch must not set sprint_label in agent_finished detail; "
        f"got: {detail}"
    )


# ── AC3: agent_finished event shows duration from its own sprint ─────────────

def test_ac3_older_sprint_event_shows_older_sprint_duration(fresh_db):
    """AC3: agent_finished event from sprint S-N shows S-N's duration, not S-N+1's.

    Setup: issue 820 coder ran in sprint-55 (100s) and sprint-97 (200s).
    Two agent_finished events are inserted with sprint_label in their detail.
    The sprint-55 event must show 100s, the sprint-97 event must show 200s.
    """
    fresh_db.record_agent_start(820, "sprint-55", "coder")
    fresh_db.record_agent_finish(820, "sprint-55", "coder", duration_seconds=100)
    fresh_db.record_agent_start(820, "sprint-97", "coder")
    fresh_db.record_agent_finish(820, "sprint-97", "coder", duration_seconds=200)

    proj = "owner/myproj"
    with fresh_db.get_conn() as conn:
        conn.execute(
            "INSERT INTO events (project, timestamp, source, actor, type, target, detail)"
            " VALUES (?, '2026-06-01T10:00:00', 'agent', 'coder', 'agent_finished',"
            " 'sess-55', ?)",
            (proj, json.dumps({"status": "done", "role": "coder", "issue_num": 820,
                               "sprint_label": "sprint-55"})),
        )
        conn.execute(
            "INSERT INTO events (project, timestamp, source, actor, type, target, detail)"
            " VALUES (?, '2026-06-10T10:00:00', 'agent', 'coder', 'agent_finished',"
            " 'sess-97', ?)",
            (proj, json.dumps({"status": "done", "role": "coder", "issue_num": 820,
                               "sprint_label": "sprint-97"})),
        )
        conn.commit()

    for mod in list(sys.modules.keys()):
        if "activity_service" in mod:
            del sys.modules[mod]

    # Sync the db module that activity_service will import, in case a prior
    # test deleted and re-imported "db" (creating a new module object).
    import db as _sync_db
    _sync_db.DB_PATH = fresh_db.DB_PATH

    import activity_service as act_svc

    FAKE_PROJECT = [{"repo": proj, "slug": "myproj"}]
    with patch.object(act_svc, "projects_module") as mock_pm, \
         patch.object(act_svc, "_read_jsonl_lifecycle_events", return_value=[]):
        mock_pm.load_projects.return_value = FAKE_PROJECT
        events = act_svc.get_project_events("myproj", limit=50)

    ev_55 = next((e for e in events if e.get("target") == "sess-55"), None)
    ev_97 = next((e for e in events if e.get("target") == "sess-97"), None)

    assert ev_55 is not None, "sprint-55 event not found"
    assert ev_97 is not None, "sprint-97 event not found"

    assert ev_55.get("duration_seconds") == 100, (
        f"Sprint-55 event should show 100s (its own run), got {ev_55.get('duration_seconds')}"
    )
    assert ev_97.get("duration_seconds") == 200, (
        f"Sprint-97 event should show 200s (its own run), got {ev_97.get('duration_seconds')}"
    )


# ── AC4: No run for (issue, role, sprint_label) → duration absent ────────────

def test_ac4_missing_run_yields_no_duration(fresh_db):
    """AC4: When no agent_run exists for the event's (issue, role, sprint_label),
    duration is absent (not pulled from a different sprint's run).
    """
    # Only a sprint-97 run exists; the sprint-55 event should not get sprint-97's duration.
    fresh_db.record_agent_start(820, "sprint-97", "coder")
    fresh_db.record_agent_finish(820, "sprint-97", "coder", duration_seconds=200)

    proj = "owner/myproj"
    with fresh_db.get_conn() as conn:
        conn.execute(
            "INSERT INTO events (project, timestamp, source, actor, type, target, detail)"
            " VALUES (?, '2026-06-01T10:00:00', 'agent', 'coder', 'agent_finished',"
            " 'sess-missing', ?)",
            (proj, json.dumps({"status": "done", "role": "coder", "issue_num": 820,
                               "sprint_label": "sprint-55"})),
        )
        conn.commit()

    for mod in list(sys.modules.keys()):
        if "activity_service" in mod:
            del sys.modules[mod]

    import db as _sync_db
    _sync_db.DB_PATH = fresh_db.DB_PATH

    import activity_service as act_svc

    FAKE_PROJECT = [{"repo": proj, "slug": "myproj"}]
    with patch.object(act_svc, "projects_module") as mock_pm, \
         patch.object(act_svc, "_read_jsonl_lifecycle_events", return_value=[]):
        mock_pm.load_projects.return_value = FAKE_PROJECT
        events = act_svc.get_project_events("myproj", limit=50)

    ev = next((e for e in events if e.get("target") == "sess-missing"), None)
    assert ev is not None, "event not found"
    assert ev.get("duration_seconds") is None, (
        f"Duration must be absent when no run exists for (820, coder, sprint-55); "
        f"got {ev.get('duration_seconds')} — it must not fall back to sprint-97's run"
    )


# ── AC5: Existing behavior unchanged when sprint_label is absent ─────────────

def test_ac5_no_sprint_label_still_enriches_duration(fresh_db):
    """AC5: Events without sprint_label in detail still receive duration enrichment."""
    fresh_db.record_agent_start(820, "sprint-97", "coder")
    fresh_db.record_agent_finish(820, "sprint-97", "coder", duration_seconds=150)

    proj = "owner/myproj"
    with fresh_db.get_conn() as conn:
        conn.execute(
            "INSERT INTO events (project, timestamp, source, actor, type, target, detail)"
            " VALUES (?, '2026-06-10T10:00:00', 'agent', 'coder', 'agent_finished',"
            " 'sess-no-sl', ?)",
            (proj, json.dumps({"status": "done", "role": "coder", "issue_num": 820})),
        )
        conn.commit()

    for mod in list(sys.modules.keys()):
        if "activity_service" in mod:
            del sys.modules[mod]

    import db as _sync_db
    _sync_db.DB_PATH = fresh_db.DB_PATH

    import activity_service as act_svc

    FAKE_PROJECT = [{"repo": proj, "slug": "myproj"}]
    with patch.object(act_svc, "projects_module") as mock_pm, \
         patch.object(act_svc, "_read_jsonl_lifecycle_events", return_value=[]):
        mock_pm.load_projects.return_value = FAKE_PROJECT
        events = act_svc.get_project_events("myproj", limit=50)

    ev = next((e for e in events if e.get("target") == "sess-no-sl"), None)
    assert ev is not None, "event not found"
    assert ev.get("duration_seconds") == 150, (
        f"Events without sprint_label should still be enriched with duration; "
        f"got {ev.get('duration_seconds')}"
    )


def test_ac5_single_run_issue_still_shows_duration(fresh_db):
    """AC5: An issue with only one historical run still shows duration correctly."""
    fresh_db.record_agent_start(821, "sprint-97", "tester")
    fresh_db.record_agent_finish(821, "sprint-97", "tester", duration_seconds=75)

    proj = "owner/myproj"
    with fresh_db.get_conn() as conn:
        conn.execute(
            "INSERT INTO events (project, timestamp, source, actor, type, target, detail)"
            " VALUES (?, '2026-06-10T11:00:00', 'agent', 'tester', 'agent_finished',"
            " 'sess-single', ?)",
            (proj, json.dumps({"status": "done", "role": "tester", "issue_num": 821,
                               "sprint_label": "sprint-97"})),
        )
        conn.commit()

    for mod in list(sys.modules.keys()):
        if "activity_service" in mod:
            del sys.modules[mod]

    import db as _sync_db
    _sync_db.DB_PATH = fresh_db.DB_PATH

    import activity_service as act_svc

    FAKE_PROJECT = [{"repo": proj, "slug": "myproj"}]
    with patch.object(act_svc, "projects_module") as mock_pm, \
         patch.object(act_svc, "_read_jsonl_lifecycle_events", return_value=[]):
        mock_pm.load_projects.return_value = FAKE_PROJECT
        events = act_svc.get_project_events("myproj", limit=50)

    ev = next((e for e in events if e.get("target") == "sess-single"), None)
    assert ev is not None, "event not found"
    assert ev.get("duration_seconds") == 75, (
        f"Issue with single historical run should still show duration; "
        f"got {ev.get('duration_seconds')}"
    )
