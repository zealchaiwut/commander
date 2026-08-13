"""Tests for issue #2262 — Definition of Ready: parser, preflight readiness, board gate.

AC1: parse_ticket_spec validates the canonical ticket sections
AC2: Preflight returns a readiness block per ticket
AC3: A board gate honours definition_of_ready_mode with off / warn / block
AC4: A ticket missing acceptance criteria is flagged in warn mode and refused in block mode
AC5: Behavioral tests exercise all three modes
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.sprint_manager.ticket_spec import parse_ticket_spec  # noqa: E402
from services.sprint_manager.definition_of_ready import check_ticket_readiness  # noqa: E402


# ── AC1: parse_ticket_spec validates canonical sections ───────────────────────

def test_ac1_parse_ticket_spec_returns_all_fields():
    """AC1: parse_ticket_spec returns all four canonical fields."""
    body = (
        "## Acceptance Criteria\n"
        "- [ ] Widget renders correctly\n\n"
        "## Design Refs\n"
        "- figma.com/file/abc\n\n"
        "## UAT Test Steps\n"
        "Navigate to /widget. Verify rendering.\n\n"
        "## Out of Scope\n"
        "Mobile styling\n"
    )
    result = parse_ticket_spec(body)
    assert result["acceptance_criteria"] == ["Widget renders correctly"]
    assert result["design_refs"] == ["figma.com/file/abc"]
    assert "Navigate to /widget" in result["test_plan"]
    assert "Mobile styling" in result["out_of_scope"]


def test_ac1_parse_ticket_spec_empty_body_returns_empty_fields():
    """AC1: parse_ticket_spec with empty body returns empty values, never raises."""
    result = parse_ticket_spec("")
    assert result["acceptance_criteria"] == []
    assert result["design_refs"] == []
    assert result["test_plan"] == ""
    assert result["out_of_scope"] == ""


def test_ac1_parse_ticket_spec_no_ac_section():
    """AC1: body with no AC heading → acceptance_criteria is empty list."""
    body = "## Design Refs\n- figma.com/abc\n\n## UAT Test Steps\nDo the thing."
    result = parse_ticket_spec(body)
    assert result["acceptance_criteria"] == []
    assert result["design_refs"] == ["figma.com/abc"]


def test_ac1_parse_ticket_spec_checklist_items_extracted():
    """AC1: checklist lines (- [ ] text, - [x] text) are extracted correctly."""
    body = (
        "## Acceptance Criteria\n"
        "- [ ] First criterion\n"
        "- [x] Second criterion\n"
        "- Plain item\n"
    )
    result = parse_ticket_spec(body)
    assert "First criterion" in result["acceptance_criteria"]
    assert "Second criterion" in result["acceptance_criteria"]
    assert "Plain item" in result["acceptance_criteria"]


# ── AC2: preflight returns readiness block per ticket ─────────────────────────

def _make_issue(number: int, *, has_ac: bool = True, has_design: bool = True,
                has_test_plan: bool = True) -> dict:
    """Build a minimal issue dict for testing."""
    body_parts = []
    if has_ac:
        body_parts.append("## Acceptance Criteria\n- [ ] Do the thing\n")
    if has_design:
        body_parts.append("## Design Refs\n- figma.com/abc\n")
    if has_test_plan:
        body_parts.append("## UAT Test Steps\nNavigate to /x. Verify it works.\n")
    return {
        "number": number,
        "title": f"Issue #{number}",
        "body": "\n".join(body_parts),
        "labels": [],
    }


def _make_preflight_client(tmp_path: Path, issues: list[dict],
                           dor_mode: str = "warn",
                           estimates: dict | None = None):
    """Create a TestClient with the preflight route properly mocked."""
    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv  # noqa: PLC0415

    def fake_root(project: str) -> Path:
        slug = project.split("/")[-1] if "/" in project else project
        p = tmp_path / slug
        p.mkdir(parents=True, exist_ok=True)
        return p

    def fake_resolve_estimate(iss: dict, estimates_dir: Path) -> dict:
        if estimates and iss["number"] in estimates:
            return estimates[iss["number"]]
        return {"estimated": True, "size": "S", "files": []}

    def fake_effective_models(project_root: Path) -> dict:
        return {}

    from fastapi.testclient import TestClient  # noqa: PLC0415

    with (
        patch.object(srv, "_get_sprint_issues", return_value=issues),
        patch("server._project_root_path", side_effect=fake_root),
        patch.object(srv, "_resolve_issue_estimate", side_effect=fake_resolve_estimate),
        patch.object(srv, "_effective_agent_models", side_effect=fake_effective_models),
        patch(
            "services.sprint_manager.settings_repo.get_setting",
            return_value={"definition_of_ready_mode": dor_mode},
        ),
    ):
        client = TestClient(srv.app, raise_server_exceptions=True)
        resp = client.get(
            "/api/sprints/sprint-1/preflight",
            params={"project": "owner/testrepo"},
        )
    return resp


def test_ac2_preflight_returns_readiness_block_warn_mode(tmp_path):
    """AC2: Preflight response includes 'readiness' key in warn mode."""
    resp = _make_preflight_client(tmp_path, [_make_issue(1)], dor_mode="warn")
    assert resp.status_code == 200
    body = resp.json()
    assert "readiness" in body


def test_ac2_readiness_block_has_ready_and_not_ready_keys(tmp_path):
    """AC2: readiness block has 'ready' (list) and 'not_ready' (list of {number, missing})."""
    resp = _make_preflight_client(tmp_path, [_make_issue(1)], dor_mode="warn")
    assert resp.status_code == 200
    body = resp.json()
    readiness = body["readiness"]
    assert "ready" in readiness
    assert "not_ready" in readiness
    assert isinstance(readiness["ready"], list)
    assert isinstance(readiness["not_ready"], list)


def test_ac2_ready_ticket_in_ready_list(tmp_path):
    """AC2: fully-specified ticket appears in readiness.ready list."""
    resp = _make_preflight_client(tmp_path, [_make_issue(42)], dor_mode="warn")
    assert resp.status_code == 200
    body = resp.json()
    assert 42 in body["readiness"]["ready"]
    assert not any(e["number"] == 42 for e in body["readiness"]["not_ready"])


def test_ac2_not_ready_entry_has_number_and_missing(tmp_path):
    """AC2: not_ready entries include {number, missing} structure."""
    issue = _make_issue(7, has_ac=False)
    resp = _make_preflight_client(
        tmp_path, [issue], dor_mode="warn",
        estimates={7: {"estimated": True, "size": "S", "files": []}},
    )
    assert resp.status_code == 200
    not_ready = resp.json()["readiness"]["not_ready"]
    assert len(not_ready) == 1
    entry = not_ready[0]
    assert entry["number"] == 7
    assert isinstance(entry["missing"], list)
    assert len(entry["missing"]) > 0


# ── AC3/AC4: board gate honours dor_mode ─────────────────────────────────────

def test_ac3_off_mode_omits_readiness_block(tmp_path):
    """AC3: definition_of_ready_mode=off → 'readiness' key absent from response."""
    resp = _make_preflight_client(tmp_path, [_make_issue(1)], dor_mode="off")
    assert resp.status_code == 200
    body = resp.json()
    assert "readiness" not in body


def test_ac3_off_mode_ok_is_true_even_with_not_ready_tickets(tmp_path):
    """AC3: mode=off → ok=True even if tickets are not ready."""
    issue = _make_issue(5, has_ac=False)
    resp = _make_preflight_client(
        tmp_path, [issue], dor_mode="off",
        estimates={5: {"estimated": False, "size": None, "files": []}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "readiness" not in body


def test_ac4_warn_mode_not_ready_ticket_in_not_ready_list(tmp_path):
    """AC4: warn mode → ticket missing AC appears in not_ready list."""
    issue = _make_issue(10, has_ac=False)
    resp = _make_preflight_client(
        tmp_path, [issue], dor_mode="warn",
        estimates={10: {"estimated": True, "size": "M", "files": []}},
    )
    assert resp.status_code == 200
    body = resp.json()
    not_ready = body["readiness"]["not_ready"]
    assert any(e["number"] == 10 for e in not_ready)
    ac_flagged = next(e for e in not_ready if e["number"] == 10)
    assert "no_acceptance_criteria" in ac_flagged["missing"]


def test_ac4_warn_mode_ok_stays_true_for_not_ready_ticket(tmp_path):
    """AC4: warn mode flags ticket as not ready but keeps ok=True (advisory only)."""
    issue = _make_issue(11, has_ac=False)
    resp = _make_preflight_client(
        tmp_path, [issue], dor_mode="warn",
        estimates={11: {"estimated": True, "size": "M", "files": []}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True  # warn = advisory, not blocking


def test_ac4_block_mode_refuses_when_ticket_missing_ac(tmp_path):
    """AC4: block mode → ok=False when at least one ticket is missing acceptance criteria."""
    issue = _make_issue(20, has_ac=False)
    resp = _make_preflight_client(
        tmp_path, [issue], dor_mode="block",
        estimates={20: {"estimated": True, "size": "M", "files": []}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False  # block = refuse if any ticket not ready
    assert "readiness" in body
    not_ready = body["readiness"]["not_ready"]
    assert any(e["number"] == 20 for e in not_ready)


def test_ac3_block_mode_includes_readiness_block(tmp_path):
    """AC3: block mode → readiness block is present (same as warn, plus ok=False)."""
    issue = _make_issue(21, has_ac=False)
    resp = _make_preflight_client(
        tmp_path, [issue], dor_mode="block",
        estimates={21: {"estimated": True, "size": "S", "files": []}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "readiness" in body
    assert "ready" in body["readiness"]
    assert "not_ready" in body["readiness"]


def test_ac3_block_mode_ok_true_when_all_tickets_ready(tmp_path):
    """AC3: block mode → ok=True when all tickets are fully specified."""
    resp = _make_preflight_client(tmp_path, [_make_issue(30)], dor_mode="block")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True  # all ready → gate passes
    assert 30 in body["readiness"]["ready"]
    assert body["readiness"]["not_ready"] == []


# ── AC5: dor_mode returned in preflight metadata ─────────────────────────────

def test_ac5_dor_mode_returned_in_response(tmp_path):
    """AC5: preflight response includes dor_mode field reflecting current setting."""
    resp = _make_preflight_client(tmp_path, [], dor_mode="warn")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("dor_mode") == "warn"


def test_ac5_dor_mode_block_returned_in_response(tmp_path):
    """AC5: preflight dor_mode field reflects block setting."""
    resp = _make_preflight_client(tmp_path, [], dor_mode="block")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("dor_mode") == "block"


def test_ac5_all_three_modes_produce_correct_ok_behavior(tmp_path):
    """AC5: all three modes exercised — off/warn=ok True, block=ok False when not-ready."""
    issue = _make_issue(99, has_ac=False)
    estimates = {99: {"estimated": True, "size": "S", "files": []}}

    resp_off = _make_preflight_client(tmp_path, [issue], dor_mode="off", estimates=estimates)
    resp_warn = _make_preflight_client(tmp_path, [issue], dor_mode="warn", estimates=estimates)
    resp_block = _make_preflight_client(tmp_path, [issue], dor_mode="block", estimates=estimates)

    assert resp_off.json()["ok"] is True, "off mode must not block"
    assert resp_warn.json()["ok"] is True, "warn mode must not block"
    assert resp_block.json()["ok"] is False, "block mode must refuse when ticket not ready"
