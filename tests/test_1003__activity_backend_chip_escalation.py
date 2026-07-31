"""Tests for issue #1003 — Activity backend chip mislabels escalated coder runs.

Each test is anchored to a specific acceptance criterion.

AC1: Each agent_finished event is matched to its specific agent_runs row (by
     started_at proximity) rather than using last-write-wins (issue_num, role) key.
AC2: Escalated cline agent_finished shows 'cline' backend chip.
AC3: Escalated claude-code agent_finished shows 'claude-code' backend chip.
AC4: Non-escalated runs display the correct backend chip unchanged.
AC5: _backend_by_key no longer overwrites a previous entry for the same
     (issue_num, role) pair.
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
    """Isolated SQLite DB; yields the patched db module."""
    db_file = tmp_path / "test_1003.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


def _reload_activity_service(fresh_db):
    """Reload activity_service with the current fresh_db path."""
    for mod in list(sys.modules.keys()):
        if "activity_service" in mod:
            del sys.modules[mod]
    import db as _sync_db
    _sync_db.DB_PATH = fresh_db.DB_PATH
    import activity_service as act_svc
    return act_svc


# ── AC1 + AC2 + AC3: escalated run — each event gets its own backend ──────────

def test_escalated_cline_event_shows_cline_backend(fresh_db):
    """AC2: After escalation, the earlier cline agent_finished event shows 'cline'."""
    # Run 1: cline coder finishes at T1
    fresh_db.record_agent_start(
        1003, "sprint-71", "coder",
        backend="cline",
        started_at="2026-06-01T10:00:00",
    )
    fresh_db.record_agent_finish(
        1003, "sprint-71", "coder",
        finished_at="2026-06-01T10:30:00",
        outcome="escalated",
    )
    # Run 2: claude-code coder finishes at T2
    fresh_db.record_agent_start(
        1003, "sprint-71", "coder",
        backend="claude-code",
        started_at="2026-06-01T11:00:00",
    )
    fresh_db.record_agent_finish(
        1003, "sprint-71", "coder",
        finished_at="2026-06-01T11:30:00",
        outcome="success",
    )

    proj = "owner/testproj"
    with fresh_db.get_conn() as conn:
        # cline event — timestamp matches run 1's finished_at
        conn.execute(
            "INSERT INTO events (project, timestamp, source, actor, type, target, detail)"
            " VALUES (?, '2026-06-01T10:30:00', 'agent', 'coder', 'agent_finished',"
            " 'sess-cline', ?)",
            (proj, json.dumps({
                "status": "done", "role": "coder",
                "issue_num": 1003, "sprint_label": "sprint-71",
            })),
        )
        # claude-code event — timestamp matches run 2's finished_at
        conn.execute(
            "INSERT INTO events (project, timestamp, source, actor, type, target, detail)"
            " VALUES (?, '2026-06-01T11:30:00', 'agent', 'coder', 'agent_finished',"
            " 'sess-cc', ?)",
            (proj, json.dumps({
                "status": "done", "role": "coder",
                "issue_num": 1003, "sprint_label": "sprint-71",
            })),
        )
        conn.commit()

    act_svc = _reload_activity_service(fresh_db)
    FAKE_PROJECTS = [{"repo": proj, "slug": "testproj"}]
    with patch.object(act_svc, "projects_module") as mock_pm, \
         patch.object(act_svc, "_read_jsonl_lifecycle_events", return_value=[]):
        mock_pm.load_projects.return_value = FAKE_PROJECTS
        events = act_svc.get_project_events("testproj", limit=50)

    cline_ev = next((e for e in events if e.get("target") == "sess-cline"), None)
    assert cline_ev is not None, "cline event not found"
    assert cline_ev.get("backend") == "cline", (
        f"Escalated cline event must show 'cline' backend; got {cline_ev.get('backend')!r}"
    )


def test_escalated_claude_code_event_shows_claude_code_backend(fresh_db):
    """AC3: After escalation, the later claude-code agent_finished event shows 'claude-code'."""
    fresh_db.record_agent_start(
        1003, "sprint-71", "coder",
        backend="cline",
        started_at="2026-06-01T10:00:00",
    )
    fresh_db.record_agent_finish(
        1003, "sprint-71", "coder",
        finished_at="2026-06-01T10:30:00",
        outcome="escalated",
    )
    fresh_db.record_agent_start(
        1003, "sprint-71", "coder",
        backend="claude-code",
        started_at="2026-06-01T11:00:00",
    )
    fresh_db.record_agent_finish(
        1003, "sprint-71", "coder",
        finished_at="2026-06-01T11:30:00",
        outcome="success",
    )

    proj = "owner/testproj"
    with fresh_db.get_conn() as conn:
        conn.execute(
            "INSERT INTO events (project, timestamp, source, actor, type, target, detail)"
            " VALUES (?, '2026-06-01T10:30:00', 'agent', 'coder', 'agent_finished',"
            " 'sess-cline', ?)",
            (proj, json.dumps({
                "status": "done", "role": "coder",
                "issue_num": 1003, "sprint_label": "sprint-71",
            })),
        )
        conn.execute(
            "INSERT INTO events (project, timestamp, source, actor, type, target, detail)"
            " VALUES (?, '2026-06-01T11:30:00', 'agent', 'coder', 'agent_finished',"
            " 'sess-cc', ?)",
            (proj, json.dumps({
                "status": "done", "role": "coder",
                "issue_num": 1003, "sprint_label": "sprint-71",
            })),
        )
        conn.commit()

    act_svc = _reload_activity_service(fresh_db)
    FAKE_PROJECTS = [{"repo": proj, "slug": "testproj"}]
    with patch.object(act_svc, "projects_module") as mock_pm, \
         patch.object(act_svc, "_read_jsonl_lifecycle_events", return_value=[]):
        mock_pm.load_projects.return_value = FAKE_PROJECTS
        events = act_svc.get_project_events("testproj", limit=50)

    cc_ev = next((e for e in events if e.get("target") == "sess-cc"), None)
    assert cc_ev is not None, "claude-code event not found"
    assert cc_ev.get("backend") == "claude-code", (
        f"Escalated claude-code event must show 'claude-code'; got {cc_ev.get('backend')!r}"
    )


def test_escalated_both_events_show_correct_backends(fresh_db):
    """AC1+AC2+AC3: Both escalation events are independently matched to their runs."""
    fresh_db.record_agent_start(
        1003, "sprint-71", "coder",
        backend="cline",
        started_at="2026-06-01T10:00:00",
    )
    fresh_db.record_agent_finish(
        1003, "sprint-71", "coder",
        finished_at="2026-06-01T10:30:00",
        outcome="escalated",
    )
    fresh_db.record_agent_start(
        1003, "sprint-71", "coder",
        backend="claude-code",
        started_at="2026-06-01T11:00:00",
    )
    fresh_db.record_agent_finish(
        1003, "sprint-71", "coder",
        finished_at="2026-06-01T11:30:00",
        outcome="success",
    )

    proj = "owner/testproj"
    with fresh_db.get_conn() as conn:
        conn.execute(
            "INSERT INTO events (project, timestamp, source, actor, type, target, detail)"
            " VALUES (?, '2026-06-01T10:30:00', 'agent', 'coder', 'agent_finished',"
            " 'sess-cline', ?)",
            (proj, json.dumps({
                "status": "done", "role": "coder",
                "issue_num": 1003, "sprint_label": "sprint-71",
            })),
        )
        conn.execute(
            "INSERT INTO events (project, timestamp, source, actor, type, target, detail)"
            " VALUES (?, '2026-06-01T11:30:00', 'agent', 'coder', 'agent_finished',"
            " 'sess-cc', ?)",
            (proj, json.dumps({
                "status": "done", "role": "coder",
                "issue_num": 1003, "sprint_label": "sprint-71",
            })),
        )
        conn.commit()

    act_svc = _reload_activity_service(fresh_db)
    FAKE_PROJECTS = [{"repo": proj, "slug": "testproj"}]
    with patch.object(act_svc, "projects_module") as mock_pm, \
         patch.object(act_svc, "_read_jsonl_lifecycle_events", return_value=[]):
        mock_pm.load_projects.return_value = FAKE_PROJECTS
        events = act_svc.get_project_events("testproj", limit=50)

    cline_ev = next((e for e in events if e.get("target") == "sess-cline"), None)
    cc_ev = next((e for e in events if e.get("target") == "sess-cc"), None)
    assert cline_ev is not None
    assert cc_ev is not None
    assert cline_ev.get("backend") == "cline", (
        f"cline event got backend={cline_ev.get('backend')!r}, expected 'cline'"
    )
    assert cc_ev.get("backend") == "claude-code", (
        f"claude-code event got backend={cc_ev.get('backend')!r}, expected 'claude-code'"
    )


# ── AC4: non-escalated run — unchanged behavior ───────────────────────────────

def test_non_escalated_run_shows_correct_backend(fresh_db):
    """AC4: A single coder run per ticket still displays the correct backend chip."""
    fresh_db.record_agent_start(
        1003, "sprint-71", "coder",
        backend="claude-code",
        started_at="2026-06-01T12:00:00",
    )
    fresh_db.record_agent_finish(
        1003, "sprint-71", "coder",
        finished_at="2026-06-01T12:45:00",
        outcome="success",
    )

    proj = "owner/testproj"
    with fresh_db.get_conn() as conn:
        conn.execute(
            "INSERT INTO events (project, timestamp, source, actor, type, target, detail)"
            " VALUES (?, '2026-06-01T12:45:00', 'agent', 'coder', 'agent_finished',"
            " 'sess-single', ?)",
            (proj, json.dumps({
                "status": "done", "role": "coder",
                "issue_num": 1003, "sprint_label": "sprint-71",
            })),
        )
        conn.commit()

    act_svc = _reload_activity_service(fresh_db)
    FAKE_PROJECTS = [{"repo": proj, "slug": "testproj"}]
    with patch.object(act_svc, "projects_module") as mock_pm, \
         patch.object(act_svc, "_read_jsonl_lifecycle_events", return_value=[]):
        mock_pm.load_projects.return_value = FAKE_PROJECTS
        events = act_svc.get_project_events("testproj", limit=50)

    ev = next((e for e in events if e.get("target") == "sess-single"), None)
    assert ev is not None, "single-run event not found"
    assert ev.get("backend") == "claude-code", (
        f"Non-escalated run must show correct backend; got {ev.get('backend')!r}"
    )


# ── AC5: structural — no last-write-wins _backend_by_key ─────────────────────

def test_activity_service_no_last_write_wins_backend_by_key():
    """AC5: _backend_by_key must not overwrite a previous entry for the same key.

    The fix must collect all runs per key (list/dict of lists) so both cline
    and claude-code entries survive for proximity matching. A single-value dict
    that does _backend_by_key[_key] = _r["backend"] would be the old bug.
    """
    src = (ROUTERS_DIR / "activity_service.py").read_text()
    # Must NOT contain the old one-value overwrite pattern
    assert "_backend_by_key[_key] = _r" not in src, (
        "activity_service.py still uses last-write-wins _backend_by_key[_key] = _r[...]. "
        "Replace with a list-based approach."
    )
    # Must use a proximity-matching helper or list-based collection
    assert (
        "_runs_list_by_key" in src
        or "_closest_run" in src
        or ".append(" in src
    ), (
        "activity_service.py must collect all agent_runs per key (list) for proximity matching."
    )
