"""Tests for issue #864 — surface planner state in daily brief and nav.

Each test is anchored to a specific acceptance criterion. The DB is a real
(temp) SQLite file seeded with real rows; the project list and on-disk sprint
goal are the only seams, patched per-test (mirroring the #839 suite).

Grounded interpretation (no scheduler/evening-flow infra exists in the repo):

* "Ran overnight" = sprints whose ``ended_at`` falls inside the brief window.
  The daily-artifact window is the 6 AM-anchored 24h span — i.e. literally what
  ran overnight. Outcome is derived from the sprint's lifecycle state
  (completed/ready_to_merge → success, needs_rework/failed/cancelled → failure,
  partial_finished → partial). Each entry links to its sprint brief.
* "Waiting on you" / pending sign-off = sprints in the ``ready_to_merge``
  lifecycle state — finished and awaiting the human merge/sign-off action.
* The nav-pill sign-off indicator is driven by the same canonical signal the
  brief exposes: ``global_kpis.awaiting_signoff`` (count of sprints awaiting
  sign-off). >0 → indicator shown; 0 → indicator hidden.

AC1  Brief "Ran overnight" lists each sprint that ran in the window with an
     outcome and a link to its sprint brief.
AC2  Brief "Waiting on you" lists every pending-signoff sprint with ticket
     count and estimated hours.
AC3  "Waiting on you" is empty when no sprint is pending sign-off.
AC4  "Ran overnight" is empty when no sprint ran in the window.
AC5  Nav-pill sign-off signal (awaiting_signoff) is positive when ≥1 sprint is
     awaiting sign-off.
AC6  The sign-off signal drops to 0 once all pending sprints are signed off.
AC7  Each "ran overnight" entry carries a direct link to its sprint brief.
AC8  The evening flow surfaces the SAME "waiting on you" list as the morning
     brief (single shared builder — the home roll-up equals the per-project
     lists, independent of which flow renders it).
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
ROUTERS_DIR = DASHBOARD_DIR / "routers"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))

REPO = "owner/widget"
SLUG = "widget"
DATE = "2026-06-10"
# Window mirrors the daily-artifact 6 AM anchor: [(D-1) 6AM, D 6AM).
WIN_START = "2026-06-09T06:00:00"
WIN_END = "2026-06-10T06:00:00"
# A timestamp comfortably inside the overnight window.
OVERNIGHT = "2026-06-10T02:30:00"
# A timestamp OUTSIDE the window (previous afternoon, before the anchor start).
OUTSIDE = "2026-06-08T15:00:00"


def _load_module(name: str, path: Path):
    if "routers" not in sys.modules:
        stub = types.ModuleType("routers")
        stub.__path__ = [str(ROUTERS_DIR)]  # type: ignore[attr-defined]
        sys.modules["routers"] = stub
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


import db as db_module  # noqa: E402

svc = _load_module("routers.brief_service", ROUTERS_DIR / "brief_service.py")


@pytest.fixture()
def fresh_db(tmp_path):
    db_file = tmp_path / "test_864.db"
    original = db_module.DB_PATH
    db_module.DB_PATH = db_file
    db_module.init_db()
    yield db_module
    db_module.DB_PATH = original


@pytest.fixture(autouse=True)
def one_project(monkeypatch):
    monkeypatch.setattr(
        svc, "_load_projects",
        lambda: [{"repo": REPO, "name": "Widget"}],
        raising=True,
    )
    monkeypatch.setattr(svc, "_sprint_goal",
                        lambda project_key, slug, label: "", raising=True)


# ── seeding helpers (real writes) ─────────────────────────────────────────────

def _sprint(db, label, state, *, project=REPO, started_at=None, ended_at=None,
            created_at=None):
    with db.get_conn() as conn:
        db._create_sprint_lifecycle_tables(conn)
        conn.execute(
            "INSERT INTO sprints (label, project, state, created_at, started_at, "
            "ended_at) VALUES (?, ?, ?, ?, ?, ?)",
            (label, project, state, created_at, started_at, ended_at),
        )
        conn.commit()


def _issue(db, number, title, labels, *, repo=REPO, state="open"):
    db.upsert_issues(repo, [{
        "number": number,
        "title": title,
        "state": state,
        "labels": [{"name": n} for n in labels],
        "updatedAt": f"{DATE}T00:00:00Z",
    }])


def _order(db, label, nums):
    db.set_sprint_ticket_order(label, nums)


def _finish_event(db, label, *, summary_issue=400, pr_number=321):
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO events (project, timestamp, source, actor, type, "
            "target, action_id, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (REPO, OVERNIGHT, "dashboard", "test", "sprint_finished", label,
             None, json.dumps({"pr_number": pr_number,
                               "summary_issue_number": summary_issue})),
        )
        conn.commit()


def _project_brief(db):
    return svc.build_project_brief(SLUG, date=DATE, window=(WIN_START, WIN_END))


def _home_brief(db):
    return svc.build_home_brief(date=DATE, window=(WIN_START, WIN_END))


# ── AC1: "Ran overnight" lists each sprint with outcome + brief link ──────────

def test_ac1_ran_overnight_lists_sprints_with_outcome_and_link(fresh_db):
    _sprint(fresh_db, "sprint-50", "completed",
            started_at="2026-06-10T01:00:00", ended_at=OVERNIGHT)
    _finish_event(fresh_db, "sprint-50", summary_issue=400)

    brief = _project_brief(fresh_db)
    assert "ran_overnight" in brief
    entry = next(e for e in brief["ran_overnight"] if e["label"] == "sprint-50")
    assert entry["outcome"] == "success"
    # AC7: a direct link to the sprint brief (summary issue and/or brief path).
    assert entry["summary_issue_number"] == 400
    assert entry["brief_path"] == "docs/sprint-briefs/sprint-50.md"


def test_ac1_ran_overnight_failure_outcome(fresh_db):
    _sprint(fresh_db, "sprint-51", "needs_rework",
            started_at="2026-06-10T01:00:00", ended_at=OVERNIGHT)
    brief = _project_brief(fresh_db)
    entry = next(e for e in brief["ran_overnight"] if e["label"] == "sprint-51")
    assert entry["outcome"] == "failure"


def test_ac1_ran_overnight_in_home_rollup(fresh_db):
    _sprint(fresh_db, "sprint-50", "completed",
            started_at="2026-06-10T01:00:00", ended_at=OVERNIGHT)
    home = _home_brief(fresh_db)
    assert "ran_overnight" in home
    labels = {e["label"] for e in home["ran_overnight"]}
    assert "sprint-50" in labels
    # home entries are tagged with their project for navigation
    assert all(e.get("project") for e in home["ran_overnight"])


# ── AC2: "Waiting on you" lists pending-signoff sprints w/ count + est hours ──

def test_ac2_waiting_on_you_count_and_estimated_hours(fresh_db):
    _sprint(fresh_db, "sprint-52", "ready_to_merge",
            started_at="2026-06-10T01:00:00", ended_at=OVERNIGHT)
    _issue(fresh_db, 100, "Big feature", ["size-L"])   # 30 min
    _issue(fresh_db, 101, "Small fix", ["size-M"])     # 15 min
    _order(fresh_db, "sprint-52", [100, 101])

    brief = _project_brief(fresh_db)
    assert "waiting_on_you" in brief
    entry = next(e for e in brief["waiting_on_you"] if e["label"] == "sprint-52")
    assert entry["ticket_count"] == 2
    # 30 + 15 = 45 min = 0.75 h
    assert entry["estimated_hours"] == pytest.approx(0.75)


# ── AC3: "Waiting on you" empty when nothing pending sign-off ─────────────────

def test_ac3_waiting_on_you_empty_when_none_pending(fresh_db):
    _sprint(fresh_db, "sprint-50", "completed",
            started_at="2026-06-10T01:00:00", ended_at=OVERNIGHT)
    brief = _project_brief(fresh_db)
    assert brief["waiting_on_you"] == []
    home = _home_brief(fresh_db)
    assert home["waiting_on_you"] == []


# ── AC4: "Ran overnight" empty when nothing ran in the window ─────────────────

def test_ac4_ran_overnight_empty_when_nothing_in_window(fresh_db):
    # A sprint that ended OUTSIDE the overnight window must not appear.
    _sprint(fresh_db, "sprint-old", "completed",
            started_at="2026-06-08T13:00:00", ended_at=OUTSIDE)
    brief = _project_brief(fresh_db)
    assert brief["ran_overnight"] == []


# ── AC5 / AC6: nav-pill sign-off signal ──────────────────────────────────────

def test_ac5_awaiting_signoff_positive_when_pending(fresh_db):
    _sprint(fresh_db, "sprint-52", "ready_to_merge",
            started_at="2026-06-10T01:00:00", ended_at=OVERNIGHT)
    _order(fresh_db, "sprint-52", [])
    home = _home_brief(fresh_db)
    assert home["global_kpis"]["awaiting_signoff"] >= 1


def test_ac6_awaiting_signoff_zero_when_all_signed_off(fresh_db):
    # Sprint already merged/completed (signed off) → not awaiting sign-off.
    _sprint(fresh_db, "sprint-52", "completed",
            started_at="2026-06-10T01:00:00", ended_at=OVERNIGHT)
    home = _home_brief(fresh_db)
    assert home["global_kpis"]["awaiting_signoff"] == 0


# ── AC7: each "ran overnight" entry links to its sprint brief ─────────────────

def test_ac7_every_ran_overnight_entry_has_a_brief_link(fresh_db):
    _sprint(fresh_db, "sprint-50", "completed",
            started_at="2026-06-10T01:00:00", ended_at=OVERNIGHT)
    _finish_event(fresh_db, "sprint-50", summary_issue=400)
    _sprint(fresh_db, "sprint-51", "needs_rework",
            started_at="2026-06-10T01:00:00", ended_at=OVERNIGHT)

    brief = _project_brief(fresh_db)
    assert brief["ran_overnight"]
    for entry in brief["ran_overnight"]:
        # A usable link exists: a summary-issue number OR a sprint-brief path.
        assert entry.get("summary_issue_number") is not None \
            or entry.get("brief_path")


# ── AC8: evening flow surfaces the SAME waiting-on-you list as the morning ────

def test_ac8_evening_flow_matches_morning_waiting_list(fresh_db):
    _sprint(fresh_db, "sprint-52", "ready_to_merge",
            started_at="2026-06-10T01:00:00", ended_at=OVERNIGHT)
    _issue(fresh_db, 100, "Big feature", ["size-L"])
    _order(fresh_db, "sprint-52", [100])

    # The single shared builder feeds both flows: the home roll-up's
    # waiting_on_you is exactly the concatenation of each project's list.
    home = _home_brief(fresh_db)
    project = _project_brief(fresh_db)

    home_keys = [(w["label"], w["ticket_count"], w["estimated_hours"])
                 for w in home["waiting_on_you"]]
    project_keys = [(w["label"], w["ticket_count"], w["estimated_hours"])
                    for w in project["waiting_on_you"]]
    assert home_keys == project_keys
    assert ("sprint-52", 1, pytest.approx(0.5)) in \
        [(k[0], k[1], k[2]) for k in home_keys]
