"""Tests for issue #1427: Serial dispatch must set coder_routing_reason on IssueState.

AC items verified:
  AC1  In serial dispatch, issue_state.coder_routing_reason is assigned _ser_route_reason
       at the same point where issue_state.coder_model is set.
  AC2  After the assignment, the live state dict contains a non-empty coder_routing_reason.
  AC3  coder_routing_reason reaches the running-pane via to_dict() (badge tooltip source).
  AC4  Pipeline dispatch behaviour is unchanged — ist.coder_routing_reason is still set
       in pipeline.py and to_dict() continues to include it.
  AC5  The agent_runs DB row for a serial-dispatched issue still contains the routing_reason
       (passed as routing_reason= to _db_agent_start_sm, unchanged by this fix).
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SM_PATH = REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"
PIPELINE_PATH = REPO_ROOT / "services" / "sprint_manager" / "pipeline.py"


# ── helpers ───────────────────────────────────────────────────────────────────

def _sm_source() -> str:
    return SM_PATH.read_text(encoding="utf-8")


def _pipeline_source() -> str:
    return PIPELINE_PATH.read_text(encoding="utf-8")


# ── AC1: serial dispatch assigns coder_routing_reason ─────────────────────────

def test_serial_dispatch_assigns_coder_routing_reason_in_source():
    """AC1: sprint_manager.py must assign issue_state.coder_routing_reason = _ser_route_reason."""
    src = _sm_source()
    assert "issue_state.coder_routing_reason = _ser_route_reason" in src, (
        "Serial dispatch path must assign issue_state.coder_routing_reason = _ser_route_reason; "
        "it was missing (issue #1427)"
    )


def test_serial_dispatch_routing_reason_adjacent_to_coder_model():
    """AC1: coder_routing_reason assignment must appear near coder_model assignment."""
    src = _sm_source()
    coder_model_idx = src.find("issue_state.coder_model = _ser_coder_model")
    assert coder_model_idx != -1, "issue_state.coder_model assignment not found in sprint_manager.py"

    routing_reason_idx = src.find("issue_state.coder_routing_reason = _ser_route_reason")
    assert routing_reason_idx != -1, (
        "issue_state.coder_routing_reason = _ser_route_reason not found in sprint_manager.py"
    )

    # The routing_reason assignment must appear within 300 characters of coder_model
    # (same dispatch block, back-to-back lines).
    distance = abs(routing_reason_idx - coder_model_idx)
    assert distance < 300, (
        f"coder_routing_reason assignment is {distance} chars from coder_model — "
        "they should be adjacent in the same dispatch block"
    )


# ── AC2: live state dict reflects coder_routing_reason ───────────────────────

def test_issue_state_to_dict_reflects_routing_reason_after_assignment():
    """AC2: after assigning coder_routing_reason, to_dict() includes the non-empty value."""
    import os
    import sys
    sys.path.insert(0, str(REPO_ROOT))
    os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))
    import services.sprint_manager.state as state_mod

    ist = state_mod.IssueState(number=42, title="Test issue")
    ist.coder_routing_reason = "size=M"

    d = ist.to_dict()
    assert d.get("coder_routing_reason") == "size=M", (
        f"to_dict() must reflect coder_routing_reason; got {d.get('coder_routing_reason')!r}"
    )


def test_issue_state_to_dict_routing_reason_non_empty_when_set():
    """AC2: coder_routing_reason in the state dict is non-empty when a reason is assigned."""
    import os
    import sys
    sys.path.insert(0, str(REPO_ROOT))
    os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))
    import services.sprint_manager.state as state_mod

    ist = state_mod.IssueState(number=7, title="Serial ticket")
    ist.coder_routing_reason = "docs-only:flag"

    d = ist.to_dict()
    rr = d.get("coder_routing_reason") or ""
    assert rr, f"coder_routing_reason must be non-empty in live state dict; got {rr!r}"


# ── AC3: badge tooltip receives routing_reason via to_dict ────────────────────

def test_issue_state_to_dict_includes_coder_routing_reason_key():
    """AC3: to_dict() must include the 'coder_routing_reason' key for the frontend badge."""
    import os
    import sys
    sys.path.insert(0, str(REPO_ROOT))
    os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))
    import services.sprint_manager.state as state_mod

    ist = state_mod.IssueState(number=1, title="badge test")
    d = ist.to_dict()
    assert "coder_routing_reason" in d, (
        "to_dict() must contain 'coder_routing_reason' key for the running-pane badge tooltip"
    )


def test_issue_state_to_dict_routing_reason_none_by_default():
    """AC3: coder_routing_reason defaults to None (no tooltip when no override occurred)."""
    import os
    import sys
    sys.path.insert(0, str(REPO_ROOT))
    os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))
    import services.sprint_manager.state as state_mod

    ist = state_mod.IssueState(number=99, title="no routing override")
    d = ist.to_dict()
    assert d["coder_routing_reason"] is None, (
        "coder_routing_reason must be None by default (UAT step 4: no error when no override)"
    )


# ── AC4: pipeline dispatch is unchanged ──────────────────────────────────────

def test_pipeline_still_assigns_coder_routing_reason():
    """AC4: pipeline.py must still set ist.coder_routing_reason = _coder_route_reason."""
    src = _pipeline_source()
    assert "ist.coder_routing_reason = _coder_route_reason" in src, (
        "pipeline.py must still assign ist.coder_routing_reason = _coder_route_reason; "
        "this line must not have been removed or changed"
    )


def test_pipeline_routing_reason_assignment_is_comment_annotated():
    """AC4: the pipeline assignment comment still references issue #1403 (unchanged)."""
    src = _pipeline_source()
    # Find the line with ist.coder_routing_reason in pipeline.py
    for line in src.splitlines():
        if "ist.coder_routing_reason = _coder_route_reason" in line:
            assert "#1403" in line, (
                f"Pipeline coder_routing_reason line must still reference #1403; got: {line!r}"
            )
            break
    else:
        pytest.fail("ist.coder_routing_reason = _coder_route_reason not found in pipeline.py")


# ── AC5: agent_runs DB row still receives routing_reason ─────────────────────

def test_serial_dispatch_db_call_still_passes_routing_reason():
    """AC5: _db_agent_start_sm call in serial dispatch still passes routing_reason=_ser_route_reason."""
    src = _sm_source()
    # Find the _db_agent_start_sm call block in the serial dispatch path
    assert "routing_reason=_ser_route_reason" in src, (
        "_db_agent_start_sm must still receive routing_reason=_ser_route_reason "
        "so the agent_runs DB row continues to capture it"
    )


def test_serial_dispatch_db_call_unchanged_after_fix():
    """AC5: The routing_reason= kwarg in _db_agent_start_sm is adjacent to model_used=_ser_coder_model."""
    src = _sm_source()
    db_call_idx = src.find("routing_reason=_ser_route_reason")
    model_used_idx = src.find("model_used=_ser_coder_model")

    assert db_call_idx != -1, "routing_reason=_ser_route_reason not found in sprint_manager.py"
    assert model_used_idx != -1, "model_used=_ser_coder_model not found in sprint_manager.py"

    # Both must be in the same _db_agent_start_sm() call block (within 200 chars)
    distance = abs(db_call_idx - model_used_idx)
    assert distance < 200, (
        f"routing_reason and model_used are {distance} chars apart — they should be in the same call"
    )
