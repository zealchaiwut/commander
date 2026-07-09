"""
Regression tests for issue #1773: remove last writer of deprecated 'planned'
lifecycle state from _sprint_signoff_set_approved().

AC1 / AC5: _sprint_signoff_set_approved() must not write state="planned" under any
           code path (None or "draft" initial state).
AC2:        The approved-but-not-run sprint gets the sanctioned post-#1686 value
            ("draft"), not the deprecated "planned".
AC3:        Read-tolerant backward-compat is untouched — _RERUN_REUSABLE_PLAN_STATES
            still includes "planned" and canonical_lifecycle("planned") == "draft".
"""

import json
import sys
from pathlib import Path

import pytest

_DASHBOARD_DIR = Path(__file__).parent.parent / "apps" / "dashboard"
sys.path.insert(0, str(_DASHBOARD_DIR))

import server  # noqa: E402
import db  # noqa: E402


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / ".commander" / "sprints").mkdir(parents=True)
    return root


def _read_state(root, label):
    return json.loads(
        server._sprint_plan_path(root, label).read_text(encoding="utf-8")
    ).get("state")


# ── AC5 / AC1: no "planned" write in either branch ───────────────────────────

def test_ac5_none_state_does_not_write_planned(project):
    """Approving a sprint with no prior state must not produce state='planned'."""
    server._sprint_signoff_set_approved(project, "sprint-10", "alice", "2026-07-09T00:00:00+00:00")
    assert _read_state(project, "sprint-10") != "planned"


def test_ac5_draft_state_does_not_write_planned(project):
    """Approving a sprint in 'draft' state must not produce state='planned'."""
    server._plan_json_set_state(project, "sprint-10", "draft", signoff={"state": "pending"})
    server._sprint_signoff_set_approved(project, "sprint-10", "alice", "2026-07-09T00:00:00+00:00")
    assert _read_state(project, "sprint-10") != "planned"


# ── AC2: sanctioned value ("draft") is written ───────────────────────────────

def test_ac2_none_state_writes_draft(project):
    """When initial state is absent, approval sets state to 'draft'."""
    server._sprint_signoff_set_approved(project, "sprint-10", "alice", "2026-07-09T00:00:00+00:00")
    assert _read_state(project, "sprint-10") == "draft"


def test_ac2_draft_state_stays_draft(project):
    """When initial state is already 'draft', approval keeps it as 'draft'."""
    server._plan_json_set_state(project, "sprint-10", "draft", signoff={"state": "pending"})
    server._sprint_signoff_set_approved(project, "sprint-10", "alice", "2026-07-09T00:00:00+00:00")
    assert _read_state(project, "sprint-10") == "draft"


# ── AC3: read-tolerant backward-compat untouched ─────────────────────────────

def test_ac3_rerun_reusable_plan_states_includes_planned():
    """_RERUN_REUSABLE_PLAN_STATES still accepts 'planned' for legacy reads."""
    assert "planned" in server._RERUN_REUSABLE_PLAN_STATES


def test_ac3_canonical_lifecycle_maps_planned_to_draft():
    """db.canonical_lifecycle('planned') returns 'draft' (legacy forward-read)."""
    assert db.canonical_lifecycle("planned") == "draft"
