"""Tests for issue #739: Show dual active agents on sprint board in pipeline mode.

Each test class maps to one acceptance criterion. Backend behaviour is exercised
through the /api/sprints/{label}/live endpoint; frontend behaviour is asserted
against the static project.html source (same convention as issue #308/#309).
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "dashboard"))

import server  # noqa: E402

# ── Frontend source for static assertions ─────────────────────────────────────
_HTML_PATH = Path(__file__).parent.parent / "apps" / "dashboard" / "static" / "project.html"
_HTML = _HTML_PATH.read_text()

# ── sprint_manager import for state-persistence assertions ────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))
from services.sprint_manager.sprint_manager import SprintState, IssueState  # noqa: E402


# ── Backend helpers ───────────────────────────────────────────────────────────

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
         patch("server._find_latest_sprint_log", return_value=None), \
         patch("server._is_sprint_running", return_value=True):
        (tmp_path / ".commander" / "sprints").mkdir(parents=True)
        yield TestClient(server.app)


def _post(client, payload):
    r = client.post("/api/sprint-status", json=payload)
    assert r.status_code == 200, r.text


def _live(client, label="sprint-99"):
    r = client.get(f"/api/sprints/{label}/live", params={"project": "zealchaiwut/commander"})
    assert r.status_code == 200, r.text
    return r.json()


# Two tickets, coder on A and tester on B simultaneously (pipeline scenario).
def _dual_pipeline_issues():
    return [
        _issue(100, "Ticket A", status="in-progress", agent_status="coder_running",
               dispatch_level=1, coder_started_at="2026-01-01T00:01:00Z"),
        _issue(101, "Ticket B", status="in-progress", agent_status="tester_running",
               dispatch_level=1,
               coder_started_at="2026-01-01T00:00:10Z",
               coder_finished_at="2026-01-01T00:00:50Z",
               tester_started_at="2026-01-01T00:01:00Z"),
    ]


# ── AC1: two distinct active rows when both agents have tickets ────────────────
class TestAC1TwoActiveAgents:
    def test_live_returns_two_active_agents(self, client):
        _post(client, _status(_dual_pipeline_issues(), pipeline_mode=True))
        data = _live(client)
        assert "active_agents" in data, "AC1: /live must expose active_agents array"
        assert len(data["active_agents"]) == 2, \
            f"AC1: both coder and tester active -> two entries, got {data['active_agents']}"

    def test_board_renders_a_card_per_active_agent(self):
        # The dual-card renderer maps over active_agents (one card each).
        assert "_smgmtActiveAgentsHtml" in _HTML, \
            "AC1: dual active-agent card renderer must exist"
        assert ".active_agents" in _HTML and "smgmt-active-agent" in _HTML, \
            "AC1: renderer must iterate active_agents into smgmt-active-agent cards"


# ── AC2: each card shows correct role label + its ticket ──────────────────────
class TestAC2RoleAndTicket:
    def test_active_agents_carry_role_and_ticket(self, client):
        _post(client, _status(_dual_pipeline_issues(), pipeline_mode=True))
        agents = _live(client)["active_agents"]
        by_name = {a["name"]: a for a in agents}
        assert set(by_name) == {"coder", "tester"}, f"AC2: expected coder+tester, got {by_name}"
        assert by_name["coder"]["ticket"]["number"] == 100, "AC2: coder works Ticket A (#100)"
        assert by_name["tester"]["ticket"]["number"] == 101, "AC2: tester works Ticket B (#101)"
        assert by_name["coder"]["ticket"]["title"] == "Ticket A"
        assert by_name["tester"]["ticket"]["title"] == "Ticket B"

    def test_card_renders_human_role_label(self):
        assert "'Coder'" in _HTML and "'Tester'" in _HTML, \
            "AC2: cards must render human labels Coder / Tester"
        assert "smgmt-active-role" in _HTML and "smgmt-active-ticket" in _HTML, \
            "AC2: card must include a role label and ticket slot"


# ── AC3: each card has its own spinner ────────────────────────────────────────
class TestAC3OwnSpinner:
    def test_spinner_is_per_card_inside_map(self):
        # The spinner class must appear inside the per-agent card template so each
        # rendered card gets its own spinner element (not one shared spinner).
        idx = _HTML.find("function _smgmtActiveAgentsHtml")
        assert idx != -1
        body = _HTML[idx: idx + 1600]
        assert "smgmt-active-spinner" in body, \
            "AC3: each active card must render its own smgmt-active-spinner"

    def test_spinner_style_defined(self):
        assert ".smgmt-active-spinner" in _HTML, "AC3: spinner CSS must be defined"
        assert "smgmt-spin" in _HTML, "AC3: spinner must use the spin animation"


# ── AC4: per-level progress visible (merged/total) ────────────────────────────
class TestAC4LevelProgress:
    def test_live_returns_per_level_merged_total(self, client):
        issues = [
            _issue(1, status="done", agent_status="coder_done", dispatch_level=1),
            _issue(2, status="done", agent_status="coder_done", dispatch_level=1),
            _issue(3, status="done", agent_status="coder_done", dispatch_level=1),
            _issue(4, status="in-progress", agent_status="coder_running", dispatch_level=1),
            _issue(5, status="in-progress", agent_status="tester_running", dispatch_level=1),
            _issue(6, status="pending", dispatch_level=2),
            _issue(7, status="pending", dispatch_level=2),
        ]
        _post(client, _status(issues, pipeline_mode=True))
        levels = _live(client)["levels"]
        l1 = next(l for l in levels if l["level"] == 1)
        assert l1["total"] == 5 and l1["merged"] == 3, \
            f"AC4: Level 1 must report 3/5 merged, got {l1}"

    def test_board_renders_level_progress(self):
        assert "_smgmtLevelsHtml" in _HTML, "AC4: per-level progress renderer must exist"
        assert "merged" in _HTML, "AC4: level row must render a merged count"


# ── AC5: next level shown in a waiting state ──────────────────────────────────
class TestAC5WaitingLevel:
    def test_next_level_marked_waiting(self, client):
        issues = [
            _issue(1, status="done", agent_status="coder_done", dispatch_level=1),
            _issue(2, status="in-progress", agent_status="coder_running", dispatch_level=1),
            _issue(3, status="pending", dispatch_level=2),
        ]
        _post(client, _status(issues, pipeline_mode=True))
        levels = _live(client)["levels"]
        l1 = next(l for l in levels if l["level"] == 1)
        l2 = next(l for l in levels if l["level"] == 2)
        assert l1["state"] == "active", f"AC5: in-flight level is active, got {l1}"
        assert l2["state"] == "waiting", f"AC5: next level must be waiting, got {l2}"

    def test_completed_then_active_then_waiting(self, client):
        issues = [
            _issue(1, status="done", agent_status="coder_done", dispatch_level=1),
            _issue(2, status="in-progress", agent_status="coder_running", dispatch_level=2),
            _issue(3, status="pending", dispatch_level=3),
        ]
        _post(client, _status(issues, pipeline_mode=True))
        levels = {l["level"]: l["state"] for l in _live(client)["levels"]}
        assert levels == {1: "complete", 2: "active", 3: "waiting"}, \
            f"AC5: level states must be complete/active/waiting, got {levels}"

    def test_board_renders_waiting_indicator(self):
        assert "Waiting for Level" in _HTML, \
            "AC5: waiting level must render a 'Waiting for Level N' label"
        assert "smgmt-level--waiting" in _HTML, \
            "AC5: waiting level must carry a distinct state class"


# ── AC6: nav pill reflects dual-agent activity ────────────────────────────────
class TestAC6NavPill:
    def test_pill_reads_active_agents_in_pipeline(self):
        idx = _HTML.find("function _snavRenderPill")
        assert idx != -1
        body = _HTML[idx: idx + 3200]
        assert "active_agents" in body and "pipeline_mode" in body, \
            "AC6: nav pill must consider active_agents + pipeline_mode (no collapse to one label)"


# ── AC7: single-agent (non-pipeline) mode unaffected ──────────────────────────
class TestAC7SingleModeUnaffected:
    def test_non_pipeline_status_has_no_levels_and_not_pipeline(self, client):
        issues = [
            _issue(1, status="in-progress", agent_status="coder_running",
                   coder_started_at="2026-01-01T00:01:00Z"),
        ]
        _post(client, _status(issues, pipeline_mode=False))
        data = _live(client)
        assert data["pipeline_mode"] is False, "AC7: non-pipeline run must report pipeline_mode False"
        assert data["levels"] == [], "AC7: no level structure without dispatch levels"

    def test_dual_ui_gated_behind_pipeline_mode(self):
        for fn in ("_smgmtActiveAgentsHtml", "_smgmtLevelsHtml"):
            idx = _HTML.find("function " + fn)
            assert idx != -1
            body = _HTML[idx: idx + 400]
            assert "pipeline_mode" in body, \
                f"AC7: {fn} must early-return unless pipeline_mode is set (single mode unchanged)"


# ── AC8: idle agent's card removed, remaining stays ───────────────────────────
class TestAC8IdleAgentDropped:
    def test_finished_agent_drops_from_active_agents(self, client):
        issues = [
            # Coder fully done on A (idle), tester still running on B.
            _issue(100, "Ticket A", status="done", agent_status="coder_done",
                   dispatch_level=1,
                   coder_started_at="2026-01-01T00:00:10Z",
                   coder_finished_at="2026-01-01T00:00:50Z",
                   tester_started_at="2026-01-01T00:00:50Z",
                   tester_finished_at="2026-01-01T00:01:30Z"),
            _issue(101, "Ticket B", status="in-progress", agent_status="tester_running",
                   dispatch_level=1,
                   coder_started_at="2026-01-01T00:00:10Z",
                   coder_finished_at="2026-01-01T00:00:50Z",
                   tester_started_at="2026-01-01T00:01:00Z"),
        ]
        _post(client, _status(issues, pipeline_mode=True))
        agents = _live(client)["active_agents"]
        assert len(agents) == 1, f"AC8: idle agent removed -> one remaining, got {agents}"
        assert agents[0]["name"] == "tester", "AC8: remaining agent is the tester on Ticket B"
        assert agents[0]["ticket"]["number"] == 101


# ── pipeline_mode persistence in sprint state (enables board gating) ──────────
class TestPipelineModePersisted:
    def test_sprint_state_round_trips_pipeline_mode(self):
        s = SprintState(sprint_label="sprint-99", sprint_number=99, project="x")
        s.pipeline_mode = True
        s.issues = [IssueState(number=1, title="t")]
        restored = SprintState.from_dict(s.to_dict())
        assert restored.pipeline_mode is True, \
            "pipeline_mode must survive to_dict/from_dict so the board can gate dual UI"

    def test_pipeline_mode_in_serialized_payload(self):
        s = SprintState(sprint_label="sprint-99", sprint_number=99)
        s.pipeline_mode = True
        assert s.to_dict().get("pipeline_mode") is True, \
            "pipeline_mode must be in the posted status payload for the dashboard"
