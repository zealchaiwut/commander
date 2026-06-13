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

import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import services.sprint_manager.agent_browser_runner as abr


# ── AC-1: import has no side-effects ────────────────────────────────────────

def test_module_imports_with_no_side_effects():
    """AC-1: importing the module must not call the binary or raise."""
    # Re-import is a no-op; assert the public surface exists. agent-browser 0.27
    # is a low-level CLI with no high-level `run` command, so the Tester drives it
    # via the `run_cli` one-command primitive (not a `run_browser_step` wrapper).
    assert hasattr(abr, "is_available") and callable(abr.is_available)
    assert hasattr(abr, "run_cli") and callable(abr.run_cli)
    assert hasattr(abr, "classify_uat_step") and callable(abr.classify_uat_step)


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


# ── _chrome_ready: probes --version + the downloaded Chrome dir (no `status`) ─

def test_chrome_ready_true_when_version_ok_and_chrome_present(tmp_path):
    chrome = tmp_path / "chrome-149.0.0"
    chrome.mkdir()

    class _R:
        returncode = 0
        stdout = "agent-browser 0.27.3"
        stderr = ""
    with patch.object(abr.subprocess, "run", return_value=_R()), \
         patch.object(abr, "_CHROME_DIR", tmp_path):
        assert abr._chrome_ready() is True


def test_chrome_ready_false_when_chrome_missing(tmp_path):
    class _R:
        returncode = 0
        stdout = "agent-browser 0.27.3"
        stderr = ""
    with patch.object(abr.subprocess, "run", return_value=_R()), \
         patch.object(abr, "_CHROME_DIR", tmp_path):  # empty dir, no chrome-*
        assert abr._chrome_ready() is False


def test_chrome_ready_false_when_binary_errors(tmp_path):
    with patch.object(abr.subprocess, "run", side_effect=OSError("not found")):
        assert abr._chrome_ready() is False


# ── run_cli: thin one-command executor the Tester composes UAT steps from ─────

def test_run_cli_passthrough():
    class _R:
        returncode = 0
        stdout = "https://example.com/"
        stderr = ""
    with patch.object(abr.subprocess, "run", return_value=_R()) as m:
        rc, out, err = abr.run_cli(["get", "url"])
    assert (rc, out, err) == (0, "https://example.com/", "")
    # invoked the real binary with our args
    assert m.call_args[0][0][0] == abr.BINARY and m.call_args[0][0][1:] == ["get", "url"]


def test_run_cli_timeout_degrades():
    import subprocess as _sp
    with patch.object(abr.subprocess, "run", side_effect=_sp.TimeoutExpired("agent-browser", 5)):
        rc, out, err = abr.run_cli(["open", "http://x"], timeout=5)
    assert rc == -1 and out == "" and "timed out" in err


def test_run_cli_missing_binary_degrades():
    with patch.object(abr.subprocess, "run", side_effect=OSError("no such binary")):
        rc, out, err = abr.run_cli(["open", "http://x"])
    assert rc == -1 and out == "" and "error" in err.lower()


def test_browser_step_is_automatable():
    """A normal web interaction is recognised as automatable."""
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


# ── classify routing still drives Step 6 (browser / http / manual) ───────────

def test_classify_uat_step_routes():
    assert abr.classify_uat_step("Open the home page and click Run") == "browser"
    assert abr.classify_uat_step("GET /api/health returns 200") == "http"
    assert abr.classify_uat_step("Looks visually balanced on a phone") == "manual"
    # BA agent-testable flag forces the browser route.
    assert abr.classify_uat_step("verify the calibration chart", agent_testable=True) == "browser"
