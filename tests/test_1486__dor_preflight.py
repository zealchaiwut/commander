"""Tests for issue #1486 — Definition-of-Ready check in sprint preflight.

AC1: get_sprint_preflight returns readiness {ready: [...], not_ready: [{number, missing: [...]}]}
AC2: READY = >=1 AC + >=1 design_ref + non-empty test_plan + resolved estimate + not XL
AC3: missing reasons: no_acceptance_criteria, no_design_ref, no_test_plan, unresolved_estimate, unsplit_xl
AC4: derived purely from parse_ticket_spec + existing estimate-resolution logic
AC5: definition_of_ready_mode: off/warn/block; read from settings, returned in preflight metadata
AC6: mode=off => readiness block omitted
AC7: fully-specified ticket classified as ready
AC8: tests cover all-ready, all-not-ready, 5 individual missing reasons, mode-setting behavior
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))

from services.sprint_manager.definition_of_ready import check_ticket_readiness


_FULL_SPEC = {
    "acceptance_criteria": ["Widget must render in blue"],
    "design_refs": ["figma.com/file/abc123"],
    "test_plan": "Navigate to /widget. Verify background color is blue.",
    "out_of_scope": "",
}
_RESOLVED_READY = {"estimated": True, "size": "L", "files": []}
_RESOLVED_UNESTIMATED = {"estimated": False, "size": None, "files": []}
_RESOLVED_XL = {"estimated": True, "size": "XL", "files": []}
_EMPTY_SPEC = {
    "acceptance_criteria": [],
    "design_refs": [],
    "test_plan": "",
    "out_of_scope": "",
}


# ── AC7: fully-specified ticket is ready ──────────────────────────────────────

def test_ac7_fully_specified_is_ready():
    """AC7: All five conditions met -> ready=True, missing=[]."""
    is_ready, missing = check_ticket_readiness(_FULL_SPEC, _RESOLVED_READY)
    assert is_ready is True
    assert missing == []


# ── All-not-ready case (AC8) ──────────────────────────────────────────────────

def test_all_missing_is_not_ready():
    """AC8 all-not-ready: empty spec + unestimated -> ready=False, 4 reasons."""
    is_ready, missing = check_ticket_readiness(_EMPTY_SPEC, _RESOLVED_UNESTIMATED)
    assert is_ready is False
    assert "no_acceptance_criteria" in missing
    assert "no_design_ref" in missing
    assert "no_test_plan" in missing
    assert "unresolved_estimate" in missing
    # unsplit_xl absent because size is None (not resolved to XL)
    assert "unsplit_xl" not in missing


# ── Individual missing-reason tests (AC3 / AC8) ───────────────────────────────

def test_missing_no_acceptance_criteria():
    """AC3/AC8: empty acceptance_criteria -> only 'no_acceptance_criteria' in missing."""
    spec = {**_FULL_SPEC, "acceptance_criteria": []}
    is_ready, missing = check_ticket_readiness(spec, _RESOLVED_READY)
    assert is_ready is False
    assert missing == ["no_acceptance_criteria"]


def test_missing_no_design_ref():
    """AC3/AC8: empty design_refs -> only 'no_design_ref' in missing."""
    spec = {**_FULL_SPEC, "design_refs": []}
    is_ready, missing = check_ticket_readiness(spec, _RESOLVED_READY)
    assert is_ready is False
    assert missing == ["no_design_ref"]


def test_missing_no_test_plan():
    """AC3/AC8: blank test_plan -> only 'no_test_plan' in missing."""
    spec = {**_FULL_SPEC, "test_plan": ""}
    is_ready, missing = check_ticket_readiness(spec, _RESOLVED_READY)
    assert is_ready is False
    assert missing == ["no_test_plan"]


def test_missing_unresolved_estimate():
    """AC3/AC8: estimated=False -> only 'unresolved_estimate' in missing."""
    is_ready, missing = check_ticket_readiness(_FULL_SPEC, _RESOLVED_UNESTIMATED)
    assert is_ready is False
    assert missing == ["unresolved_estimate"]


def test_missing_unsplit_xl():
    """AC3/AC8: size=XL -> only 'unsplit_xl' in missing."""
    is_ready, missing = check_ticket_readiness(_FULL_SPEC, _RESOLVED_XL)
    assert is_ready is False
    assert missing == ["unsplit_xl"]


# ── Mode-setting read behavior (AC5 / AC6) ───────────────────────────────────

def test_preflight_off_mode_omits_readiness():
    """AC6: definition_of_ready_mode=off -> 'readiness' key absent from response."""
    import server as srv

    def _no_issues(project, sprint_label):
        return []

    with (
        patch.object(srv, "_get_sprint_issues", _no_issues),
        patch(
            "services.sprint_manager.settings_repo.get_setting",
            return_value={"definition_of_ready_mode": "off"},
        ),
    ):
        from fastapi.testclient import TestClient
        client = TestClient(srv.app, raise_server_exceptions=False)
        resp = client.get(
            "/api/sprints/sprint-99/preflight",
            params={"project": "testowner/testrepo"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert "readiness" not in body


def test_preflight_warn_mode_includes_readiness():
    """AC5: definition_of_ready_mode=warn -> 'readiness' key present, dor_mode='warn'."""
    import server as srv

    def _no_issues(project, sprint_label):
        return []

    with (
        patch.object(srv, "_get_sprint_issues", _no_issues),
        patch(
            "services.sprint_manager.settings_repo.get_setting",
            return_value={"definition_of_ready_mode": "warn"},
        ),
    ):
        from fastapi.testclient import TestClient
        client = TestClient(srv.app, raise_server_exceptions=False)
        resp = client.get(
            "/api/sprints/sprint-99/preflight",
            params={"project": "testowner/testrepo"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert "readiness" in body
    assert body["readiness"] == {"ready": [], "not_ready": []}
    assert body.get("dor_mode") == "warn"
