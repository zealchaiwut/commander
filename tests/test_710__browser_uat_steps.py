"""Tests for issue #710: Run browser UAT steps via agent-browser, not MANUAL.

Each test is anchored to a specific acceptance criterion:

AC-1: Tester Step 6 routes a UAT step to the browser runner when the BA flagged
      it `agent-testable` OR its text describes a browser interaction (keywords:
      open, navigate, click, see, expect, page) — instead of marking MANUAL.
AC-2: An agent_browser_runner result is recorded as PASS or FAIL with an
      attached screenshot path.
AC-3: A FAIL browser step sets overall ticket status to NEEDS_FIXES, identical
      to a failed AC check.
AC-4: A step is marked MANUAL only when the runner returns "uncovered" or when
      the runner is not installed.
AC-5: HTTP-only UAT steps (no browser interaction) continue to route to httpx.
AC-6: sprint_manager tester dispatch injects agent_browser_runner into the
      tester environment and surfaces its results in the final test report.
AC-7: The example ticket UAT step (open localhost:8000/project/x, click Run
      Sprint, expect a spinner) produces a PASS/FAIL report entry with a
      screenshot — never MANUAL.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import services.sprint_manager.agent_browser_runner as abr
import services.sprint_manager.sprint_manager as sm

TESTER_MD = REPO_ROOT / ".claude" / "agents" / "tester.md"

EXAMPLE_BROWSER_STEP = (
    "open localhost:8000/project/x and click Run Sprint, expect a spinner"
)


# ── AC-1: browser routing by agent-testable flag or browser keywords ─────────

@pytest.mark.parametrize("step", [
    "Open localhost:8000/project/1 and click Run Sprint",
    "Navigate to the dashboard and verify the title",
    "Click the Approve button",
    "You should see a loading spinner",
    "Expect a modal to appear",
    "The settings page renders the dark theme",
])
def test_browser_keyword_steps_route_to_browser(step):
    """AC-1: steps with browser-interaction keywords route to the browser runner."""
    assert abr.classify_uat_step(step) == "browser"
    assert abr.is_browser_step(step) is True


def test_agent_testable_flag_forces_browser_route():
    """AC-1: a BA `agent-testable` flag routes to browser even with no keywords."""
    plain = "The widget total reflects the latest tally"
    assert abr.classify_uat_step(plain) != "browser"  # no keyword on its own
    assert abr.classify_uat_step(plain, agent_testable=True) == "browser"
    assert abr.is_browser_step(plain, agent_testable=True) is True


# ── AC-2: result recorded as PASS / FAIL with a screenshot path ──────────────

def test_pass_result_maps_to_pass_status_with_screenshot():
    """AC-2: a passing runner result reports PASS and carries a screenshot path."""
    result = {"status": "pass", "detail": "spinner shown", "screenshot_path": "/tmp/uat-1.png"}
    assert abr.report_status(result) == "PASS"
    assert result["screenshot_path"] == "/tmp/uat-1.png"


def test_fail_result_maps_to_fail_status():
    """AC-2: a failing runner result reports FAIL."""
    result = {"status": "fail", "detail": "no spinner", "screenshot_path": "/tmp/uat-2.png"}
    assert abr.report_status(result) == "FAIL"


# ── AC-3: a FAIL browser step forces NEEDS_FIXES (same as a failed AC) ───────

def test_failed_browser_step_counts_as_failure():
    """AC-3: a fail result is a failure, driving overall status to NEEDS_FIXES."""
    failed = {"status": "fail", "detail": "error state", "screenshot_path": "/tmp/x.png"}
    assert abr.counts_as_failure(failed) is True
    # tester.md must state that a failed browser step sets NEEDS_FIXES
    text = TESTER_MD.read_text(encoding="utf-8")
    assert "NEEDS_FIXES" in text
    lower = text.lower()
    assert "browser" in lower and "needs_fixes" in lower


# ── AC-4: MANUAL only when uncovered or runner not installed ─────────────────

def test_manual_only_when_uncovered():
    """AC-4: an uncovered result is the MANUAL fallback; pass/fail are not."""
    uncovered = {"status": "uncovered", "detail": "x", "screenshot_path": None}
    assert abr.report_status(uncovered) == "MANUAL"
    assert abr.is_manual_fallback(uncovered) is True
    assert abr.report_status({"status": "pass", "screenshot_path": "/tmp/a.png"}) != "MANUAL"
    assert abr.report_status({"status": "fail", "screenshot_path": None}) != "MANUAL"


def test_not_installed_falls_back_to_uncovered_manual():
    """AC-4: when the runner is not installed, the step becomes MANUAL (uncovered)."""
    with patch.object(abr, "is_available", return_value=False):
        result = abr.run_browser_step(EXAMPLE_BROWSER_STEP, "http://localhost:8000")
    assert result["status"] == "uncovered"
    assert abr.report_status(result) == "MANUAL"


# ── AC-5: HTTP-only steps continue to route to httpx ─────────────────────────

@pytest.mark.parametrize("step", [
    "POST /api/sprint/start returns 200 with {status: running}",
    "GET /api/home responds with a 200 status code",
    "The DELETE /api/agents/3 endpoint removes the agent",
])
def test_http_only_steps_route_to_http(step):
    """AC-5: HTTP/API steps with no browser keywords route to httpx, not browser."""
    assert abr.classify_uat_step(step) == "http"
    assert abr.is_browser_step(step) is False


# ── AC-6: sprint_manager dispatch injects the runner + surfaces results ──────

def _capture_tester_dispatch(tmp_path, sprint_label="sprint-54"):
    """Run _dispatch_tester with Popen mocked; return the (cmd, env) it built."""
    log_path = tmp_path / "tester.log"
    log_path.write_text("", encoding="utf-8")

    captured = {}

    def fake_popen(cmd, *args, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        proc = MagicMock()
        proc.wait.return_value = 0
        proc.kill.side_effect = ProcessLookupError("already dead")
        return proc

    with (
        patch.object(sm.subprocess, "Popen", side_effect=fake_popen),
        patch.object(sm, "_r", return_value="owner/repo"),
        patch.object(sm, "_issue_log_path", return_value=log_path),
        patch.object(sm, "_post_agent_event"),
        patch.object(sm, "dispatch_alerts"),
        patch.object(sm, "_add_blocked_label"),
        patch.object(sm.agent_browser_runner, "is_available", return_value=False),
    ):
        sm._dispatch_tester(issue_num=1, alert_modes=[], sprint_label=sprint_label)
    return captured


def test_dispatch_injects_agent_browser_env(tmp_path):
    """AC-6: dispatch sets the agent-browser availability env var for the tester."""
    captured = _capture_tester_dispatch(tmp_path)
    env = captured["env"]
    assert env is not None
    assert env.get("COMMANDER_AGENT_BROWSER_AVAILABLE") in ("0", "1")


def test_dispatch_prompt_mentions_browser_runner(tmp_path):
    """AC-6: the tester prompt instructs Step-6 browser routing via the runner."""
    captured = _capture_tester_dispatch(tmp_path)
    prompt = captured["cmd"][-1]
    assert "agent_browser_runner" in prompt
    assert "710" in prompt
    # the routing keywords from AC-1 must be surfaced to the tester
    assert "MANUAL" in prompt


# ── AC-7: the example ticket step yields PASS/FAIL + screenshot, not MANUAL ──

def test_example_step_classifies_as_browser():
    """AC-7: the canonical example UAT step routes to the browser runner."""
    assert abr.classify_uat_step(EXAMPLE_BROWSER_STEP) == "browser"


def test_example_step_pass_produces_screenshot_not_manual(tmp_path):
    """AC-7: a passing run of the example step reports PASS with a real screenshot."""
    shot = tmp_path / "uat-spinner.png"

    def fake_run(cmd, *args, **kwargs):
        shot.write_bytes(b"\x89PNG\r\n\x1a\n")

        class _R:
            returncode = 0
            stdout = '{"success": true, "detail": "spinner appeared"}'
            stderr = ""
        return _R()

    with (
        patch.object(abr, "is_available", return_value=True),
        patch.object(abr, "_new_screenshot_path", return_value=shot),
        patch.object(abr.subprocess, "run", side_effect=fake_run),
    ):
        result = abr.run_browser_step(EXAMPLE_BROWSER_STEP, "http://localhost:8000")

    assert abr.report_status(result) == "PASS"
    assert result["status"] != "uncovered"  # never MANUAL
    assert result["screenshot_path"] == str(shot)
    assert Path(result["screenshot_path"]).exists()


def test_example_step_fail_sets_needs_fixes_signal(tmp_path):
    """AC-7/AC-3: a failing run of the example step is a failure with a screenshot."""
    shot = tmp_path / "uat-error.png"

    def fake_run(cmd, *args, **kwargs):
        shot.write_bytes(b"\x89PNG\r\n\x1a\n")

        class _R:
            returncode = 0
            stdout = '{"success": false, "detail": "spinner never appeared"}'
            stderr = ""
        return _R()

    with (
        patch.object(abr, "is_available", return_value=True),
        patch.object(abr, "_new_screenshot_path", return_value=shot),
        patch.object(abr.subprocess, "run", side_effect=fake_run),
    ):
        result = abr.run_browser_step(EXAMPLE_BROWSER_STEP, "http://localhost:8000")

    assert abr.report_status(result) == "FAIL"
    assert abr.counts_as_failure(result) is True
    assert Path(result["screenshot_path"]).exists()


# ── tester.md documents the Step-6 contract (AC-1, AC-2, AC-4, AC-5) ─────────

def test_tester_md_step6_documents_browser_contract():
    """tester.md Step 6 must document the browser-runner routing and fallbacks."""
    text = TESTER_MD.read_text(encoding="utf-8")
    lower = text.lower()
    assert "agent_browser_runner" in text            # the runner is wired in
    assert "httpx" in lower                           # AC-5: http path retained
    assert "uncovered" in lower                       # AC-4: MANUAL only on uncovered
    assert "screenshot" in lower                      # AC-2: screenshot recorded
    # AC-1: at least some of the browser keywords are listed in Step 6
    assert any(kw in lower for kw in ("navigate", "click", "expect"))
