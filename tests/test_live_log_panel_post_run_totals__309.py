"""Tests for issue #309 — Add live log panel and post-run totals to running sprint card.

AC-1  Live log panel rendered inside/below running sprint card, shows recent_log_lines.
AC-2  Log panel refreshes every 5s via a separate _smgmtLogPollId timer.
AC-3  Each log line shows timestamp and message in monospace font via smgmt-log-* classes.
AC-4  Log panel is scrollable (max-height 200px, overflow-y auto, auto-scrolls to newest).
AC-5  Log panel reuses smgmt-log-* CSS classes from archived-log styling.
AC-6  Log panel header includes a "live" streaming indicator element.
AC-7  When active_agent and pid are in /live response, shown in log header.
AC-8  When sprint finishes, live log panel is removed during outcome band injection.
AC-9  Running → outcome transition preserves all tickets (no disappearance).
AC-10 Implementation uses polling (recent_log_lines), not SSE /live/stream.
"""

import re
import sys
import json
import pytest
from pathlib import Path
from unittest.mock import patch

# ── path setup ────────────────────────────────────────────────────────────────

REPO_ROOT    = Path(__file__).parent.parent
DASHBOARD    = REPO_ROOT / "apps" / "dashboard"
PROJECT_HTML = DASHBOARD / "static" / "project.html"

sys.path.insert(0, str(DASHBOARD))

_HTML = PROJECT_HTML.read_text(encoding="utf-8")


# ── helpers ───────────────────────────────────────────────────────────────────

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


def _sprint_status(issues: list[dict]) -> dict:
    return {
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


@pytest.fixture
def client(tmp_path):
    """FastAPI test client with mocked filesystem."""
    import server

    with patch("server._project_root_path", return_value=tmp_path), \
         patch("server._commander_dir", return_value=tmp_path / ".commander"), \
         patch("server._find_latest_sprint_log", return_value=None), \
         patch("server._is_sprint_running", return_value=True):
        (tmp_path / ".commander" / "sprints").mkdir(parents=True)
        from fastapi.testclient import TestClient
        yield TestClient(server.app)


def _post_status(client, payload: dict):
    resp = client.post("/api/sprint-status", json=payload)
    assert resp.status_code == 200, resp.text


def _get_live(client, label: str = "sprint-99") -> dict:
    resp = client.get(f"/api/sprints/{label}/live", params={"project": "zealchaiwut/commander"})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ═════════════════════════════════════════════════════════════════════════════
# AC-1  /live returns recent_log_lines; log panel HTML present in template
# ═════════════════════════════════════════════════════════════════════════════

class TestAC1LiveLogPanelRendered:
    """Log panel HTML is present in the running card template; /live exposes recent_log_lines."""

    def test_live_endpoint_returns_recent_log_lines_key(self, client):
        _post_status(client, _sprint_status([_issue(1)]))
        data = _get_live(client)
        assert "recent_log_lines" in data, (
            "FAIL AC-1: /live response does not contain 'recent_log_lines' key"
        )

    def test_recent_log_lines_is_list(self, client):
        _post_status(client, _sprint_status([_issue(1)]))
        data = _get_live(client)
        assert isinstance(data["recent_log_lines"], list), (
            "FAIL AC-1: recent_log_lines must be a list"
        )

    def test_recent_log_lines_empty_when_no_log_file(self, client):
        _post_status(client, _sprint_status([_issue(1)]))
        data = _get_live(client)
        assert data["recent_log_lines"] == [], (
            "FAIL AC-1: recent_log_lines should be [] when no log file exists"
        )

    def test_live_log_panel_div_in_running_card_template(self):
        assert 'smgmt-live-log' in _HTML, (
            "FAIL AC-1: .smgmt-live-log container not found in project.html"
        )

    def test_live_log_panel_id_template_in_running_card(self):
        assert 'id="smgmt-live-log-' in _HTML, (
            "FAIL AC-1: smgmt-live-log-{label} id not found in running card template"
        )

    def test_live_log_stream_id_template_in_running_card(self):
        assert 'id="smgmt-live-log-stream-' in _HTML, (
            "FAIL AC-1: smgmt-live-log-stream-{label} id not found in running card template"
        )

    def test_recent_log_lines_referenced_in_js(self):
        assert "recent_log_lines" in _HTML, (
            "FAIL AC-1: 'recent_log_lines' not referenced in project.html JS"
        )

    def test_waiting_for_log_placeholder_when_empty(self):
        assert "Waiting for log" in _HTML, (
            "FAIL AC-1: placeholder 'Waiting for log' not found in _smgmtLiveLogLinesHtml"
        )

    def test_live_log_rendered_below_ticket_rows(self):
        m = re.search(r'smgmt-sprint-tickets.*?smgmt-live-log', _HTML, re.DOTALL)
        assert m, (
            "FAIL AC-1: smgmt-live-log panel is not placed after smgmt-sprint-tickets in template"
        )


# ═════════════════════════════════════════════════════════════════════════════
# AC-2  Separate 5s poll timer for log panel; 2s indicator poll unchanged
# ═════════════════════════════════════════════════════════════════════════════

class TestAC2FiveSecondPollTimer:
    """A separate _smgmtLogPollId timer fires every 5s for the log panel."""

    def test_log_poll_id_variable_declared(self):
        assert "_smgmtLogPollId" in _HTML, (
            "FAIL AC-2: _smgmtLogPollId variable not declared in project.html"
        )

    def test_log_poll_uses_5000ms_interval(self):
        assert "setInterval(_smgmtLogPollTick, 5000)" in _HTML, (
            "FAIL AC-2: setInterval(_smgmtLogPollTick, 5000) not found — log panel must poll every 5s"
        )

    def test_live_indicator_poll_still_uses_2000ms(self):
        assert "setInterval(_smgmtLivePollTick, 2000)" in _HTML, (
            "FAIL AC-2: the existing 2s poll setInterval(_smgmtLivePollTick, 2000) must remain intact"
        )

    def test_log_poll_tick_function_exists(self):
        assert "function _smgmtLogPollTick" in _HTML, (
            "FAIL AC-2: _smgmtLogPollTick function not found in project.html"
        )

    def test_log_poll_id_cleared_on_tab_switch(self):
        switch_fn = _HTML[_HTML.find("function switchTab"):]
        # Locate the portion that clears timers
        assert "_smgmtLogPollId" in switch_fn, (
            "FAIL AC-2: _smgmtLogPollId is not cleared in switchTab — timers will leak across tabs"
        )

    def test_log_poll_id_cleared_in_live_poll_restart(self):
        restart_fn = _HTML[_HTML.find("function _smgmtLivePollRestart"):]
        assert "_smgmtLogPollId" in restart_fn, (
            "FAIL AC-2: _smgmtLogPollId not cleared/set in _smgmtLivePollRestart"
        )


# ═════════════════════════════════════════════════════════════════════════════
# AC-3  Monospace font, timestamp + message on each log line
# ═════════════════════════════════════════════════════════════════════════════

class TestAC3LogLineFormat:
    """Each log line shows timestamp and message; log panel uses monospace font."""

    def test_log_stream_uses_monospace_font_via_css(self):
        m = re.search(r'\.smgmt-live-log-stream\s*\{([^}]+)\}', _HTML, re.DOTALL)
        assert m, "FAIL AC-3: .smgmt-live-log-stream CSS rule not found"
        rule = m.group(1)
        assert "mono" in rule, (
            f"FAIL AC-3: .smgmt-live-log-stream does not use monospace font (var(--mono)): {rule}"
        )

    def test_log_line_uses_smgmt_log_line_class(self):
        log_lines_fn = _HTML[_HTML.find("function _smgmtLiveLogLinesHtml"):]
        assert "smgmt-log-line" in log_lines_fn, (
            "FAIL AC-3: smgmt-log-line class not used in _smgmtLiveLogLinesHtml"
        )

    def test_log_line_renders_timestamp_span(self):
        log_lines_fn = _HTML[_HTML.find("function _smgmtLiveLogLinesHtml"):]
        assert "smgmt-log-time" in log_lines_fn, (
            "FAIL AC-3: smgmt-log-time span not rendered in _smgmtLiveLogLinesHtml"
        )

    def test_log_line_renders_message_span(self):
        log_lines_fn = _HTML[_HTML.find("function _smgmtLiveLogLinesHtml"):]
        assert "smgmt-log-msg" in log_lines_fn, (
            "FAIL AC-3: smgmt-log-msg span not rendered in _smgmtLiveLogLinesHtml"
        )

    def test_parse_log_lines_returns_timestamp_and_message(self, client, tmp_path):
        import server
        lines = ["→ Dispatching coder for #99", "✓ #99 promoted to UAT"]
        result = server._parse_log_lines_for_live(lines)
        assert len(result) == 2
        for entry in result:
            assert "timestamp" in entry, "FAIL AC-3: log entry missing 'timestamp' field"
            assert "message"   in entry, "FAIL AC-3: log entry missing 'message' field"

    def test_parse_log_lines_timestamp_is_string(self, client, tmp_path):
        import server
        result = server._parse_log_lines_for_live(["some log line"])
        assert isinstance(result[0]["timestamp"], str), (
            "FAIL AC-3: timestamp must be a string"
        )

    def test_parse_log_lines_message_matches_raw_line(self, client, tmp_path):
        import server
        result = server._parse_log_lines_for_live(["Hello world log"])
        assert "Hello world log" in result[0]["message"], (
            "FAIL AC-3: log entry message does not contain original line text"
        )

    def test_parse_log_lines_extracts_bracketed_timestamp(self, client):
        import server
        result = server._parse_log_lines_for_live(
            ["[2026-06-16 22:35] Dispatching coder for #99"]
        )
        assert result[0]["timestamp"] == "2026-06-16 22:35"
        assert result[0]["message"] == "Dispatching coder for #99"

    def test_parse_log_lines_skips_blank_lines(self, client, tmp_path):
        import server
        result = server._parse_log_lines_for_live(["", "  ", "Real line"])
        assert len(result) == 1
        assert "Real line" in result[0]["message"]

    def test_log_lines_html_escapes_user_content(self):
        # escHtml must be applied to both timestamp and message
        log_lines_fn = _HTML[_HTML.find("function _smgmtLiveLogLinesHtml"):]
        assert "escHtml" in log_lines_fn, (
            "FAIL AC-3: escHtml() not called in _smgmtLiveLogLinesHtml — XSS risk"
        )


# ═════════════════════════════════════════════════════════════════════════════
# AC-4  Scrollable panel, max-height 200px, auto-scrolls to newest entry
# ═════════════════════════════════════════════════════════════════════════════

class TestAC4Scrollable:
    """Panel has max-height 200px, overflow-y auto, and auto-scrolls on update."""

    def test_log_stream_has_max_height_200px(self):
        m = re.search(r'\.smgmt-live-log-stream\s*\{([^}]+)\}', _HTML, re.DOTALL)
        assert m, "FAIL AC-4: .smgmt-live-log-stream CSS rule not found"
        rule = m.group(1)
        assert "200px" in rule, (
            f"FAIL AC-4: max-height 200px not set on .smgmt-live-log-stream: {rule}"
        )

    def test_log_stream_overflow_y_auto(self):
        m = re.search(r'\.smgmt-live-log-stream\s*\{([^}]+)\}', _HTML, re.DOTALL)
        assert m, "FAIL AC-4: .smgmt-live-log-stream CSS rule not found"
        rule = m.group(1)
        assert "overflow-y" in rule and "auto" in rule, (
            f"FAIL AC-4: overflow-y: auto not set on .smgmt-live-log-stream: {rule}"
        )

    def test_live_log_patch_sets_scroll_top(self):
        patch_fn = _HTML[_HTML.find("function _smgmtLiveLogPatch"):]
        assert "scrollTop" in patch_fn and "scrollHeight" in patch_fn, (
            "FAIL AC-4: _smgmtLiveLogPatch does not set scrollTop = scrollHeight"
        )

    def test_live_log_patch_auto_scroll_direction(self):
        patch_fn = _HTML[_HTML.find("function _smgmtLiveLogPatch"):]
        m = re.search(r'scrollTop\s*=\s*\w+\.scrollHeight', patch_fn)
        assert m, (
            "FAIL AC-4: scrollTop is not set to scrollHeight in _smgmtLiveLogPatch"
        )


# ═════════════════════════════════════════════════════════════════════════════
# AC-5  Reuses smgmt-log-* CSS classes from archived-log styling
# ═════════════════════════════════════════════════════════════════════════════

class TestAC5ReusesLogClasses:
    """Live log panel reuses existing smgmt-log-* classes, not new ad-hoc ones."""

    def test_smgmt_log_line_class_defined_in_css(self):
        assert ".smgmt-log-line" in _HTML, (
            "FAIL AC-5: .smgmt-log-line CSS class not defined"
        )

    def test_smgmt_log_time_class_defined_in_css(self):
        assert ".smgmt-log-time" in _HTML, (
            "FAIL AC-5: .smgmt-log-time CSS class not defined"
        )

    def test_live_log_line_html_uses_smgmt_log_line(self):
        log_lines_fn = _HTML[_HTML.find("function _smgmtLiveLogLinesHtml"):]
        assert "smgmt-log-line" in log_lines_fn, (
            "FAIL AC-5: live log does not reuse smgmt-log-line class"
        )

    def test_live_log_line_html_uses_smgmt_log_time(self):
        log_lines_fn = _HTML[_HTML.find("function _smgmtLiveLogLinesHtml"):]
        assert "smgmt-log-time" in log_lines_fn, (
            "FAIL AC-5: live log does not reuse smgmt-log-time class"
        )


# ═════════════════════════════════════════════════════════════════════════════
# AC-6  Log panel header includes a "live" streaming indicator
# ═════════════════════════════════════════════════════════════════════════════

class TestAC6LiveIndicator:
    """Header contains a visual 'live' indicator and the literal text 'live'."""

    def test_live_indicator_css_class_defined(self):
        assert ".smgmt-live-indicator" in _HTML, (
            "FAIL AC-6: .smgmt-live-indicator CSS class not defined"
        )

    def test_live_indicator_element_in_template(self):
        assert 'smgmt-live-indicator' in _HTML, (
            "FAIL AC-6: smgmt-live-indicator element not in running card template"
        )

    def test_live_indicator_is_animated(self):
        m = re.search(r'\.smgmt-live-indicator\s*\{([^}]+)\}', _HTML, re.DOTALL)
        assert m, "FAIL AC-6: .smgmt-live-indicator CSS rule not found"
        rule = m.group(1)
        assert "animation" in rule, (
            "FAIL AC-6: .smgmt-live-indicator has no animation — streaming indicator must pulse"
        )

    def test_live_text_label_in_log_bar(self):
        log_bar = _HTML[_HTML.find("smgmt-live-log-bar"):]
        assert ">live<" in log_bar, (
            "FAIL AC-6: the text 'live' is not present in the log bar header"
        )

    def test_log_bar_css_uses_monospace(self):
        m = re.search(r'\.smgmt-live-log-bar\s*\{([^}]+)\}', _HTML, re.DOTALL)
        assert m, "FAIL AC-6: .smgmt-live-log-bar CSS rule not found"
        rule = m.group(1)
        assert "mono" in rule, (
            f"FAIL AC-6: .smgmt-live-log-bar does not use monospace font: {rule}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# AC-7  active_agent and pid shown in log panel header
# ═════════════════════════════════════════════════════════════════════════════

class TestAC7ActiveAgentInHeader:
    """/live returns active_agent; header renders name·pid when present."""

    def test_live_endpoint_returns_active_agent_key(self, client):
        _post_status(client, _sprint_status([_issue(1)]))
        data = _get_live(client)
        assert "active_agent" in data, (
            "FAIL AC-7: /live response does not contain 'active_agent' key"
        )

    def test_active_agent_null_when_no_state_or_pid_file(self, client):
        _post_status(client, _sprint_status([_issue(1)]))
        data = _get_live(client)
        assert data["active_agent"] is None, (
            "FAIL AC-7: active_agent should be null when no state/pid files exist"
        )

    def test_active_agent_includes_name_and_pid_from_pid_file(self, client, tmp_path):
        import server as _server

        (tmp_path / ".commander" / "sprints").mkdir(parents=True, exist_ok=True)
        pid_file = tmp_path / ".commander" / "sprints" / "sprint-99-pid"
        pid_file.write_text("12345\n")

        _post_status(client, _sprint_status([_issue(1)]))
        data = _get_live(client)
        assert data["active_agent"] is not None, (
            "FAIL AC-7: active_agent should be set when a pid file exists"
        )
        assert data["active_agent"]["pid"] == 12345, (
            "FAIL AC-7: active_agent.pid should match the pid file contents"
        )

    def test_active_agent_from_state_file_sets_name(self, client, tmp_path):
        (tmp_path / ".commander" / "sprints").mkdir(parents=True, exist_ok=True)
        state = {
            "issues": [
                {
                    "number": 1,
                    "coder_started_at": "2026-01-01T00:00:00Z",
                    "tester_started_at": None,
                    "tester_finished_at": None,
                }
            ]
        }
        state_file = tmp_path / ".commander" / "sprints" / "sprint-99-state.json"
        state_file.write_text(json.dumps(state))

        _post_status(client, _sprint_status([_issue(1)]))
        data = _get_live(client)
        assert data["active_agent"] is not None, (
            "FAIL AC-7: active_agent should not be null when state file identifies a running issue"
        )
        assert data["active_agent"]["name"] == "coder", (
            f"FAIL AC-7: active_agent.name should be 'coder' for in-flight coder: {data['active_agent']}"
        )

    def test_active_agent_name_uppercase_in_js_template(self):
        template_region = _HTML[_HTML.find("smgmt-live-agent"):]
        assert "toUpperCase()" in template_region or ".toUpperCase()" in template_region, (
            "FAIL AC-7: active_agent.name is not converted toUpperCase() in the template"
        )

    def test_active_agent_pid_included_in_header_text(self):
        template_region = _HTML[_HTML.find("smgmt-live-agent"):]
        assert "pid" in template_region, (
            "FAIL AC-7: 'pid' not shown in smgmt-live-agent header element"
        )

    def test_agent_el_id_in_live_log_patch(self):
        patch_fn = _HTML[_HTML.find("function _smgmtLiveLogPatch"):]
        assert "smgmt-live-agent-" in patch_fn, (
            "FAIL AC-7: _smgmtLiveLogPatch does not update smgmt-live-agent-{label} element"
        )

    def test_agent_el_text_cleared_when_no_agent(self):
        patch_fn = _HTML[_HTML.find("function _smgmtLiveLogPatch"):]
        # When agent is falsy, textContent should be set to ''
        assert "textContent" in patch_fn, (
            "FAIL AC-7: _smgmtLiveLogPatch does not update agent element's textContent"
        )


# ═════════════════════════════════════════════════════════════════════════════
# AC-8  Live log panel removed during outcome band injection
# ═════════════════════════════════════════════════════════════════════════════

class TestAC8OutcomeBandTransition:
    """_smgmtInjectOutcomeBand removes the live log panel from the card."""

    def test_inject_outcome_band_removes_live_log_panel(self):
        inject_fn = _HTML[_HTML.find("function _smgmtInjectOutcomeBand"):]
        inject_fn_body = inject_fn[:inject_fn.find("\nfunction ", 10)]
        assert "smgmt-live-log" in inject_fn_body, (
            "FAIL AC-8: _smgmtInjectOutcomeBand does not remove .smgmt-live-log panel — "
            "live log will remain on card after sprint ends"
        )

    def test_inject_outcome_band_queries_live_log(self):
        inject_fn = _HTML[_HTML.find("function _smgmtInjectOutcomeBand"):]
        inject_fn_body = inject_fn[:inject_fn.find("\nfunction ", 10)]
        m = re.search(r"querySelector\(['\"]\.smgmt-live-log['\"]", inject_fn_body)
        assert m, (
            "FAIL AC-8: _smgmtInjectOutcomeBand does not querySelector('.smgmt-live-log')"
        )

    def test_inject_outcome_band_calls_remove_on_live_log(self):
        inject_fn = _HTML[_HTML.find("function _smgmtInjectOutcomeBand"):]
        inject_fn_body = inject_fn[:inject_fn.find("\nfunction ", 10)]
        live_log_section = inject_fn_body[inject_fn_body.find("smgmt-live-log"):]
        # The element should be removed after the querySelector
        assert ".remove()" in live_log_section, (
            "FAIL AC-8: .remove() is not called on the live log panel in _smgmtInjectOutcomeBand"
        )


# ═════════════════════════════════════════════════════════════════════════════
# AC-9  Running → outcome transition preserves all tickets
# ═════════════════════════════════════════════════════════════════════════════

class TestAC9TransitionPreservesTickets:
    """All tickets present in running view remain after sprint transitions to outcome."""

    def test_outcome_band_does_not_remove_ticket_rows(self):
        inject_fn = _HTML[_HTML.find("function _smgmtInjectOutcomeBand"):]
        inject_fn_body = inject_fn[:inject_fn.find("\nfunction ", 10)]
        # Must NOT remove smgmt-sprint-tickets or smgmt-ticket-row elements
        assert "smgmt-sprint-tickets" not in inject_fn_body or \
               ".remove()" not in inject_fn_body[inject_fn_body.find("smgmt-sprint-tickets"):
                                                  inject_fn_body.find("smgmt-sprint-tickets") + 100], (
            "FAIL AC-9: _smgmtInjectOutcomeBand removes smgmt-sprint-tickets — tickets will disappear"
        )

    def test_live_endpoint_returns_all_submitted_issues(self, client):
        _post_status(client, _sprint_status([
            _issue(10, "Ticket A", status="done",    agent_status="completed"),
            _issue(11, "Ticket B", status="pending", agent_status="queued"),
            _issue(12, "Ticket C", status="pending", agent_status="coder_running"),
        ]))
        data = _get_live(client)
        numbers = {i["number"] for i in data["issues"]}
        assert numbers == {10, 11, 12}, (
            f"FAIL AC-9: /live issues missing tickets — got {numbers}, expected {{10, 11, 12}}"
        )

    def test_done_and_pending_tickets_both_returned(self, client):
        _post_status(client, _sprint_status([
            _issue(20, "Done ticket",    status="done",    agent_status="completed"),
            _issue(21, "Pending ticket", status="pending", agent_status="queued"),
        ]))
        data = _get_live(client)
        by_num = {i["number"]: i for i in data["issues"]}
        assert 20 in by_num and 21 in by_num, (
            "FAIL AC-9: /live does not return both done and pending tickets"
        )


# ═════════════════════════════════════════════════════════════════════════════
# AC-10  Uses polling (recent_log_lines), not SSE /live/stream
# ═════════════════════════════════════════════════════════════════════════════

class TestAC10PollingNotSSE:
    """Log panel reads from cached recent_log_lines (polling), not from /live/stream SSE."""

    def test_log_poll_reads_from_live_cache_not_stream(self):
        poll_tick_fn = _HTML[_HTML.find("function _smgmtLogPollTick"):]
        poll_tick_body = poll_tick_fn[:poll_tick_fn.find("\nfunction ", 10)]
        assert "_smgmtLiveCache" in poll_tick_body, (
            "FAIL AC-10: _smgmtLogPollTick does not read from _smgmtLiveCache — "
            "log panel must use cached poll data, not direct fetch"
        )

    def test_log_patch_reads_recent_log_lines_from_live(self):
        patch_fn = _HTML[_HTML.find("function _smgmtLiveLogPatch"):]
        patch_body = patch_fn[:patch_fn.find("\nfunction ", 10)]
        assert "recent_log_lines" in patch_body, (
            "FAIL AC-10: _smgmtLiveLogPatch does not use live.recent_log_lines"
        )

    def test_log_panel_does_not_fetch_live_stream_directly(self):
        poll_tick_fn = _HTML[_HTML.find("function _smgmtLogPollTick"):]
        poll_tick_body = poll_tick_fn[:poll_tick_fn.find("\nfunction ", 10)]
        assert "fetch(" not in poll_tick_body, (
            "FAIL AC-10: _smgmtLogPollTick calls fetch() — it must read from cache, not SSE stream"
        )

    def test_no_eventsource_in_log_panel_code(self):
        poll_tick_fn = _HTML[_HTML.find("function _smgmtLogPollTick"):]
        poll_tick_body = poll_tick_fn[:poll_tick_fn.find("\nfunction ", 10)]
        assert "EventSource" not in poll_tick_body, (
            "FAIL AC-10: EventSource found in _smgmtLogPollTick — must use polling, not SSE"
        )

    def test_parse_log_lines_limit_is_50(self, tmp_path):
        import server
        # Generate 60 lines; only the last 50 should be returned
        lines = [f"Line {i}" for i in range(60)]
        result = server._parse_log_lines_for_live(lines, limit=50)
        assert len(result) == 50, (
            f"FAIL AC-10: _parse_log_lines_for_live should return at most 50 entries, got {len(result)}"
        )
        assert "Line 59" in result[-1]["message"], (
            "FAIL AC-10: last entry should be the newest line (Line 59)"
        )

    def test_parse_log_lines_returns_oldest_first(self, tmp_path):
        import server
        lines = ["First line", "Second line", "Third line"]
        result = server._parse_log_lines_for_live(lines)
        assert "First" in result[0]["message"], (
            "FAIL AC-10: _parse_log_lines_for_live should return lines oldest-first"
        )
        assert "Third" in result[-1]["message"], (
            "FAIL AC-10: _parse_log_lines_for_live should return lines oldest-first"
        )


# ═════════════════════════════════════════════════════════════════════════════
# Integration: /live endpoint exposes all required fields for issue #309
# ═════════════════════════════════════════════════════════════════════════════

class TestIntegration:
    """End-to-end: /live response has all fields needed by the live log panel."""

    def test_live_snapshot_has_all_issue_309_fields(self, client):
        _post_status(client, _sprint_status([
            _issue(100, "Queued",    status="pending",   agent_status="queued"),
            _issue(101, "Running",   status="pending",   agent_status="coder_running",
                   coder_started_at="2026-01-01T00:00:00Z"),
            _issue(102, "Done",      status="done",      agent_status="completed"),
        ]))
        data = _get_live(client)

        # Both new fields must be present
        assert "recent_log_lines" in data, "FAIL integration: recent_log_lines missing from /live"
        assert "active_agent" in data,     "FAIL integration: active_agent missing from /live"

        # recent_log_lines type
        assert isinstance(data["recent_log_lines"], list), \
            "FAIL integration: recent_log_lines must be a list"

        # active_agent is None or a dict with name/pid keys
        if data["active_agent"] is not None:
            assert "name" in data["active_agent"], \
                "FAIL integration: active_agent missing 'name' key"
            assert "pid" in data["active_agent"], \
                "FAIL integration: active_agent missing 'pid' key"

        # Issues array still present (not broken by issue #309 changes)
        assert "issues" in data, "FAIL integration: issues array missing — regression in /live"
        assert len(data["issues"]) == 3, \
            f"FAIL integration: expected 3 issues, got {len(data['issues'])}"

    def test_recent_log_lines_populated_from_log_file(self, client, tmp_path):
        import server

        # Create a log file the endpoint will read
        logs_dir = tmp_path / ".commander" / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_file = logs_dir / "sprint-run-sprint-99-20260101.log"
        log_file.write_text("→ Dispatching coder\n✓ #100 done\n")

        _post_status(client, _sprint_status([_issue(100)]))

        with patch("server._find_latest_sprint_log", return_value=log_file):
            resp = client.get(
                "/api/sprints/sprint-99/live",
                params={"project": "zealchaiwut/commander"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["recent_log_lines"]) == 2, (
            f"FAIL integration: expected 2 log entries from file, got {len(data['recent_log_lines'])}"
        )
        messages = [e["message"] for e in data["recent_log_lines"]]
        assert any("Dispatching coder" in m for m in messages), \
            "FAIL integration: dispatch log line not in recent_log_lines"
        assert any("#100 done" in m for m in messages), \
            "FAIL integration: success log line not in recent_log_lines"
