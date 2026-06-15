"""Sprint-lifecycle redesign P0: block same-label re-dispatch.

Design contract: docs/architecture/sprint-lifecycle.md
Tracking:        docs/milestones/sprint-lifecycle-redesign.md (P0)

One label = one attempt. A sprint label whose plan reached a terminal state
(completed / cancelled) may never be dispatched again via POST /api/sprints/run;
re-runs create a child sub-sprint instead (POST /api/sprints/{label}/rerun).
Re-dispatching the same label is what produced multi-attempt forensics under a
single label (sprint-68.6 ran three times).

AC-1: _reject_terminal_label_redispatch raises 409 for state=completed.
AC-2: ... and for state=cancelled.
AC-3: planning / running / absent plan.json pass through (no raise).
AC-4: the 409 detail points the caller to the Re-run flow.
AC-5: run_sprint_managed wires the guard in before dispatch.
AC-6: board source — has-rework cards with tickets route to the re-run flow,
      never to smgmtRunSprint with the same label; the built bundle is fresh.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(_DASHBOARD_ROOT))

_BOARD_RENDER_SRC = (
    _DASHBOARD_ROOT / "static" / "src" / "sprint-board" / "board-render.js"
).read_text(encoding="utf-8")
_BUNDLE_SRC = (
    _DASHBOARD_ROOT / "static" / "dist" / "bundle.js"
).read_text(encoding="utf-8")
_SERVER_SRC = (_DASHBOARD_ROOT / "server.py").read_text(encoding="utf-8")


def _write_plan(tmp: Path, label: str, state: str | None) -> Path:
    """Create <tmp>/.commander/sprints/<label>-plan.json with the given state."""
    sprints_dir = tmp / ".commander" / "sprints"
    sprints_dir.mkdir(parents=True, exist_ok=True)
    if state is not None:
        (sprints_dir / f"{label}-plan.json").write_text(
            json.dumps({"state": state, "tickets": [1]}), encoding="utf-8"
        )
    return tmp


class TestTerminalStateGuard:
    """AC-1 … AC-4: the guard helper itself."""

    def test_completed_raises_409(self, tmp_path):
        from server import _reject_terminal_label_redispatch
        project = _write_plan(tmp_path, "sprint-68.6", "completed")
        with pytest.raises(HTTPException) as exc:
            _reject_terminal_label_redispatch(project, "sprint-68.6")
        assert exc.value.status_code == 409

    def test_cancelled_raises_409(self, tmp_path):
        from server import _reject_terminal_label_redispatch
        project = _write_plan(tmp_path, "sprint-68.6", "cancelled")
        with pytest.raises(HTTPException) as exc:
            _reject_terminal_label_redispatch(project, "sprint-68.6")
        assert exc.value.status_code == 409

    @pytest.mark.parametrize("state", ["planning", "running", None])
    def test_non_terminal_states_pass(self, tmp_path, state):
        from server import _reject_terminal_label_redispatch
        project = _write_plan(tmp_path, "sprint-70", state)
        # Must not raise — first attempts and freshly created children dispatch.
        _reject_terminal_label_redispatch(project, "sprint-70")

    def test_409_detail_points_to_rerun(self, tmp_path):
        from server import _reject_terminal_label_redispatch
        project = _write_plan(tmp_path, "sprint-68.6", "completed")
        with pytest.raises(HTTPException) as exc:
            _reject_terminal_label_redispatch(project, "sprint-68.6")
        detail = str(exc.value.detail)
        assert "Re-run" in detail
        assert "sprint-68.6" in detail

    def test_phantom_terminal_plan_without_tickets_passes(self, tmp_path):
        """A bare {"state": "completed"} plan.json with no tickets and no
        durable `sprints` row is a phantom (a finish/sweep path stamped it for
        a label that never dispatched anything). It must NOT block a first run —
        regression for sprint-66, whose 8 open tickets could not be dispatched.
        """
        from server import _reject_terminal_label_redispatch
        sprints_dir = tmp_path / ".commander" / "sprints"
        sprints_dir.mkdir(parents=True, exist_ok=True)
        # Phantom: terminal state, no tickets — exactly what was on disk.
        (sprints_dir / "sprint-99999-plan.json").write_text(
            json.dumps({"state": "completed"}), encoding="utf-8"
        )
        # sprint-99999 has no durable row → must pass through (no raise).
        _reject_terminal_label_redispatch(tmp_path, "sprint-99999")

    def test_terminal_states_constant(self):
        # P1 extended the terminal set with the unified-lifecycle endings
        # (ready_to_merge / needs_rework); cancelled stays for legacy files.
        from server import _TERMINAL_PLAN_STATES
        assert _TERMINAL_PLAN_STATES == frozenset(
            {"completed", "ready_to_merge", "needs_rework", "cancelled"}
        )


class TestRunEndpointWiring:
    """AC-5: the managed run endpoint calls the guard before dispatch."""

    def test_run_sprint_managed_calls_guard(self):
        import re
        m = re.search(
            r"def run_sprint_managed\(.*?(?=\n@app\.|\ndef [a-z_]+\()", _SERVER_SRC,
            re.DOTALL,
        )
        assert m, "run_sprint_managed not found in server.py"
        body = m.group(0)
        assert "_reject_terminal_label_redispatch(" in body, (
            "POST /api/sprints/run must reject terminal-state labels (P0: "
            "no same-label re-dispatch)"
        )


class TestBoardRendering:
    """AC-6: board source routes post-run cards to the re-run flow."""

    def test_has_rework_routes_to_rerun(self):
        assert "isHasRework || isPostRun" in _BOARD_RENDER_SRC, (
            "has-rework cards (with or without tickets) must render the "
            "re-run button, not a same-label Run Sprint button"
        )

    def test_canrun_no_longer_special_cases_has_rework(self):
        assert "(!hasCompleted || isHasRework)" not in _BOARD_RENDER_SRC, (
            "canRun must not re-enable Run Sprint for has-rework sprints — "
            "their label is terminal"
        )

    def test_bundle_is_rebuilt(self):
        assert "isHasRework || isPostRun" in _BUNDLE_SRC, (
            "static/dist/bundle.js is stale — run `npm run build`"
        )

    def test_post_run_gated_on_ledger_not_ticket_labels(self):
        assert "sprint_has_run" in _BOARD_RENDER_SRC, (
            "board must read sprint_has_run from the API"
        )
        assert "_smgmtHasLedgerRun" in _BOARD_RENDER_SRC, (
            "board must gate post-run affordances on ledger run state"
        )
        assert "hasLedgerRun &&" in _BOARD_RENDER_SRC, (
            "isHasRework / isReadyToMerge must require a ledger run"
        )
