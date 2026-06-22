"""A no-dispatch run (no tickets dispatched) must leave the sprint runnable, not
needs_rework.

Bug: starting a sprint whose tickets weren't dispatchable (wrong/missing labels,
or a transient gh lag) set the sprint to 'running', found nothing, then marked it
needs_rework with a pointless "Re-run -> N.1" — even though no work happened. The
fix drops the running lifecycle row (reconcile -> deleted) and resets plan state
to draft so the board rebuilds it as a fresh runnable draft.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

import db  # noqa: E402


def test_no_dispatch_reset_drops_running_row_leaving_sprint_runnable():
    proj = "owner/nodispatch-test"
    db.transition_sprint_state("sprint-nd9", "running", actor="manager", project=proj)
    assert db.get_sprint("sprint-nd9", project=proj)["state"] == "running"

    # The fix's mechanism for a no-op run: reconcile -> deleted removes the row.
    res = db.transition_sprint_state("sprint-nd9", "deleted", actor="reconcile", project=proj)
    assert res.accepted is True
    # No lifecycle row -> board rebuilds it as a fresh runnable draft (not needs_rework).
    assert db.get_sprint("sprint-nd9", project=proj) is None
