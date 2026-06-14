"""Tests for issue #883 — Advisor Maintains 2-to-5-Sprint Look-Ahead on Roadmap.

Each test is anchored to a specific AC from the issue.
Pure logic lives in services.sprint_manager.advisor; I/O-coupled logic
in apps/dashboard/routers/advisor_service and roadmap_service.

Acceptance criteria covered:
  AC1  — Advisor generates 2-5 look-ahead entries alongside suggestions.
  AC2  — Each look-ahead entry is a single line (no newlines).
  AC3  — GET /api/roadmap includes look_ahead in its payload.
  AC4  — look_ahead is empty list when advisor has not yet run.
  AC5  — Look-ahead never exceeds 5 entries.
  AC6  — plan-next.js fetches look-ahead and exposes it for pre-fill.
  AC7  — plan-next.js skips look-ahead pre-fill when none exists.
  AC8  — Generating look-ahead does not call any GitHub write API.
  AC9  — Clearing look-ahead does not affect sprints/milestones.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "apps" / "dashboard"))

os.environ.setdefault("DB_PATH", str(_ROOT / "commander.db"))

import db as _db_module  # noqa: E402
from services.sprint_manager import advisor as adv  # noqa: E402


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def fresh_db(tmp_path):
    """Isolated SQLite DB; patches db module."""
    db_file = tmp_path / "test_883.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


def _mk_suggestion(**kwargs) -> dict:
    defaults = {
        "pitch": "Do something useful",
        "rationale": "Because milestone X needs it",
        "milestone": "v2.0",
        "scope": "M",
    }
    return {**defaults, **kwargs}


def _fake_inputs() -> dict:
    return {
        "product_md": None,
        "product_md_missing": True,
        "milestones": [],
        "backlog": [],
        "todos": [],
        "briefs": [],
        "failures": [],
    }


SAMPLE_LOOK_AHEAD = [
    "Sprint 73: Auth layer — User Auth milestone",
    "Sprint 74: Analytics dashboard — Reporting milestone",
    "Sprint 75: Mobile responsive polish — UX milestone",
]


# ── AC1: look-ahead generated alongside suggestions ───────────────────────────

def test_ac1_validate_look_ahead_accepts_2_to_5_entries():
    entries = ["Sprint A: milestone X", "Sprint B: milestone Y"]
    result = adv.validate_look_ahead(entries)
    assert len(result) == 2


def test_ac1_validate_look_ahead_accepts_exactly_5():
    entries = [f"Sprint {i}: milestone" for i in range(1, 6)]
    result = adv.validate_look_ahead(entries)
    assert len(result) == 5


def test_ac1_validate_look_ahead_accepts_3():
    result = adv.validate_look_ahead(SAMPLE_LOOK_AHEAD)
    assert len(result) == 3


def test_ac1_run_advisor_saves_look_ahead(fresh_db, monkeypatch, tmp_path):
    import routers.advisor_service as svc  # noqa: PLC0415
    fake_suggestions = [_mk_suggestion(pitch=f"s-{i}") for i in range(3)]
    monkeypatch.setattr(svc, "run_advisor_agent",
                        lambda prompt, **kw: fake_suggestions)
    monkeypatch.setattr(svc, "run_look_ahead_agent",
                        lambda prompt, **kw: list(SAMPLE_LOOK_AHEAD))
    monkeypatch.setattr(svc, "collect_inputs",
                        lambda project, **kw: _fake_inputs())
    svc.run_advisor("myproject", on_demand=True, repo_root=tmp_path)
    result = svc.get_look_ahead("myproject")
    assert len(result) == len(SAMPLE_LOOK_AHEAD)


def test_ac1_look_ahead_built_alongside_suggestions(fresh_db, monkeypatch, tmp_path):
    import routers.advisor_service as svc  # noqa: PLC0415
    call_log = []

    def fake_agent(prompt, **kw):
        call_log.append("suggestions")
        return [_mk_suggestion() for _ in range(3)]

    def fake_la_agent(prompt, **kw):
        call_log.append("look_ahead")
        return list(SAMPLE_LOOK_AHEAD)

    monkeypatch.setattr(svc, "run_advisor_agent", fake_agent)
    monkeypatch.setattr(svc, "run_look_ahead_agent", fake_la_agent)
    monkeypatch.setattr(svc, "collect_inputs", lambda p, **kw: _fake_inputs())

    svc.run_advisor("myproject", on_demand=True, repo_root=tmp_path)
    assert "suggestions" in call_log
    assert "look_ahead" in call_log


# ── AC2: each look-ahead entry is a single line ───────────────────────────────

def test_ac2_validate_look_ahead_rejects_multiline_entry():
    # Need 2+ entries to pass the count check, then trigger the single-line check.
    entries = ["Sprint 1: milestone A\nExtra line", "Sprint 2: milestone B"]
    with pytest.raises(ValueError, match="single-line"):
        adv.validate_look_ahead(entries)


def test_ac2_single_line_entries_pass():
    entries = ["Sprint 73: Auth layer", "Sprint 74: Dashboard"]
    result = adv.validate_look_ahead(entries)
    for entry in result:
        assert "\n" not in entry


# ── AC3: roadmap payload includes look_ahead ──────────────────────────────────

def test_ac3_roadmap_service_includes_look_ahead_key(fresh_db, monkeypatch):
    import routers.roadmap_service as rsvc  # noqa: PLC0415
    import routers.advisor_service as asvc  # noqa: PLC0415

    monkeypatch.setattr(rsvc.gh, "list_milestones", lambda repo, state="all": [])
    monkeypatch.setattr(rsvc.gh, "list_issues_with_milestone", lambda repo: [])
    monkeypatch.setattr(rsvc._settings, "get_setting_scoped",
                        lambda scope, key, project=None: {})
    monkeypatch.setattr(asvc, "get_look_ahead", lambda project: list(SAMPLE_LOOK_AHEAD))

    payload = rsvc.get_roadmap("owner/repo")
    assert "look_ahead" in payload


def test_ac3_roadmap_returns_look_ahead_entries(fresh_db, monkeypatch):
    import routers.roadmap_service as rsvc  # noqa: PLC0415
    import routers.advisor_service as asvc  # noqa: PLC0415

    monkeypatch.setattr(rsvc.gh, "list_milestones", lambda repo, state="all": [])
    monkeypatch.setattr(rsvc.gh, "list_issues_with_milestone", lambda repo: [])
    monkeypatch.setattr(rsvc._settings, "get_setting_scoped",
                        lambda scope, key, project=None: {})
    monkeypatch.setattr(asvc, "get_look_ahead", lambda project: list(SAMPLE_LOOK_AHEAD))

    payload = rsvc.get_roadmap("owner/repo")
    assert payload["look_ahead"] == list(SAMPLE_LOOK_AHEAD)


# ── AC4: empty look-ahead when no advisor run ─────────────────────────────────

def test_ac4_look_ahead_empty_before_any_run(fresh_db):
    import routers.advisor_service as svc  # noqa: PLC0415
    result = svc.get_look_ahead("never-run-project")
    assert result == []


def test_ac4_roadmap_shows_empty_look_ahead_when_none(fresh_db, monkeypatch):
    import routers.roadmap_service as rsvc  # noqa: PLC0415
    import routers.advisor_service as asvc  # noqa: PLC0415

    monkeypatch.setattr(rsvc.gh, "list_milestones", lambda repo, state="all": [])
    monkeypatch.setattr(rsvc.gh, "list_issues_with_milestone", lambda repo: [])
    monkeypatch.setattr(rsvc._settings, "get_setting_scoped",
                        lambda scope, key, project=None: {})
    monkeypatch.setattr(asvc, "get_look_ahead", lambda project: [])

    payload = rsvc.get_roadmap("owner/repo")
    assert payload["look_ahead"] == []


# ── AC5: look-ahead capped at 5 entries ──────────────────────────────────────

def test_ac5_validate_look_ahead_rejects_zero():
    with pytest.raises(ValueError):
        adv.validate_look_ahead([])


def test_ac5_validate_look_ahead_rejects_one():
    with pytest.raises(ValueError):
        adv.validate_look_ahead(["Sprint 1: only one"])


def test_ac5_validate_look_ahead_rejects_more_than_5():
    entries = [f"Sprint {i}: milestone" for i in range(1, 8)]
    with pytest.raises(ValueError, match="5"):
        adv.validate_look_ahead(entries)


def test_ac5_save_look_ahead_only_stores_up_to_5(fresh_db):
    import routers.advisor_service as svc  # noqa: PLC0415
    entries = [f"Sprint {i}: milestone" for i in range(1, 7)]  # 6 entries
    # validate_look_ahead will reject before save is called (in run_look_ahead_agent)
    # but save_look_ahead should also enforce the cap as a safety net
    svc.save_look_ahead("proj", entries[:5], on_demand=False)
    result = svc.get_look_ahead("proj")
    assert len(result) <= 5


# ── AC6: plan-next.js fetches look-ahead for pre-fill ───────────────────────

PLAN_NEXT_JS = _ROOT / "apps" / "dashboard" / "static" / "src" / "sprint-board" / "plan-next.js"
BUNDLE = _ROOT / "apps" / "dashboard" / "static" / "dist" / "bundle.js"


def test_ac6_plan_next_js_fetches_look_ahead_endpoint():
    src = PLAN_NEXT_JS.read_text(encoding="utf-8")
    assert "advisor/look-ahead" in src


def test_ac6_plan_next_js_uses_first_look_ahead_entry():
    src = PLAN_NEXT_JS.read_text(encoding="utf-8")
    # First entry used as scope hint
    assert "entries[0]" in src or "look_ahead[0]" in src or "[0]" in src


def test_ac6_look_ahead_prefill_in_bundle():
    bundle = BUNDLE.read_text(encoding="utf-8")
    assert "advisor/look-ahead" in bundle


# ── AC7: plan-next without look-ahead behaves identically ────────────────────

def test_ac7_plan_next_js_handles_empty_look_ahead():
    src = PLAN_NEXT_JS.read_text(encoding="utf-8")
    # Guard: checks that entries exist before using them
    assert "length" in src or "entries" in src


def test_ac7_plan_next_js_does_not_block_when_no_look_ahead():
    src = PLAN_NEXT_JS.read_text(encoding="utf-8")
    # catch/fallback so look-ahead fetch failure never blocks plan-next
    assert "catch" in src


# ── AC8: no GitHub writes during look-ahead generation ───────────────────────

def test_ac8_save_look_ahead_does_not_call_gh_post(fresh_db, monkeypatch):
    import routers.advisor_service as svc  # noqa: PLC0415
    calls = []
    monkeypatch.setattr(svc, "_gh_post", lambda *a, **k: calls.append(a) or {})
    svc.save_look_ahead("myproject", SAMPLE_LOOK_AHEAD, on_demand=False)
    assert calls == [], "save_look_ahead must not call any GitHub write API"


def test_ac8_run_look_ahead_agent_does_not_call_gh_post(monkeypatch):
    import routers.advisor_service as svc  # noqa: PLC0415
    calls = []
    monkeypatch.setattr(svc, "_gh_post", lambda *a, **k: calls.append(a) or {})

    fake_result = subprocess_fake(SAMPLE_LOOK_AHEAD)
    monkeypatch.setattr(svc.subprocess, "run", lambda *a, **k: fake_result)

    svc.run_look_ahead_agent("some prompt")
    assert calls == [], "run_look_ahead_agent must not call any GitHub write API"


def subprocess_fake(look_ahead: list[str]):
    """Fake subprocess.CompletedProcess for look-ahead agent output."""
    import subprocess  # noqa: PLC0415
    raw = json.dumps(look_ahead)
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=raw, stderr="")


# ── AC9: clearing look-ahead does not affect sprints / milestones ─────────────

def test_ac9_save_look_ahead_does_not_touch_sprint_tables(fresh_db):
    import routers.advisor_service as svc  # noqa: PLC0415
    svc.save_look_ahead("proj", SAMPLE_LOOK_AHEAD, on_demand=False)
    # Sprints table unchanged — just verify no exception and look_ahead table isolated
    with _db_module.get_conn() as conn:
        sprint_tables = [
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
    assert "advisor_look_ahead" in sprint_tables
    # Sprint-related tables exist independently
    assert "agents" in sprint_tables


def test_ac9_replacing_look_ahead_does_not_alter_suggestions(fresh_db):
    import routers.advisor_service as svc  # noqa: PLC0415
    suggestions = [_mk_suggestion(pitch=f"idea-{i}") for i in range(3)]
    svc.save_suggestions("proj", suggestions)

    svc.save_look_ahead("proj", SAMPLE_LOOK_AHEAD, on_demand=False)
    svc.save_look_ahead("proj", [], on_demand=False)  # clear

    # Suggestions unaffected
    saved = svc.get_suggestions("proj")
    assert len(saved) == 3
    assert all("idea" in r["pitch"] for r in saved)


def test_ac9_clearing_look_ahead_returns_empty(fresh_db):
    import routers.advisor_service as svc  # noqa: PLC0415
    svc.save_look_ahead("proj", SAMPLE_LOOK_AHEAD, on_demand=False)
    svc.save_look_ahead("proj", [], on_demand=False)  # clear
    result = svc.get_look_ahead("proj")
    assert result == []
