"""Tests for issue #777: Dual active-agent cards show a shared/incorrect PID.

Each test class maps to one acceptance criterion.  Backend behaviour is
exercised through the /api/sprints/{label}/live endpoint and via direct
IssueState serialisation assertions.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "dashboard"))

import server  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent))
from services.sprint_manager.state import IssueState  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────

def _issue(
    number,
    title="Ticket",
    status="pending",
    agent_status="queued",
    dispatch_level=0,
    coder_started_at=None,
    coder_finished_at=None,
    tester_started_at=None,
    tester_finished_at=None,
    coder_pid=None,
    tester_pid=None,
):
    return {
        "number": number,
        "title": title,
        "status": status,
        "agent_status": agent_status,
        "dispatch_level": dispatch_level,
        "coder_started_at": coder_started_at,
        "coder_finished_at": coder_finished_at,
        "tester_started_at": tester_started_at,
        "tester_finished_at": tester_finished_at,
        "failure_reason": None,
        "skip_reason": None,
        "tokens_in": 0,
        "tokens_out": 0,
        "coder_pid": coder_pid,
        "tester_pid": tester_pid,
    }


def _status(issues, pipeline_mode=False, label="sprint-99"):
    payload = {
        "project": "zealchaiwut/commander",
        "sprint_label": label,
        "sprint_number": 99,
        "issues": issues,
        "start_timestamp": "2026-01-01T00:00:00Z",
        "total_tokens_in": 0,
        "total_tokens_out": 0,
        "wall_clock_secs": 0.0,
        "token_budget": 0,
        "paused": False,
    }
    if pipeline_mode:
        payload["pipeline_mode"] = True
    return payload


@pytest.fixture
def client(tmp_path):
    with patch("server._project_root_path", return_value=tmp_path), \
         patch("server._commander_dir", return_value=tmp_path / ".commander"), \
         patch("routers.sprint_live._find_latest_sprint_log", return_value=None), \
         patch("server._sprint_pid_alive", return_value=True):
        (tmp_path / ".commander" / "sprints").mkdir(parents=True)
        yield TestClient(server.app)


def _post(client, payload):
    r = client.post("/api/sprint-status", json=payload)
    assert r.status_code == 200, r.text


def _live(client, label="sprint-99"):
    r = client.get(f"/api/sprints/{label}/live",
                   params={"project": "zealchaiwut/commander"})
    assert r.status_code == 200, r.text
    return r.json()


# ── AC1: each card in pipeline mode shows its own (different) PID ─────────────

class TestAC1EachCardOwnPid:
    def test_dual_pipeline_different_pids(self, client):
        issues = [
            _issue(100, "Ticket A", status="in-progress", agent_status="coder_running",
                   dispatch_level=1, coder_started_at="2026-01-01T00:01:00Z",
                   coder_pid=12345),
            _issue(101, "Ticket B", status="in-progress", agent_status="tester_running",
                   dispatch_level=1,
                   coder_started_at="2026-01-01T00:00:10Z",
                   coder_finished_at="2026-01-01T00:00:50Z",
                   tester_started_at="2026-01-01T00:01:00Z",
                   tester_pid=67890),
        ]
        _post(client, _status(issues, pipeline_mode=True))
        agents = _live(client)["active_agents"]
        assert len(agents) == 2, f"AC1: need two active agents, got {agents}"
        by_name = {a["name"]: a for a in agents}
        assert by_name["coder"]["pid"] == 12345, "AC1: coder PID must be 12345"
        assert by_name["tester"]["pid"] == 67890, "AC1: tester PID must be 67890"
        assert by_name["coder"]["pid"] != by_name["tester"]["pid"], \
            "AC1: coder and tester must not share the same PID"


# ── AC2: coder PID from coder's per-issue state, tester PID from tester's ─────

class TestAC2PerIssueSourced:
    def test_coder_pid_from_coder_issue(self, client):
        issues = [
            _issue(200, "Coder Ticket", status="in-progress", agent_status="coder_running",
                   coder_started_at="2026-01-01T00:01:00Z", coder_pid=11111),
        ]
        _post(client, _status(issues, pipeline_mode=False))
        agents = _live(client)["active_agents"]
        assert len(agents) == 1
        assert agents[0]["name"] == "coder"
        assert agents[0]["pid"] == 11111, \
            f"AC2: coder PID must come from coder issue state, got {agents[0]['pid']}"

    def test_tester_pid_from_tester_issue(self, client):
        issues = [
            _issue(201, "Tester Ticket", status="in-progress", agent_status="tester_running",
                   coder_started_at="2026-01-01T00:00:10Z",
                   coder_finished_at="2026-01-01T00:00:50Z",
                   tester_started_at="2026-01-01T00:01:00Z",
                   tester_pid=22222),
        ]
        _post(client, _status(issues, pipeline_mode=False))
        agents = _live(client)["active_agents"]
        assert len(agents) == 1
        assert agents[0]["name"] == "tester"
        assert agents[0]["pid"] == 22222, \
            f"AC2: tester PID must come from tester issue state, got {agents[0]['pid']}"

    def test_pids_are_independent_across_issues(self, client):
        issues = [
            _issue(300, "Coder Ticket", status="in-progress", agent_status="coder_running",
                   dispatch_level=2, coder_started_at="2026-01-01T00:02:00Z",
                   coder_pid=55555),
            _issue(301, "Tester Ticket", status="in-progress", agent_status="tester_running",
                   dispatch_level=1,
                   coder_started_at="2026-01-01T00:00:00Z",
                   coder_finished_at="2026-01-01T00:01:00Z",
                   tester_started_at="2026-01-01T00:01:30Z",
                   tester_pid=66666),
        ]
        _post(client, _status(issues, pipeline_mode=True))
        agents = {a["name"]: a["pid"] for a in _live(client)["active_agents"]}
        assert agents["coder"] == 55555, f"AC2: coder PID must be 55555, got {agents}"
        assert agents["tester"] == 66666, f"AC2: tester PID must be 66666, got {agents}"


# ── AC3: when per-issue PID is absent, card omits PID (pid is None/absent) ────

class TestAC3OmitPidWhenUnattributable:
    def test_coder_card_has_no_pid_when_untracked(self, client):
        issues = [
            _issue(400, "No-PID Coder", status="in-progress", agent_status="coder_running",
                   coder_started_at="2026-01-01T00:01:00Z"),
            # coder_pid intentionally absent / None
        ]
        _post(client, _status(issues, pipeline_mode=False))
        agents = _live(client)["active_agents"]
        assert len(agents) == 1
        assert agents[0]["name"] == "coder"
        assert agents[0].get("pid") is None, \
            f"AC3: coder card must omit/null PID when untracked, got {agents[0].get('pid')}"

    def test_tester_card_has_no_pid_when_untracked(self, client):
        issues = [
            _issue(401, "No-PID Tester", status="in-progress", agent_status="tester_running",
                   coder_started_at="2026-01-01T00:00:00Z",
                   coder_finished_at="2026-01-01T00:00:30Z",
                   tester_started_at="2026-01-01T00:00:30Z"),
            # tester_pid intentionally absent / None
        ]
        _post(client, _status(issues, pipeline_mode=False))
        agents = _live(client)["active_agents"]
        assert len(agents) == 1
        assert agents[0]["name"] == "tester"
        assert agents[0].get("pid") is None, \
            f"AC3: tester card must omit/null PID when untracked, got {agents[0].get('pid')}"

    def test_dual_pipeline_partial_pids(self, client):
        issues = [
            _issue(500, "Coder with PID", status="in-progress", agent_status="coder_running",
                   dispatch_level=1, coder_started_at="2026-01-01T00:01:00Z",
                   coder_pid=99999),
            _issue(501, "Tester no PID", status="in-progress", agent_status="tester_running",
                   dispatch_level=1,
                   coder_started_at="2026-01-01T00:00:00Z",
                   coder_finished_at="2026-01-01T00:00:30Z",
                   tester_started_at="2026-01-01T00:00:30Z"),
            # tester_pid absent
        ]
        _post(client, _status(issues, pipeline_mode=True))
        agents = {a["name"]: a["pid"] for a in _live(client)["active_agents"]}
        assert agents["coder"] == 99999, "AC3: coder PID must be 99999"
        assert agents["tester"] is None, \
            f"AC3: tester card must have null PID when untracked, got {agents['tester']}"


# ── AC4: single-agent mode shows correct PID unchanged ────────────────────────

class TestAC4SingleAgentUnchanged:
    def test_single_coder_shows_per_issue_pid(self, client):
        issues = [
            _issue(600, "Single Coder", status="in-progress", agent_status="coder_running",
                   coder_started_at="2026-01-01T00:01:00Z", coder_pid=77777),
        ]
        _post(client, _status(issues, pipeline_mode=False))
        data = _live(client)
        assert data["pipeline_mode"] is False, "AC4: must be non-pipeline"
        agents = data["active_agents"]
        assert len(agents) == 1, f"AC4: single-agent mode -> one card, got {agents}"
        assert agents[0]["name"] == "coder"
        assert agents[0]["pid"] == 77777, \
            f"AC4: single-agent coder PID must be 77777, got {agents[0]['pid']}"

    def test_single_tester_shows_per_issue_pid(self, client):
        issues = [
            _issue(601, "Single Tester", status="in-progress", agent_status="tester_running",
                   coder_started_at="2026-01-01T00:00:00Z",
                   coder_finished_at="2026-01-01T00:00:30Z",
                   tester_started_at="2026-01-01T00:00:30Z",
                   tester_pid=88888),
        ]
        _post(client, _status(issues, pipeline_mode=False))
        agents = _live(client)["active_agents"]
        assert len(agents) == 1
        assert agents[0]["name"] == "tester"
        assert agents[0]["pid"] == 88888, \
            f"AC4: single-agent tester PID must be 88888, got {agents[0]['pid']}"


# ── AC5: shared agent_pid no longer assigned to both cards ────────────────────

class TestAC5NoBothAssignment:
    def test_coder_and_tester_not_same_pid_when_state_file_has_pid(self, client, tmp_path):
        """Even if the state.json PID file exists with a sprint-manager PID,
        each card must use its own per-issue PID, not share the sprint-manager PID."""
        # Write a sprint-N-pid file so active_agent is populated
        pid_file = tmp_path / ".commander" / "sprints" / "sprint-99-pid"
        pid_file.write_text("9999")

        issues = [
            _issue(700, "Coder Issue", status="in-progress", agent_status="coder_running",
                   dispatch_level=1, coder_started_at="2026-01-01T00:01:00Z",
                   coder_pid=1111),
            _issue(701, "Tester Issue", status="in-progress", agent_status="tester_running",
                   dispatch_level=1,
                   coder_started_at="2026-01-01T00:00:00Z",
                   coder_finished_at="2026-01-01T00:00:30Z",
                   tester_started_at="2026-01-01T00:00:30Z",
                   tester_pid=2222),
        ]
        _post(client, _status(issues, pipeline_mode=True))
        agents = {a["name"]: a["pid"] for a in _live(client)["active_agents"]}
        # Must NOT both show 9999 (the sprint-manager PID)
        assert agents["coder"] != 9999, \
            "AC5: coder card must not show the sprint-manager PID"
        assert agents["tester"] != 9999, \
            "AC5: tester card must not show the sprint-manager PID"
        assert agents["coder"] == 1111, "AC5: coder must show its own per-issue PID"
        assert agents["tester"] == 2222, "AC5: tester must show its own per-issue PID"


# ── IssueState serialization: coder_pid / tester_pid round-trip ───────────────

class TestIssueStatePidSerialization:
    def test_coder_pid_in_to_dict(self):
        ist = IssueState(number=1, title="t")
        ist.coder_pid = 12345
        d = ist.to_dict()
        assert d.get("coder_pid") == 12345, \
            f"coder_pid must be in to_dict(), got {d}"

    def test_tester_pid_in_to_dict(self):
        ist = IssueState(number=1, title="t")
        ist.tester_pid = 67890
        d = ist.to_dict()
        assert d.get("tester_pid") == 67890, \
            f"tester_pid must be in to_dict(), got {d}"

    def test_pid_round_trip(self):
        ist = IssueState(number=1, title="t")
        ist.coder_pid = 11111
        ist.tester_pid = 22222
        restored = IssueState.from_dict(ist.to_dict())
        assert restored.coder_pid == 11111, \
            f"coder_pid must survive to_dict/from_dict, got {restored.coder_pid}"
        assert restored.tester_pid == 22222, \
            f"tester_pid must survive to_dict/from_dict, got {restored.tester_pid}"

    def test_pid_defaults_to_none(self):
        ist = IssueState(number=1, title="t")
        assert ist.coder_pid is None, "coder_pid must default to None"
        assert ist.tester_pid is None, "tester_pid must default to None"

    def test_pid_survives_missing_in_dict(self):
        d = {"number": 1, "title": "t"}  # no coder_pid / tester_pid
        ist = IssueState.from_dict(d)
        assert ist.coder_pid is None
        assert ist.tester_pid is None
