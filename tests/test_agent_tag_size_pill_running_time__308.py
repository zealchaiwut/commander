"""Tests for issue #308: Add agent tag, size pill, and running time to sprint card."""
import re
import sys
from pathlib import Path
from unittest.mock import patch
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "dashboard"))

from fastapi.testclient import TestClient

# ── HTML source for frontend assertions ──────────────────────────────────────

_HTML_PATH = Path(__file__).parent.parent / "apps" / "dashboard" / "static" / "project.html"
_HTML = _HTML_PATH.read_text()


# ── Backend test helpers ──────────────────────────────────────────────────────

def _issue(
    number: int,
    title: str = "Test ticket",
    status: str = "pending",
    agent_status: str = "queued",
    coder_started_at: str | None = None,
    tester_finished_at: str | None = None,
) -> dict:
    return {
        "number": number,
        "title": title,
        "status": status,
        "agent_status": agent_status,
        "coder_started_at": coder_started_at,
        "coder_finished_at": None,
        "tester_started_at": None,
        "tester_finished_at": tester_finished_at,
        "failure_reason": None,
        "skip_reason": None,
        "tokens_in": 0,
        "tokens_out": 0,
    }


def _sprint_status(issues: list[dict], estimates: dict | None = None) -> dict:
    payload: dict = {
        "project": "zealchaiwut/commander",
        "sprint_label": "sprint-99",
        "sprint_number": 99,
        "issues": issues,
        "start_timestamp": "2026-01-01T00:00:00Z",
        "total_tokens_in": 0,
        "total_tokens_out": 0,
        "wall_clock_secs": 0.0,
        "token_budget": 0,
        "paused": False,
    }
    if estimates is not None:
        payload["estimates"] = estimates
    return payload


@pytest.fixture
def client(tmp_path):
    import server

    with patch("server._project_root_path", return_value=tmp_path), \
         patch("server._commander_dir", return_value=tmp_path / ".commander"), \
         patch("server._find_latest_sprint_log", return_value=None), \
         patch("server._is_sprint_running", return_value=True):
        (tmp_path / ".commander" / "sprints").mkdir(parents=True)
        yield TestClient(server.app)


def _post_status(client, payload: dict):
    resp = client.post("/api/sprint-status", json=payload)
    assert resp.status_code == 200, resp.text


def _get_live(client, label: str = "sprint-99") -> dict:
    resp = client.get(f"/api/sprints/{label}/live", params={"project": "zealchaiwut/commander"})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── AC-1: agent field in live.issues ─────────────────────────────────────────

class TestAC1AgentField:
    """agent field is present in live.issues[] entries."""

    def test_coder_running_sets_agent_to_coder(self, client):
        _post_status(client, _sprint_status([
            _issue(1, agent_status="coder_running"),
        ]))
        live = _get_live(client)
        iss = next(i for i in live["issues"] if i["number"] == 1)
        assert iss["agent"] == "coder"

    def test_tester_running_sets_agent_to_tester(self, client):
        _post_status(client, _sprint_status([
            _issue(2, agent_status="tester_running"),
        ]))
        live = _get_live(client)
        iss = next(i for i in live["issues"] if i["number"] == 2)
        assert iss["agent"] == "tester"

    def test_coder_dispatched_sets_agent_to_coder(self, client):
        _post_status(client, _sprint_status([
            _issue(3, agent_status="coder_dispatched"),
        ]))
        live = _get_live(client)
        iss = next(i for i in live["issues"] if i["number"] == 3)
        assert iss["agent"] == "coder"

    def test_tester_dispatched_sets_agent_to_tester(self, client):
        _post_status(client, _sprint_status([
            _issue(4, agent_status="tester_dispatched"),
        ]))
        live = _get_live(client)
        iss = next(i for i in live["issues"] if i["number"] == 4)
        assert iss["agent"] == "tester"


# ── AC-2: agent null for pending/done tickets ─────────────────────────────────

class TestAC2AgentNullForNonActive:
    """Agent field is null for pending and done tickets."""

    def test_queued_ticket_has_null_agent(self, client):
        _post_status(client, _sprint_status([
            _issue(10, agent_status="queued", status="pending"),
        ]))
        live = _get_live(client)
        iss = live["issues"][0]
        assert iss["agent"] is None

    def test_completed_ticket_has_null_agent(self, client):
        _post_status(client, _sprint_status([
            _issue(11, agent_status="completed", status="done"),
        ]))
        live = _get_live(client)
        iss = live["issues"][0]
        assert iss["agent"] is None

    def test_failed_ticket_has_null_agent(self, client):
        _post_status(client, _sprint_status([
            _issue(12, agent_status="failed", status="skipped"),
        ]))
        live = _get_live(client)
        iss = live["issues"][0]
        assert iss["agent"] is None

    def test_mixed_tickets_only_in_flight_have_agent(self, client):
        _post_status(client, _sprint_status([
            _issue(20, agent_status="queued",       status="pending"),
            _issue(21, agent_status="coder_running", status="pending"),
            _issue(22, agent_status="completed",    status="done"),
        ]))
        live = _get_live(client)
        by_num = {i["number"]: i for i in live["issues"]}
        assert by_num[20]["agent"] is None
        assert by_num[21]["agent"] == "coder"
        assert by_num[22]["agent"] is None


# ── AC-3: size field from estimates, null if absent ──────────────────────────

class TestAC3SizeField:
    """size pill shows S/M/L/XL from estimates; null when no estimate."""

    def test_size_from_estimate_appears_in_live_issues(self, client):
        _post_status(client, _sprint_status(
            [_issue(30, agent_status="coder_running")],
            estimates={"30": {"size": "M", "minutes": 15}},
        ))
        live = _get_live(client)
        iss = live["issues"][0]
        assert iss["size"] == "M"

    def test_all_valid_sizes_pass_through(self, client):
        tickets = [_issue(n) for n in (40, 41, 42, 43)]
        estimates = {
            "40": {"size": "S", "minutes": 7},
            "41": {"size": "M", "minutes": 15},
            "42": {"size": "L", "minutes": 25},
            "43": {"size": "XL", "minutes": 40},
        }
        _post_status(client, _sprint_status(tickets, estimates=estimates))
        live = _get_live(client)
        by_num = {i["number"]: i for i in live["issues"]}
        assert by_num[40]["size"] == "S"
        assert by_num[41]["size"] == "M"
        assert by_num[42]["size"] == "L"
        assert by_num[43]["size"] == "XL"

    def test_no_estimate_gives_null_size(self, client):
        _post_status(client, _sprint_status([_issue(50)]))
        live = _get_live(client)
        iss = live["issues"][0]
        assert iss["size"] is None

    def test_size_null_with_empty_estimates(self, client):
        _post_status(client, _sprint_status([_issue(51)], estimates={}))
        live = _get_live(client)
        iss = live["issues"][0]
        assert iss["size"] is None


# ── AC-4: elapsed_secs computed from coder_started_at ────────────────────────

class TestAC4ElapsedSecs:
    """elapsed_secs is computed from coder_started_at when available."""

    def test_elapsed_computed_from_coder_started_at(self, client):
        _post_status(client, _sprint_status([
            _issue(60, coder_started_at="2026-01-01T00:00:00Z",
                   tester_finished_at="2026-01-01T00:02:13Z"),
        ]))
        live = _get_live(client)
        iss = live["issues"][0]
        # 2m 13s = 133 seconds
        assert iss["elapsed_secs"] == 133

    def test_elapsed_present_for_in_flight_ticket(self, client):
        # coder_started_at set but no tester_finished_at → uses "now" so elapsed > 0
        _post_status(client, _sprint_status([
            _issue(61, agent_status="coder_running",
                   coder_started_at="2026-01-01T00:00:00Z"),
        ]))
        live = _get_live(client)
        iss = live["issues"][0]
        assert iss["elapsed_secs"] is not None
        assert iss["elapsed_secs"] >= 0


# ── AC-5: elapsed_secs null for tickets not yet started ───────────────────────

class TestAC5ElapsedNullForUnstarted:
    """Tickets with no coder_started_at have null elapsed_secs."""

    def test_queued_ticket_has_null_elapsed(self, client):
        _post_status(client, _sprint_status([_issue(70)]))
        live = _get_live(client)
        iss = live["issues"][0]
        assert iss["elapsed_secs"] is None

    def test_pending_ticket_without_start_has_null_elapsed(self, client):
        _post_status(client, _sprint_status([
            _issue(71, status="pending", agent_status="queued", coder_started_at=None),
        ]))
        live = _get_live(client)
        iss = live["issues"][0]
        assert iss["elapsed_secs"] is None


# ── AC-6: SIZE_MINUTES constant in JS ────────────────────────────────────────

class TestAC6SizeMinutesConstant:
    """SIZE_MINUTES constant is defined as a placeholder near running-card code."""

    def test_size_minutes_constant_defined(self):
        assert "const SIZE_MINUTES" in _HTML, "SIZE_MINUTES constant not found in project.html"

    def test_size_minutes_has_correct_values(self):
        m = re.search(r"const SIZE_MINUTES\s*=\s*\{([^}]+)\}", _HTML)
        assert m, "SIZE_MINUTES constant definition not found"
        body = m.group(1)
        assert "S:" in body or "S :" in body
        assert "M:" in body or "M :" in body
        assert "L:" in body or "L :" in body
        assert "XL:" in body or "XL :" in body
        assert "7" in body
        assert "15" in body
        assert "25" in body
        assert "40" in body

    def test_size_minutes_has_placeholder_comment(self):
        # The comment must appear near the SIZE_MINUTES constant
        idx = _HTML.find("const SIZE_MINUTES")
        assert idx != -1, "SIZE_MINUTES not found"
        surrounding = _HTML[max(0, idx - 200): idx + 300]
        assert "placeholder" in surrounding.lower() or "configurable" in surrounding.lower(), \
            "No placeholder comment found near SIZE_MINUTES"

    def test_size_minutes_not_used_in_display_logic(self):
        # SIZE_MINUTES must not appear in render/display functions for this ticket
        # It may appear in the const definition and comment, but not in dynamic HTML generation
        uses = [m.start() for m in re.finditer(r"SIZE_MINUTES", _HTML)]
        # Should be only the definition and possibly a comment reference
        assert len(uses) >= 1, "SIZE_MINUTES not defined"
        for pos in uses:
            ctx = _HTML[max(0, pos - 50): pos + 80]
            assert "const SIZE_MINUTES" in ctx or "//" in ctx or "/*" in ctx or "*" in ctx, \
                f"SIZE_MINUTES appears to be used in display logic at position {pos}: {ctx!r}"


# ── AC-7: CSS styling reuses smgmt-running tokens ─────────────────────────────

class TestAC7Styling:
    """Agent tag and size pill use existing CSS tokens; purple/amber treatment."""

    def test_coder_class_uses_purple_tokens(self):
        assert ".smgmt-ticket-agent-tag.coder-class" in _HTML
        m = re.search(r"\.smgmt-ticket-agent-tag\.coder-class\s*\{([^}]+)\}", _HTML)
        assert m, "coder-class CSS rule not found"
        rule = m.group(1)
        assert "purple" in rule, f"coder-class does not use purple token: {rule}"

    def test_tester_class_uses_amber_tokens(self):
        assert ".smgmt-ticket-agent-tag.tester-class" in _HTML
        m = re.search(r"\.smgmt-ticket-agent-tag\.tester-class\s*\{([^}]+)\}", _HTML)
        assert m, "tester-class CSS rule not found"
        rule = m.group(1)
        assert "amber" in rule, f"tester-class does not use amber token: {rule}"

    def test_unknown_class_fallback_exists(self):
        assert "unknown-class" in _HTML, "unknown-class fallback not defined"

    def test_size_pill_css_exists(self):
        assert ".smgmt-ticket-size-pill" in _HTML

    def test_agent_tag_css_exists(self):
        assert ".smgmt-ticket-agent-tag" in _HTML


# ── AC-8: Row layout order ────────────────────────────────────────────────────

class TestAC8RowLayout:
    """Row order: ticket num → title → size pill → agent tag → elapsed."""

    def test_row_order_in_initial_render(self):
        # Find the template row in _smgmtRunningCardHtml
        m = re.search(
            r'smgmt-ticket-num.*?smgmt-ticket-title.*?sizePillHtml.*?agentTagHtml.*?elapsedHtml',
            _HTML, re.DOTALL
        )
        assert m, "Row layout order (num→title→size→agent→elapsed) not found in initial render"

    def test_row_order_in_live_patch_init(self):
        # _smgmtLivePatch uses DOM insertion: title.after(sizePill), sizePill.after(agentTag),
        # row.appendChild(elapsed).  Verify these patterns exist in the function.
        patch_section = _HTML[_HTML.find("function _smgmtLivePatch"):]
        # Size pill is inserted after title element
        assert "titleEl.after(sizePillEl)" in patch_section or \
               "titleEl).after" in patch_section or \
               "titleEl.after" in patch_section, \
            "size pill is not inserted after title element in _smgmtLivePatch"
        # Agent tag is inserted after size pill (or after title if no size pill)
        assert "sizeEl.after(agentTagEl)" in patch_section or \
               "sizeEl).after" in patch_section or \
               "sizeEl.after" in patch_section, \
            "agent tag is not inserted after size pill in _smgmtLivePatch"
        # Elapsed is appended to the row (last element)
        assert "row.appendChild(elapsedEl)" in patch_section or \
               "appendChild(elapsedEl)" in patch_section, \
            "elapsed element is not appended to row in _smgmtLivePatch"


# ── AC-9: No backend changes for novel role strings ───────────────────────────

class TestAC9ArbitraryRoleString:
    """_smgmtAgentTagClass handles novel role strings gracefully."""

    def test_agent_tag_class_function_handles_unknown_roles(self):
        # The function must return unknown-class for strings not containing CODER or TESTER
        m = re.search(r"function _smgmtAgentTagClass\(role\)\s*\{([^}]+)\}", _HTML, re.DOTALL)
        assert m, "_smgmtAgentTagClass function not found"
        body = m.group(1)
        assert "unknown-class" in body, "No fallback to unknown-class for novel roles"
        assert "CODER" in body
        assert "TESTER" in body

    def test_agent_rendered_uppercase_in_html(self):
        # The template calls .toUpperCase() on agent value
        assert "toUpperCase()" in _HTML or ".toUpperCase()" in _HTML

    def test_agent_tag_rendered_without_hardcoded_role_list(self):
        # The renderring code must use the role string from snapshot, not a fixed if/else
        # agent tag html uses liveIss.agent directly (not a switch/if checking fixed values)
        assert "liveIss.agent" in _HTML, "Agent tag not sourced from liveIss.agent"


# ── Integration: full live snapshot contains all issue-308 fields ─────────────

class TestIntegration:
    """End-to-end: /live issues[] has agent, size, elapsed_secs for all tickets."""

    def test_full_snapshot_all_fields_present(self, client):
        _post_status(client, _sprint_status(
            [
                _issue(100, title="Pending",    agent_status="queued"),
                _issue(101, title="In flight",  agent_status="coder_running",
                       coder_started_at="2026-01-01T00:00:00Z"),
                _issue(102, title="Testing",    agent_status="tester_running"),
                _issue(103, title="Done",       agent_status="completed", status="done"),
            ],
            estimates={
                "101": {"size": "S", "minutes": 7},
                "102": {"size": "XL", "minutes": 40},
            },
        ))
        live = _get_live(client)
        by_num = {i["number"]: i for i in live["issues"]}

        # All issues have the three new fields
        for num in (100, 101, 102, 103):
            assert "agent" in by_num[num]
            assert "size" in by_num[num]
            assert "elapsed_secs" in by_num[num]

        # AC-1/AC-2: agent only on in-flight tickets
        assert by_num[100]["agent"] is None
        assert by_num[101]["agent"] == "coder"
        assert by_num[102]["agent"] == "tester"
        assert by_num[103]["agent"] is None

        # AC-3: size from estimates, null when absent
        assert by_num[100]["size"] is None
        assert by_num[101]["size"] == "S"
        assert by_num[102]["size"] == "XL"
        assert by_num[103]["size"] is None

        # AC-5: elapsed null for un-started tickets
        assert by_num[100]["elapsed_secs"] is None
