"""Tests for issue #1089 — Gate startup sweep before flipping live running sprints.

Each test is anchored to an acceptance criterion:

  AC1  Three-condition gate: PID absent AND no live process AND grace elapsed
  AC2  Sweep never writes plan.json directly; uses db.transition_sprint_state
  AC3  Dashboard restart with live manager leaves sprint in running state
  AC4  Dead PID past grace settles to needs_rework via guarded writer
  AC5  Guard rejection is logged; plan.json not force-written
  AC6  Grace window configurable via COMMANDER_SWEEP_GRACE_SECONDS env var
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "apps" / "dashboard"))

from apps.dashboard import server  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sprints_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".commander" / "sprints"
    d.mkdir(parents=True)
    return d


def _write_plan(sprints_dir: Path, label: str, state: str = "running",
                started_at: str | None = None) -> Path:
    data: dict = {"state": state}
    if started_at:
        data["started_at"] = started_at
    path = sprints_dir / f"{label}-plan.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _old_started_at(seconds_ago: int = 120) -> str:
    dt = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    return dt.isoformat()


def _recent_started_at(seconds_ago: int = 5) -> str:
    dt = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    return dt.isoformat()


def _fake_projects(tmp_path: Path) -> tuple[list, str]:
    """Return (projects_list, repo_str) pointing plan files at tmp_path."""
    repo = "test/repo"
    return [{"repo": repo}], repo


def _run_sweep(tmp_path: Path, projects: list, grace: int = 30,
               live_manager_pid: object = None,
               db_sprint_row: dict | None = None,
               transition_result: tuple = (True, None)) -> dict:
    """Run _sweep_plan_json_states with standard mocks. Returns captured calls."""
    calls = {"transition": [], "plan_json_set": []}

    def fake_project_root(repo):
        return tmp_path

    def fake_transition(label, to_state, actor, end_reason=None):
        calls["transition"].append((label, to_state, actor, end_reason))
        return transition_result

    def fake_plan_json_set(project_root, sprint_label, state, **kw):
        calls["plan_json_set"].append((sprint_label, state, kw))

    with (
        patch.object(server, "_project_root_path", side_effect=fake_project_root),
        patch.object(server, "_live_manager_pid", return_value=live_manager_pid),
        patch.object(server, "COMMANDER_SWEEP_GRACE_SECONDS", grace),
        patch("db.get_sprint", return_value=db_sprint_row),
        patch("db.transition_sprint_state", side_effect=fake_transition),
        patch.object(server, "_plan_json_set_state", side_effect=fake_plan_json_set),
    ):
        server._sweep_plan_json_states(projects)

    return calls


# ---------------------------------------------------------------------------
# AC1 — Three-condition gate
# ---------------------------------------------------------------------------

class TestThreeConditionGate:
    """AC1: all three must be true before writing needs_rework."""

    def test_condition1_pid_file_present_skips(self, tmp_path):
        sprints_dir = _make_sprints_dir(tmp_path)
        _write_plan(sprints_dir, "sprint-1", started_at=_old_started_at())
        (sprints_dir / "sprint-1-pid").write_text("12345", encoding="utf-8")
        projects, _ = _fake_projects(tmp_path)

        calls = _run_sweep(tmp_path, projects, grace=5, live_manager_pid=None,
                           db_sprint_row={"started_at": _old_started_at()})
        assert calls["transition"] == [], "PID file present — must not transition"

    def test_condition1_pending_file_present_skips(self, tmp_path):
        sprints_dir = _make_sprints_dir(tmp_path)
        _write_plan(sprints_dir, "sprint-1", started_at=_old_started_at())
        (sprints_dir / "sprint-1-pid.pending").write_text("0", encoding="utf-8")
        projects, _ = _fake_projects(tmp_path)

        calls = _run_sweep(tmp_path, projects, grace=5, live_manager_pid=None,
                           db_sprint_row={"started_at": _old_started_at()})
        assert calls["transition"] == [], "Pending PID file present — must not transition"

    def test_condition2_live_manager_skips(self, tmp_path):
        sprints_dir = _make_sprints_dir(tmp_path)
        _write_plan(sprints_dir, "sprint-1", started_at=_old_started_at())
        projects, _ = _fake_projects(tmp_path)

        calls = _run_sweep(tmp_path, projects, grace=5,
                           live_manager_pid=9999,
                           db_sprint_row={"started_at": _old_started_at()})
        assert calls["transition"] == [], "Live manager PID — must not transition"

    def test_condition3_grace_not_elapsed_skips(self, tmp_path):
        sprints_dir = _make_sprints_dir(tmp_path)
        recent = _recent_started_at(seconds_ago=5)
        _write_plan(sprints_dir, "sprint-1", started_at=recent)
        projects, _ = _fake_projects(tmp_path)

        calls = _run_sweep(tmp_path, projects, grace=60, live_manager_pid=None,
                           db_sprint_row={"started_at": recent})
        assert calls["transition"] == [], "Grace window not elapsed — must not transition"

    def test_condition3_no_started_at_skips(self, tmp_path):
        sprints_dir = _make_sprints_dir(tmp_path)
        _write_plan(sprints_dir, "sprint-1")
        projects, _ = _fake_projects(tmp_path)

        calls = _run_sweep(tmp_path, projects, grace=5, live_manager_pid=None,
                           db_sprint_row=None)
        assert calls["transition"] == [], "No started_at — grace assumed, must not transition"

    def test_all_conditions_met_transitions(self, tmp_path):
        sprints_dir = _make_sprints_dir(tmp_path)
        started = _old_started_at(seconds_ago=120)
        _write_plan(sprints_dir, "sprint-1", started_at=started)
        projects, _ = _fake_projects(tmp_path)

        calls = _run_sweep(tmp_path, projects, grace=30, live_manager_pid=None,
                           db_sprint_row={"started_at": started})
        assert len(calls["transition"]) == 1, "All conditions met — must call transition"
        label, to_state, actor, end_reason = calls["transition"][0]
        assert to_state == "needs_rework"
        assert actor == "reconcile"

    def test_non_running_plan_skipped(self, tmp_path):
        sprints_dir = _make_sprints_dir(tmp_path)
        _write_plan(sprints_dir, "sprint-2", state="completed")
        projects, _ = _fake_projects(tmp_path)

        calls = _run_sweep(tmp_path, projects, grace=5, live_manager_pid=None,
                           db_sprint_row={"started_at": _old_started_at()})
        assert calls["transition"] == [], "Non-running plan must be skipped"


# ---------------------------------------------------------------------------
# AC2 — No direct plan.json write
# ---------------------------------------------------------------------------

class TestNoDirectPlanJsonWrite:
    """AC2: state changes go through db.transition_sprint_state, not raw file writes."""

    def test_transition_called_not_direct_write(self, tmp_path):
        sprints_dir = _make_sprints_dir(tmp_path)
        started = _old_started_at(120)
        plan_file = _write_plan(sprints_dir, "sprint-5", started_at=started)
        original_content = plan_file.read_text()
        projects, _ = _fake_projects(tmp_path)

        calls = _run_sweep(tmp_path, projects, grace=30, live_manager_pid=None,
                           db_sprint_row={"started_at": started},
                           transition_result=(True, None))

        assert calls["transition"], "transition_sprint_state must be called"
        assert calls["plan_json_set"], "_plan_json_set_state must be called after approval"
        data_before_set = json.loads(original_content)
        assert data_before_set.get("state") == "running", \
            "plan.json must not be mutated before transition approval"


# ---------------------------------------------------------------------------
# AC3 — Dashboard restart with genuinely running sprint stays running
# ---------------------------------------------------------------------------

class TestDashboardRestartPreservesRunning:
    """AC3: restarting the dashboard while a sprint is running must not flip state."""

    def test_live_manager_pid_leaves_sprint_running(self, tmp_path):
        sprints_dir = _make_sprints_dir(tmp_path)
        _write_plan(sprints_dir, "sprint-3", started_at=_old_started_at())
        projects, _ = _fake_projects(tmp_path)

        calls = _run_sweep(tmp_path, projects, grace=5, live_manager_pid=11111,
                           db_sprint_row={"started_at": _old_started_at()})
        assert calls["transition"] == [], \
            "Live manager running mid-restart — state must remain running"

    def test_pid_file_present_leaves_sprint_running(self, tmp_path):
        sprints_dir = _make_sprints_dir(tmp_path)
        _write_plan(sprints_dir, "sprint-3", started_at=_old_started_at())
        (sprints_dir / "sprint-3-pid").write_text("42", encoding="utf-8")
        projects, _ = _fake_projects(tmp_path)

        calls = _run_sweep(tmp_path, projects, grace=5, live_manager_pid=None,
                           db_sprint_row={"started_at": _old_started_at()})
        assert calls["transition"] == [], \
            "PID file present on restart — state must remain running"


# ---------------------------------------------------------------------------
# AC4 — Dead PID past grace → needs_rework via guarded writer
# ---------------------------------------------------------------------------

class TestDeadPidPastGrace:
    """AC4: confirmed-dead sprint past grace window settles to needs_rework."""

    def test_dead_pid_past_grace_transitions_to_needs_rework(self, tmp_path):
        sprints_dir = _make_sprints_dir(tmp_path)
        started = _old_started_at(seconds_ago=300)
        _write_plan(sprints_dir, "sprint-10", started_at=started)
        projects, _ = _fake_projects(tmp_path)

        calls = _run_sweep(tmp_path, projects, grace=60, live_manager_pid=None,
                           db_sprint_row={"started_at": started},
                           transition_result=(True, None))

        assert len(calls["transition"]) == 1
        _, to_state, actor, end_reason = calls["transition"][0]
        assert to_state == "needs_rework", "must transition to needs_rework"
        assert actor == "reconcile", "actor must be reconcile"
        assert end_reason == "process lost", "end_reason must be process lost"

    def test_plan_json_also_updated_on_success(self, tmp_path):
        sprints_dir = _make_sprints_dir(tmp_path)
        started = _old_started_at(300)
        _write_plan(sprints_dir, "sprint-10", started_at=started)
        projects, _ = _fake_projects(tmp_path)

        calls = _run_sweep(tmp_path, projects, grace=60, live_manager_pid=None,
                           db_sprint_row={"started_at": started},
                           transition_result=(True, None))

        assert calls["plan_json_set"], "_plan_json_set_state must be called after approval"
        _, state, _ = calls["plan_json_set"][0]
        assert state == "needs_rework"


# ---------------------------------------------------------------------------
# AC5 — Guard rejection: sweep logs, does not force-write
# ---------------------------------------------------------------------------

class TestGuardRejection:
    """AC5: when transition_sprint_state returns (False, reason), no plan.json write."""

    def test_rejected_transition_leaves_plan_json_unchanged(self, tmp_path):
        sprints_dir = _make_sprints_dir(tmp_path)
        started = _old_started_at(300)
        plan_file = _write_plan(sprints_dir, "sprint-7", started_at=started)
        projects, _ = _fake_projects(tmp_path)

        calls = _run_sweep(tmp_path, projects, grace=60, live_manager_pid=None,
                           db_sprint_row={"started_at": started},
                           transition_result=(False, "running→terminal rejected: not authorized"))

        assert calls["plan_json_set"] == [], \
            "Guard rejected — _plan_json_set_state must NOT be called"
        data = json.loads(plan_file.read_text())
        assert data.get("state") == "running", \
            "Guard rejected — plan.json must still show running"

    def test_rejection_does_not_raise(self, tmp_path):
        sprints_dir = _make_sprints_dir(tmp_path)
        started = _old_started_at(300)
        _write_plan(sprints_dir, "sprint-7", started_at=started)
        projects, _ = _fake_projects(tmp_path)

        _run_sweep(tmp_path, projects, grace=60, live_manager_pid=None,
                   db_sprint_row={"started_at": started},
                   transition_result=(False, "rejected"))


# ---------------------------------------------------------------------------
# AC6 — Grace window configurable via env var
# ---------------------------------------------------------------------------

class TestGraceWindowConfigurable:
    """AC6: COMMANDER_SWEEP_GRACE_SECONDS controls the grace window."""

    def test_custom_grace_blocks_recent_sprint(self, tmp_path):
        sprints_dir = _make_sprints_dir(tmp_path)
        started = _old_started_at(seconds_ago=90)
        _write_plan(sprints_dir, "sprint-20", started_at=started)
        projects, _ = _fake_projects(tmp_path)

        calls = _run_sweep(tmp_path, projects, grace=120, live_manager_pid=None,
                           db_sprint_row={"started_at": started})
        assert calls["transition"] == [], \
            "90s sprint within 120s grace window — must not transition"

    def test_custom_grace_allows_old_sprint(self, tmp_path):
        sprints_dir = _make_sprints_dir(tmp_path)
        started = _old_started_at(seconds_ago=200)
        _write_plan(sprints_dir, "sprint-20", started_at=started)
        projects, _ = _fake_projects(tmp_path)

        calls = _run_sweep(tmp_path, projects, grace=120, live_manager_pid=None,
                           db_sprint_row={"started_at": started},
                           transition_result=(True, None))
        assert len(calls["transition"]) == 1, \
            "200s sprint past 120s grace window — must transition"

    def test_server_has_configurable_grace_constant(self):
        assert hasattr(server, "COMMANDER_SWEEP_GRACE_SECONDS"), \
            "server.COMMANDER_SWEEP_GRACE_SECONDS must exist"
        assert isinstance(server.COMMANDER_SWEEP_GRACE_SECONDS, int)
        assert server.COMMANDER_SWEEP_GRACE_SECONDS > 0, "default grace must be positive"
