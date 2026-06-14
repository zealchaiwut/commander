"""Tests for issue #881 — Add daily advisor agent for next-build suggestions.

Each test is anchored to a specific acceptance criterion from the issue.
Pure scheduling/validation logic lives in ``services.sprint_manager.advisor``
and is exercised directly (no live server, subprocesses, or wall clock).
DB-coupled tests use an isolated SQLite file via the ``fresh_db`` fixture.

Acceptance criteria covered:
  AC1  — Advisor runs once per calendar day; silently skips when dashboard down.
  AC2  — Agent reads all required context inputs.
  AC3  — Each run produces exactly 3-5 suggestions; outside that range = failure.
  AC4  — Every suggestion has: pitch, rationale, milestone, scope (S/M/L).
  AC5  — Suggestions written to local DB as drafts; no GitHub writes.
  AC6  — POST /api/projects/{project}/advisor/run triggers on-demand run.
  AC7  — On-demand run replaces (not appends) draft suggestions.
  AC8  — GET /api/projects/{project}/advisor/suggestions returns current drafts.
  AC9  — Missing PRODUCT.md: agent still runs, notes absence in rationale.
  AC10 — Daily schedule does not catch up missed runs; fires only at scheduled time.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "apps" / "dashboard"))

os.environ.setdefault("DB_PATH", str(_ROOT / "commander.db"))

import db as _db_module  # noqa: E402
from services.sprint_manager import advisor as adv  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────

def _mk_suggestion(**kwargs) -> dict:
    defaults = {
        "pitch": "Do something useful",
        "rationale": "Because milestone X needs it",
        "milestone": "v2.0",
        "scope": "M",
    }
    return {**defaults, **kwargs}


# ── In-memory settings store ──────────────────────────────────────────────────

@pytest.fixture
def mem_store(monkeypatch):
    store: dict = {}

    def _key(scope, key, project):
        return f"{scope}|{project or ''}|{key}"

    def fake_get_scoped(scope, key, project=None):
        return store.get(_key(scope, key, project), {})

    def fake_set(scope, key, value, project=None):
        store[_key(scope, key, project)] = value

    monkeypatch.setattr(adv.settings_repo, "get_setting_scoped", fake_get_scoped)
    monkeypatch.setattr(adv.settings_repo, "set_setting", fake_set)
    return store


# ── DB fixture ────────────────────────────────────────────────────────────────

@pytest.fixture
def fresh_db(tmp_path):
    """Isolated SQLite DB; patches db module."""
    db_file = tmp_path / "test_881.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


# ── AC1: daily fire logic ─────────────────────────────────────────────────────

def test_ac1_fires_when_time_matches_and_not_yet_fired_today():
    assert adv.should_fire("09:00", "09:00", None, "2026-06-14") is True


def test_ac1_does_not_fire_twice_same_day():
    assert adv.should_fire("09:00", "09:00", "2026-06-14", "2026-06-14") is False


def test_ac1_does_not_fire_when_no_schedule_configured():
    assert adv.should_fire("", "09:00", None, "2026-06-14") is False
    assert adv.should_fire(None, "09:00", None, "2026-06-14") is False


def test_ac1_does_not_fire_before_scheduled_minute():
    assert adv.should_fire("09:00", "08:59", None, "2026-06-14") is False


def test_ac1_fires_again_on_new_day_after_previous_fire():
    assert adv.should_fire("09:00", "09:00", "2026-06-13", "2026-06-14") is True


def test_ac1_settings_round_trip(mem_store):
    adv.set_advisor_time("myproject", "08:30")
    assert adv.get_advisor_time("myproject") == "08:30"


def test_ac1_last_fired_round_trip(mem_store):
    adv.set_advisor_last_fired("myproject", "2026-06-14")
    assert adv.get_advisor_last_fired("myproject") == "2026-06-14"


# ── AC2: required inputs ──────────────────────────────────────────────────────

def test_ac2_collect_inputs_includes_all_required_keys(tmp_path):
    (tmp_path / "PRODUCT.md").write_text("# Product\nThis product does X.")
    import routers.advisor_service as svc  # noqa: PLC0415
    inputs = svc.collect_inputs(
        "owner/repo",
        repo_root=tmp_path,
        gh_list_milestones=lambda repo: [],
        gh_list_backlog=lambda repo: [],
        db_get_todos=lambda project: [],
        db_get_briefs=lambda project, n: [],
        db_get_failures=lambda project, n: [],
    )
    required_keys = {
        "product_md", "milestones", "backlog", "todos", "briefs", "failures",
    }
    assert required_keys <= set(inputs.keys())


def test_ac2_product_md_content_included(tmp_path):
    (tmp_path / "PRODUCT.md").write_text("We build cool things.")
    import routers.advisor_service as svc  # noqa: PLC0415
    inputs = svc.collect_inputs(
        "owner/repo",
        repo_root=tmp_path,
        gh_list_milestones=lambda repo: [],
        gh_list_backlog=lambda repo: [],
        db_get_todos=lambda project: [],
        db_get_briefs=lambda project, n: [],
        db_get_failures=lambda project, n: [],
    )
    assert "cool things" in (inputs["product_md"] or "")


# ── AC3: suggestion count validation ─────────────────────────────────────────

def test_ac3_rejects_fewer_than_3_suggestions():
    with pytest.raises(ValueError, match="3"):
        adv.validate_suggestions([_mk_suggestion() for _ in range(2)])


def test_ac3_rejects_more_than_5_suggestions():
    with pytest.raises(ValueError, match="5"):
        adv.validate_suggestions([_mk_suggestion() for _ in range(6)])


def test_ac3_accepts_exactly_3_suggestions():
    result = adv.validate_suggestions([_mk_suggestion() for _ in range(3)])
    assert len(result) == 3


def test_ac3_accepts_exactly_5_suggestions():
    result = adv.validate_suggestions([_mk_suggestion() for _ in range(5)])
    assert len(result) == 5


def test_ac3_accepts_4_suggestions():
    result = adv.validate_suggestions([_mk_suggestion() for _ in range(4)])
    assert len(result) == 4


# ── AC4: suggestion structure ─────────────────────────────────────────────────

def test_ac4_valid_suggestion_passes():
    adv.validate_suggestion_structure(_mk_suggestion())  # should not raise


@pytest.mark.parametrize("missing_field", ["pitch", "rationale", "milestone", "scope"])
def test_ac4_missing_field_raises(missing_field):
    s = _mk_suggestion()
    del s[missing_field]
    with pytest.raises(ValueError, match=missing_field):
        adv.validate_suggestion_structure(s)


def test_ac4_invalid_scope_raises():
    s = _mk_suggestion(scope="XL")
    with pytest.raises(ValueError, match="scope"):
        adv.validate_suggestion_structure(s)


@pytest.mark.parametrize("scope", ["S", "M", "L"])
def test_ac4_valid_scopes_pass(scope):
    adv.validate_suggestion_structure(_mk_suggestion(scope=scope))  # no raise


# ── AC5: DB storage, no GitHub writes ────────────────────────────────────────

def test_ac5_save_then_get_returns_suggestions(fresh_db):
    import routers.advisor_service as svc  # noqa: PLC0415
    suggestions = [_mk_suggestion(pitch=f"idea-{i}") for i in range(3)]
    svc.save_suggestions("myproject", suggestions, on_demand=False)
    result = svc.get_suggestions("myproject")
    assert len(result) == 3
    pitches = {r["pitch"] for r in result}
    assert pitches == {"idea-0", "idea-1", "idea-2"}


def test_ac5_no_github_write_on_save(fresh_db, monkeypatch):
    import routers.advisor_service as svc  # noqa: PLC0415
    calls = []
    monkeypatch.setattr(svc, "_gh_post", lambda *a, **k: calls.append(a) or {})
    svc.save_suggestions("myproject", [_mk_suggestion() for _ in range(3)])
    assert calls == [], "save_suggestions must not call any GitHub write API"


def test_ac5_suggestions_persisted_across_calls(fresh_db):
    import routers.advisor_service as svc  # noqa: PLC0415
    svc.save_suggestions("p1", [_mk_suggestion(pitch="x")] * 3)
    result = svc.get_suggestions("p1")
    assert len(result) == 3


# ── AC6: on-demand run ────────────────────────────────────────────────────────

def test_ac6_run_advisor_calls_agent_and_saves(fresh_db, monkeypatch, tmp_path):
    import routers.advisor_service as svc  # noqa: PLC0415
    fake_suggestions = [_mk_suggestion(pitch=f"on-demand-{i}") for i in range(3)]
    monkeypatch.setattr(svc, "run_advisor_agent", lambda prompt, **kw: fake_suggestions)
    monkeypatch.setattr(svc, "collect_inputs", lambda project, **kw: {
        "product_md": None, "product_md_missing": True,
        "milestones": [], "backlog": [], "todos": [], "briefs": [], "failures": [],
    })
    result = svc.run_advisor("myproject", on_demand=True, repo_root=tmp_path)
    assert len(result) == 3
    saved = svc.get_suggestions("myproject")
    assert len(saved) == 3


# ── AC7: on-demand replaces, not appends ─────────────────────────────────────

def test_ac7_on_demand_replaces_existing_suggestions(fresh_db):
    import routers.advisor_service as svc  # noqa: PLC0415
    old = [_mk_suggestion(pitch=f"old-{i}") for i in range(3)]
    svc.save_suggestions("proj", old)

    new = [_mk_suggestion(pitch=f"new-{i}") for i in range(4)]
    svc.save_suggestions("proj", new, on_demand=True)

    result = svc.get_suggestions("proj")
    assert len(result) == 4
    assert all("new" in r["pitch"] for r in result)


def test_ac7_scheduled_run_also_replaces(fresh_db):
    import routers.advisor_service as svc  # noqa: PLC0415
    svc.save_suggestions("p", [_mk_suggestion(pitch="first")] * 3)
    svc.save_suggestions("p", [_mk_suggestion(pitch="second")] * 3, on_demand=False)
    result = svc.get_suggestions("p")
    assert len(result) == 3
    assert all(r["pitch"] == "second" for r in result)


# ── AC8: GET suggestions ──────────────────────────────────────────────────────

def test_ac8_get_suggestions_empty_when_no_run(fresh_db):
    import routers.advisor_service as svc  # noqa: PLC0415
    result = svc.get_suggestions("never-run-project")
    assert result == []


def test_ac8_get_suggestions_returns_all_fields(fresh_db):
    import routers.advisor_service as svc  # noqa: PLC0415
    s = _mk_suggestion(pitch="p", rationale="r", milestone="mv1", scope="L")
    svc.save_suggestions("proj", [s, s, s])
    rows = svc.get_suggestions("proj")
    for row in rows:
        assert "pitch" in row
        assert "rationale" in row
        assert "milestone" in row
        assert "scope" in row
        assert "run_at" in row


# ── AC9: missing PRODUCT.md ───────────────────────────────────────────────────

def test_ac9_missing_product_md_does_not_crash(tmp_path):
    import routers.advisor_service as svc  # noqa: PLC0415
    inputs = svc.collect_inputs(
        "owner/repo",
        repo_root=tmp_path,
        gh_list_milestones=lambda repo: [],
        gh_list_backlog=lambda repo: [],
        db_get_todos=lambda project: [],
        db_get_briefs=lambda project, n: [],
        db_get_failures=lambda project, n: [],
    )
    assert inputs["product_md"] is None
    assert inputs["product_md_missing"] is True


def test_ac9_prompt_notes_product_md_absence(tmp_path):
    import routers.advisor_service as svc  # noqa: PLC0415
    inputs = {
        "product_md": None,
        "product_md_missing": True,
        "milestones": [],
        "backlog": [],
        "todos": [],
        "briefs": [],
        "failures": [],
    }
    prompt = svc.build_prompt("owner/repo", inputs)
    assert "PRODUCT.md" in prompt
    assert "absent" in prompt.lower() or "missing" in prompt.lower()


# ── AC10: no catch-up on missed runs ─────────────────────────────────────────

def test_ac10_does_not_catch_up_if_time_has_passed():
    # Missed 09:00 — now it's 14:00, last fired yesterday. No catch-up.
    assert adv.should_fire("09:00", "14:00", "2026-06-13", "2026-06-14") is False


def test_ac10_does_not_fire_on_startup_if_schedule_not_due():
    # Dashboard just started at 08:55; schedule is 09:00.
    assert adv.should_fire("09:00", "08:55", None, "2026-06-14") is False


def test_ac10_fires_normally_when_due():
    # No missed-run catch-up logic, just fires at 09:00 today.
    assert adv.should_fire("09:00", "09:00", "2026-06-13", "2026-06-14") is True
