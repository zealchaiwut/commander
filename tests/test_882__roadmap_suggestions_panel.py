"""Tests for issue #882 — Add Roadmap Suggestions Panel with Accept and Dismiss.

TDD: tests written before implementation.

Acceptance criteria covered:
  AC1  — Roadmap tab suggestions panel: GET /advisor/suggestions returns cards with pitch,
          rationale, milestone, scope.
  AC2  — Accept/Dismiss buttons (UI): dismiss/accept endpoints return 200 while no action
          in flight; 409 when called twice rapidly is not required (idempotent dismiss).
  AC3  — Accept returns seed_prompt containing pitch, rationale, milestone, scope.
  AC4  — Accept response attributes suggestion (includes suggestion_id, pitch, milestone).
  AC5  — Accept response includes milestone_name for downstream ticket creation.
  AC6  — Dismiss persists pitch to advisor_dismissed; suggestion removed from active list.
  AC7  — Dismissed pitches injected into build_prompt as exclusions list.
  AC8  — Dismissed suggestion absent from get_suggestions after persist.
  AC9  — Empty list returned (not hidden) when all suggestions dismissed.
  AC10 — Dismiss one pitch does not remove other suggestions.
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


# ── fixtures ──────────────────────────────────────────────────────────────────

def _mk_suggestion(**kwargs) -> dict:
    defaults = {
        "pitch": "Add dark mode support",
        "rationale": "Multiple todo notes request theming; PRODUCT.md calls for accessibility.",
        "milestone": "v3.0-UX",
        "scope": "M",
    }
    return {**defaults, **kwargs}


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test_882.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


# ── AC1: get_suggestions returns cards with all required fields ───────────────

def test_ac1_get_suggestions_returns_pitch_rationale_milestone_scope(fresh_db):
    import routers.advisor_service as svc  # noqa: PLC0415
    s = _mk_suggestion()
    svc.save_suggestions("zealchaiwut/commander", [s, s, s])
    rows = svc.get_suggestions("zealchaiwut/commander")
    assert len(rows) >= 1
    row = rows[0]
    assert "pitch" in row
    assert "rationale" in row
    assert "milestone" in row
    assert "scope" in row


def test_ac1_get_suggestions_returns_id_field(fresh_db):
    """Each suggestion row exposes an id so dismiss/accept can reference it."""
    import routers.advisor_service as svc  # noqa: PLC0415
    svc.save_suggestions("p", [_mk_suggestion()] * 3)
    rows = svc.get_suggestions("p")
    for row in rows:
        assert "id" in row, "get_suggestions must include 'id' field"
        assert isinstance(row["id"], int)


# ── AC6: dismiss persists to storage ─────────────────────────────────────────

def test_ac6_dismiss_saves_pitch_to_dismissed_table(fresh_db):
    import routers.advisor_service as svc  # noqa: PLC0415
    svc.save_suggestions("p", [_mk_suggestion(pitch="idea-A")] * 3)
    rows = svc.get_suggestions("p")
    suggestion_id = rows[0]["id"]
    svc.dismiss_suggestion("p", suggestion_id)
    dismissed = svc.get_dismissed_pitches("p")
    assert "idea-A" in dismissed


def test_ac6_dismiss_removes_suggestion_from_active_list(fresh_db):
    """After dismissing, get_suggestions must not return that suggestion."""
    import routers.advisor_service as svc  # noqa: PLC0415
    svc.save_suggestions("p", [
        _mk_suggestion(pitch="keep-me"),
        _mk_suggestion(pitch="dismiss-me"),
        _mk_suggestion(pitch="also-keep"),
    ])
    rows = svc.get_suggestions("p")
    dismiss_id = next(r["id"] for r in rows if r["pitch"] == "dismiss-me")
    svc.dismiss_suggestion("p", dismiss_id)
    active = svc.get_suggestions("p")
    pitches = [r["pitch"] for r in active]
    assert "dismiss-me" not in pitches


# ── AC7: dismissed pitches injected into advisor prompt ───────────────────────

def test_ac7_build_prompt_includes_dismissed_exclusions(fresh_db):
    import routers.advisor_service as svc  # noqa: PLC0415
    svc.save_suggestions("p", [_mk_suggestion(pitch="Idea X")] * 3)
    rows = svc.get_suggestions("p")
    svc.dismiss_suggestion("p", rows[0]["id"])
    inputs = {
        "product_md": None, "product_md_missing": True,
        "milestones": [], "backlog": [], "todos": [], "briefs": [], "failures": [],
        "dismissed_pitches": svc.get_dismissed_pitches("p"),
    }
    prompt = svc.build_prompt("p", inputs)
    assert "Idea X" in prompt
    assert "dismiss" in prompt.lower() or "exclude" in prompt.lower() or "do not" in prompt.lower()


def test_ac7_build_prompt_with_no_dismissed_omits_exclusion_block(fresh_db):
    import routers.advisor_service as svc  # noqa: PLC0415
    inputs = {
        "product_md": None, "product_md_missing": True,
        "milestones": [], "backlog": [], "todos": [], "briefs": [], "failures": [],
        "dismissed_pitches": [],
    }
    prompt = svc.build_prompt("p", inputs)
    assert "Previously dismissed" not in prompt


# ── AC8: dismissed doesn't reappear after refresh ────────────────────────────

def test_ac8_dismissed_absent_from_get_suggestions_after_new_advisor_run(fresh_db):
    """Even after save_suggestions replaces drafts, dismissed pitches stay filtered."""
    import routers.advisor_service as svc  # noqa: PLC0415
    pitch_to_dismiss = "Build onboarding wizard"
    svc.save_suggestions("p", [
        _mk_suggestion(pitch=pitch_to_dismiss),
        _mk_suggestion(pitch="Other idea A"),
        _mk_suggestion(pitch="Other idea B"),
    ])
    rows = svc.get_suggestions("p")
    dismiss_id = next(r["id"] for r in rows if r["pitch"] == pitch_to_dismiss)
    svc.dismiss_suggestion("p", dismiss_id)

    # Simulate a new advisor run that would re-propose the same pitch
    svc.save_suggestions("p", [
        _mk_suggestion(pitch=pitch_to_dismiss),
        _mk_suggestion(pitch="Brand new idea"),
        _mk_suggestion(pitch="Another new idea"),
    ])
    active = svc.get_suggestions("p")
    pitches = [r["pitch"] for r in active]
    assert pitch_to_dismiss not in pitches
    assert "Brand new idea" in pitches


# ── AC9: empty state when all dismissed ──────────────────────────────────────

def test_ac9_returns_empty_list_when_all_suggestions_dismissed(fresh_db):
    import routers.advisor_service as svc  # noqa: PLC0415
    svc.save_suggestions("p", [
        _mk_suggestion(pitch="A"),
        _mk_suggestion(pitch="B"),
        _mk_suggestion(pitch="C"),
    ])
    for row in svc.get_suggestions("p"):
        svc.dismiss_suggestion("p", row["id"])
    result = svc.get_suggestions("p")
    assert result == [], "empty list expected when all suggestions dismissed"


# ── AC10: dismiss one doesn't affect others ───────────────────────────────────

def test_ac10_dismiss_one_leaves_others_intact(fresh_db):
    import routers.advisor_service as svc  # noqa: PLC0415
    svc.save_suggestions("p", [
        _mk_suggestion(pitch="Keep A"),
        _mk_suggestion(pitch="Dismiss B"),
        _mk_suggestion(pitch="Keep C"),
    ])
    rows = svc.get_suggestions("p")
    dismiss_id = next(r["id"] for r in rows if r["pitch"] == "Dismiss B")
    svc.dismiss_suggestion("p", dismiss_id)
    active = [r["pitch"] for r in svc.get_suggestions("p")]
    assert "Keep A" in active
    assert "Keep C" in active
    assert "Dismiss B" not in active


# ── AC3 + AC4 + AC5: accept returns seed prompt with suggestion fields ─────────

def test_ac3_accept_prompt_contains_pitch(fresh_db):
    import routers.advisor_service as svc  # noqa: PLC0415
    import routers.suggestions_service as ss  # noqa: PLC0415
    svc.save_suggestions("p", [_mk_suggestion(pitch="Implement SSO login")] * 3)
    rows = svc.get_suggestions("p")
    result = ss.build_accept_prompt(rows[0])
    assert "Implement SSO login" in result["seed_prompt"]


def test_ac3_accept_prompt_contains_rationale(fresh_db):
    import routers.advisor_service as svc  # noqa: PLC0415
    import routers.suggestions_service as ss  # noqa: PLC0415
    svc.save_suggestions("p", [_mk_suggestion(rationale="Users keep asking for it.")] * 3)
    rows = svc.get_suggestions("p")
    result = ss.build_accept_prompt(rows[0])
    assert "Users keep asking for it." in result["seed_prompt"]


def test_ac4_accept_result_includes_attribution_fields(fresh_db):
    """Accept response must carry suggestion_id, pitch, milestone for attribution."""
    import routers.advisor_service as svc  # noqa: PLC0415
    import routers.suggestions_service as ss  # noqa: PLC0415
    svc.save_suggestions("p", [_mk_suggestion(pitch="Add search", milestone="v2.0")] * 3)
    rows = svc.get_suggestions("p")
    result = ss.build_accept_prompt(rows[0])
    assert "suggestion_id" in result
    assert result["suggestion_id"] == rows[0]["id"]
    assert result["pitch"] == "Add search"


def test_ac5_accept_result_includes_milestone_name(fresh_db):
    """Accept response includes milestone_name so the frontend can set it at post time."""
    import routers.advisor_service as svc  # noqa: PLC0415
    import routers.suggestions_service as ss  # noqa: PLC0415
    svc.save_suggestions("p", [_mk_suggestion(milestone="v3.0 Launch")] * 3)
    rows = svc.get_suggestions("p")
    result = ss.build_accept_prompt(rows[0])
    assert result["milestone_name"] == "v3.0 Launch"


# ── Router-level tests ────────────────────────────────────────────────────────

def test_dismiss_endpoint_returns_200(fresh_db):
    """POST .../dismiss returns 200 with dismissed suggestion info."""
    import routers.advisor_service as svc  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415
    from fastapi import FastAPI  # noqa: PLC0415
    from routers.suggestions import router  # noqa: PLC0415

    app = FastAPI()
    app.include_router(router)

    proj = "myproject"
    svc.save_suggestions(proj, [_mk_suggestion(pitch="to-dismiss")] * 3)
    rows = svc.get_suggestions(proj)
    sid = rows[0]["id"]

    client = TestClient(app)
    resp = client.post(f"/api/projects/{proj}/advisor/suggestions/{sid}/dismiss")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("dismissed") is True


def test_accept_endpoint_returns_seed_prompt(fresh_db):
    """POST .../accept returns seed_prompt and milestone_name."""
    import routers.advisor_service as svc  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415
    from fastapi import FastAPI  # noqa: PLC0415
    from routers.suggestions import router  # noqa: PLC0415

    app = FastAPI()
    app.include_router(router)

    proj = "myproject"
    svc.save_suggestions(proj, [_mk_suggestion(pitch="Ship it", milestone="v4")] * 3)
    rows = svc.get_suggestions(proj)
    sid = rows[0]["id"]

    client = TestClient(app)
    resp = client.post(f"/api/projects/{proj}/advisor/suggestions/{sid}/accept")
    assert resp.status_code == 200
    body = resp.json()
    assert "seed_prompt" in body
    assert "Ship it" in body["seed_prompt"]
    assert body["milestone_name"] == "v4"


def test_accept_404_for_nonexistent_suggestion(fresh_db):
    from fastapi.testclient import TestClient  # noqa: PLC0415
    from fastapi import FastAPI  # noqa: PLC0415
    from routers.suggestions import router  # noqa: PLC0415

    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)
    resp = client.post("/api/projects/myproject/advisor/suggestions/99999/accept")
    assert resp.status_code == 404


def test_dismiss_404_for_nonexistent_suggestion(fresh_db):
    from fastapi.testclient import TestClient  # noqa: PLC0415
    from fastapi import FastAPI  # noqa: PLC0415
    from routers.suggestions import router  # noqa: PLC0415

    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)
    resp = client.post("/api/projects/myproject/advisor/suggestions/99999/dismiss")
    assert resp.status_code == 404


def test_dismissed_list_endpoint_returns_pitches(fresh_db):
    """GET .../dismissed returns the list of dismissed pitches."""
    import routers.advisor_service as svc  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415
    from fastapi import FastAPI  # noqa: PLC0415
    from routers.suggestions import router  # noqa: PLC0415

    app = FastAPI()
    app.include_router(router)

    proj = "myproject"
    svc.save_suggestions(proj, [
        _mk_suggestion(pitch="pitch-alpha"),
        _mk_suggestion(pitch="pitch-beta"),
        _mk_suggestion(pitch="pitch-gamma"),
    ])
    rows = svc.get_suggestions(proj)
    first_id = rows[0]["id"]
    svc.dismiss_suggestion(proj, first_id)

    client = TestClient(app)
    resp = client.get(f"/api/projects/{proj}/advisor/dismissed")
    assert resp.status_code == 200
    body = resp.json()
    assert "dismissed" in body
    assert isinstance(body["dismissed"], list)
    assert any("pitch-alpha" in d for d in body["dismissed"])
