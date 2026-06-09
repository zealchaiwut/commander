"""Tests for issue #709: Wire agent-browser as Live Browser UAT Tester.

AC-1: Module exists, importable with no side-effects when agent-browser absent.
AC-2: is_available() -> True only when `which agent-browser` succeeds AND Chrome set up.
AC-3: run_browser_step() returns dict with keys status / detail / screenshot_path.
AC-4: When unavailable, run_browser_step logs a WARNING and returns the uncovered
      result without raising.
AC-5: Non-browser steps (physical device, hardware) return status "uncovered".
AC-6: Caller helper treats "uncovered" as MANUAL and not as a failure.
AC-7: With agent-browser installed, run_browser_step navigates, asserts, and returns
      a screenshot_path pointing to a real file on disk.
AC-8: Unit tests cover (a) is_available mocked which hit/miss, (b) run_browser_step
      unavailable, (c) run_browser_step uncovered for a non-browser step.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import services.sprint_manager.agent_browser_runner as abr


# ── AC-1: import has no side-effects ────────────────────────────────────────

def test_module_imports_with_no_side_effects():
    """AC-1: importing the module must not call the binary or raise."""
    # Re-import is a no-op; assert the public surface exists.
    assert hasattr(abr, "is_available")
    assert hasattr(abr, "run_browser_step")
    assert callable(abr.is_available)
    assert callable(abr.run_browser_step)


# ── AC-2 / AC-8(a): is_available with mocked which hit/miss ──────────────────

def test_is_available_false_when_which_misses():
    """AC-2/AC-8(a): no binary on PATH -> not available."""
    with patch.object(abr.shutil, "which", return_value=None):
        assert abr.is_available() is False


def test_is_available_false_when_binary_present_but_chrome_not_setup():
    """AC-2: binary present but `agent-browser install` not run -> not available."""
    with patch.object(abr.shutil, "which", return_value="/usr/local/bin/agent-browser"), \
         patch.object(abr, "_chrome_ready", return_value=False):
        assert abr.is_available() is False


def test_is_available_true_when_which_hits_and_chrome_ready():
    """AC-2/AC-8(a): binary present AND Chrome set up -> available."""
    with patch.object(abr.shutil, "which", return_value="/usr/local/bin/agent-browser"), \
         patch.object(abr, "_chrome_ready", return_value=True):
        assert abr.is_available() is True


# ── AC-3 / AC-4 / AC-8(b): unavailable -> uncovered + WARNING ────────────────

def test_run_browser_step_unavailable_returns_uncovered(caplog):
    """AC-3/AC-4/AC-8(b): unavailable -> uncovered result, WARNING logged, no raise."""
    with patch.object(abr, "is_available", return_value=False):
        with caplog.at_level(logging.WARNING):
            result = abr.run_browser_step("Navigate to home and verify title", "http://localhost:3000")

    assert set(result.keys()) == {"status", "detail", "screenshot_path"}
    assert result["status"] == "uncovered"
    assert result["detail"] == "agent-browser not installed"
    assert result["screenshot_path"] is None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1


# ── AC-5 / AC-8(c): non-browser step -> uncovered ────────────────────────────

@pytest.mark.parametrize("step", [
    "Tap the back button on a physical Android device",
    "Connect the USB hardware dongle and observe the LED",
    "Run the app on a real iPhone and pinch to zoom",
])
def test_non_browser_step_returns_uncovered(step):
    """AC-5/AC-8(c): device/hardware steps are not automatable -> uncovered."""
    with patch.object(abr, "is_available", return_value=True):
        result = abr.run_browser_step(step, "http://localhost:3000")
    assert result["status"] == "uncovered"
    assert result["screenshot_path"] is None


def test_browser_step_is_automatable():
    """AC-5: a normal web interaction is recognised as automatable."""
    assert abr.is_step_automatable("Navigate to the home page and verify the title is 'My App'") is True


# ── AC-6: caller helpers treat uncovered as MANUAL, not failure ──────────────

def test_uncovered_is_manual_fallback_and_not_failure():
    """AC-6: uncovered -> MANUAL fallback, never counted as a failure."""
    uncovered = {"status": "uncovered", "detail": "x", "screenshot_path": None}
    assert abr.is_manual_fallback(uncovered) is True
    assert abr.counts_as_failure(uncovered) is False


def test_pass_and_fail_classification():
    """AC-6: pass is neither manual nor failure; fail is a failure, not manual."""
    passed = {"status": "pass", "detail": "ok", "screenshot_path": "/tmp/a.png"}
    failed = {"status": "fail", "detail": "bad", "screenshot_path": None}
    assert abr.is_manual_fallback(passed) is False
    assert abr.counts_as_failure(passed) is False
    assert abr.is_manual_fallback(failed) is False
    assert abr.counts_as_failure(failed) is True


# ── AC-7: installed -> navigate, assert, real screenshot on disk ─────────────

def test_run_browser_step_returns_real_screenshot_when_installed(tmp_path):
    """AC-7: with agent-browser available, a pass returns a screenshot path that exists."""
    shot = tmp_path / "uat-shot.png"

    def fake_run(cmd, *args, **kwargs):
        # Simulate agent-browser writing a screenshot and reporting success.
        shot.write_bytes(b"\x89PNG\r\n\x1a\n")

        class _R:
            returncode = 0
            stdout = '{"success": true, "detail": "title matched: My App"}'
            stderr = ""
        return _R()

    with patch.object(abr, "is_available", return_value=True), \
         patch.object(abr, "_new_screenshot_path", return_value=shot), \
         patch.object(abr.subprocess, "run", side_effect=fake_run):
        result = abr.run_browser_step(
            "Navigate to the home page and verify the title is 'My App'",
            "http://localhost:3000",
        )

    assert result["status"] == "pass"
    assert result["detail"]
    assert result["screenshot_path"] == str(shot)
    assert Path(result["screenshot_path"]).exists()


def test_run_browser_step_reports_fail_on_failed_assertion(tmp_path):
    """AC-7: a failed assertion from agent-browser surfaces as status fail."""
    shot = tmp_path / "uat-shot.png"

    def fake_run(cmd, *args, **kwargs):
        shot.write_bytes(b"\x89PNG\r\n\x1a\n")

        class _R:
            returncode = 0
            stdout = '{"success": false, "detail": "title mismatch"}'
            stderr = ""
        return _R()

    with patch.object(abr, "is_available", return_value=True), \
         patch.object(abr, "_new_screenshot_path", return_value=shot), \
         patch.object(abr.subprocess, "run", side_effect=fake_run):
        result = abr.run_browser_step("Navigate and verify title", "http://localhost:3000")

    assert result["status"] == "fail"
    assert "mismatch" in result["detail"]
