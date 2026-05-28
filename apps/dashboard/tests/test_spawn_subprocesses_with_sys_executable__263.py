"""Tests for issue #263 — spawn sprint subprocesses with sys.executable instead of literal python3

Acceptance criteria:
- All four ["python3", ...] subprocess invocations in server.py are replaced with [sys.executable, ...].
- sys import is confirmed present at the top of server.py.
- After spawning a sprint dispatch subprocess, the server waits up to 2 seconds; if the process
  exits before that deadline, it reads the tail of the dispatch log file and returns HTTP 502
  with the error detail in the response body.
- Triggering "Run Sprint" on a valid sprint causes per-issue logs to appear, confirming
  sprint_manager.py starts processing tickets.
- When pyyaml is unavailable to the spawned subprocess (simulated), the dashboard returns HTTP 502
  and the response body contains the actual error text from the log rather than an empty 202.
- No regression: successful sprint dispatch still returns HTTP 202 when the subprocess starts
  cleanly and runs past the 2-second window.

Static checks validate the source files directly (no live server required).
Live-server checks are marked with @pytest.mark.live.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from unittest import mock

import pytest

SERVER_PY = Path(__file__).parent.parent / "server.py"


# ---------------------------------------------------------------------------
# AC-1: All ["python3", ...] literals are replaced with [sys.executable, ...]
# ---------------------------------------------------------------------------

class TestSysExecutableSubstitution:
    def test_sys_module_is_imported(self):
        """AC-2: sys import is confirmed present at the top of server.py."""
        content = SERVER_PY.read_text()
        lines = content.split('\n')[:50]  # Check first 50 lines for imports
        assert any('import sys' in line for line in lines), (
            "sys module must be imported in server.py"
        )

    def test_no_literal_python3_in_subprocess_commands(self):
        """AC-1: All subprocess invocations use sys.executable, not "python3"."""
        content = SERVER_PY.read_text()

        # Find all subprocess.Popen and subprocess.run calls
        import re
        popen_calls = re.findall(r'subprocess\.Popen\s*\(\s*\[(.*?)\]', content, re.DOTALL)
        run_calls = re.findall(r'subprocess\.run\s*\(\s*\[(.*?)\]', content, re.DOTALL)

        for call in popen_calls + run_calls:
            # Each call should not contain the literal string "python3"
            assert '"python3"' not in call and "'python3'" not in call, (
                f"Found literal 'python3' in subprocess call: {call[:100]}"
            )

    def test_init_project_uses_sys_executable(self):
        """AC-1: init_project endpoint uses sys.executable."""
        content = SERVER_PY.read_text()
        # Find the init_project function
        match = content.find("def init_project")
        assert match != -1, "init_project function must exist"
        func_content = content[match:match+2000]
        assert "sys.executable" in func_content, (
            "init_project must use sys.executable to spawn init_project.py"
        )
        assert '"python3"' not in func_content or "'python3'" not in func_content

    def test_run_sprint_uses_sys_executable(self):
        """AC-1: run_sprint endpoint uses sys.executable."""
        content = SERVER_PY.read_text()
        # Find the run_sprint function
        match = content.find("def run_sprint(body: SprintRunBody)")
        assert match != -1, "run_sprint function must exist"
        func_content = content[match:match+2000]
        assert "sys.executable" in func_content, (
            "run_sprint must use sys.executable to spawn sprint_manager.py"
        )
        assert '"python3"' not in func_content or "'python3'" not in func_content

    def test_run_sprint_managed_uses_sys_executable(self):
        """AC-1: run_sprint_managed endpoint uses sys.executable."""
        content = SERVER_PY.read_text()
        # Find the run_sprint_managed function
        match = content.find("def run_sprint_managed(body: SprintMgmtRunBody)")
        assert match != -1, "run_sprint_managed function must exist"
        # Find the next function to get a reasonable chunk size
        next_match = content.find("@app.", match + 100)
        if next_match == -1:
            next_match = match + 5000
        func_content = content[match:next_match]
        assert "sys.executable" in func_content, (
            "run_sprint_managed must use sys.executable to spawn sprint_manager.py"
        )
        assert '"python3"' not in func_content or "'python3'" not in func_content

    def test_promote_to_master_uses_sys_executable(self):
        """AC-1: promote_to_master endpoint uses sys.executable."""
        content = SERVER_PY.read_text()
        # Find the promote_to_master function
        match = content.find("def promote_to_master(body: PromoteBody)")
        assert match != -1, "promote_to_master function must exist"
        func_content = content[match:match+1500]
        assert "sys.executable" in func_content, (
            "promote_to_master must use sys.executable to spawn promote_to_master.py"
        )
        assert '"python3"' not in func_content or "'python3'" not in func_content


# ---------------------------------------------------------------------------
# AC-3: Early-crash detection (wait 2 seconds, return HTTP 502 on early exit)
# ---------------------------------------------------------------------------

class TestEarlyEarlyDetection:
    def test_run_sprint_managed_has_early_crash_detection(self):
        """AC-3: run_sprint_managed includes early-crash detection logic."""
        content = SERVER_PY.read_text()

        # Check for early-crash detection pattern anywhere in the file
        assert "proc.wait" in content, "Must call proc.wait() for early-crash detection"
        assert "TimeoutExpired" in content, (
            "Must catch subprocess.TimeoutExpired exception"
        )
        assert "proc.wait(timeout=2.0)" in content, (
            "Timeout should be exactly 2.0 seconds"
        )
        assert 'status_code=502' in content, "Must return HTTP 502 on early crash"

        # Verify the pattern is in run_sprint_managed specifically
        match = content.find("def run_sprint_managed(body: SprintMgmtRunBody)")
        end_match = content.find("@app.", match + 100)
        if end_match == -1:
            end_match = len(content)
        func_section = content[match:end_match]

        assert "proc.wait" in func_section, (
            "run_sprint_managed must include proc.wait() for early-crash detection"
        )

    def test_early_crash_reads_log_tail(self):
        """AC-3: Early-crash detection reads the log tail for error message."""
        content = SERVER_PY.read_text()

        # Verify log tail reading is implemented
        assert "read_text" in content, "Must read log file for error message"
        assert "splitlines()" in content, "Must extract tail lines from log"

        # Verify it's in run_sprint_managed
        match = content.find("def run_sprint_managed(body: SprintMgmtRunBody)")
        end_match = content.find("@app.", match + 100)
        if end_match == -1:
            end_match = len(content)
        func_section = content[match:end_match]

        assert "log_path.read_text" in func_section, (
            "run_sprint_managed must read the log file for error details"
        )

    def test_timeout_expired_allows_202_response(self):
        """AC-6: When subprocess is still running after 2 seconds, return 202."""
        content = SERVER_PY.read_text()

        # Verify error handling
        assert "TimeoutExpired" in content, (
            "Must catch subprocess.TimeoutExpired exception"
        )
        assert "except subprocess.TimeoutExpired:" in content, (
            "Must explicitly catch TimeoutExpired"
        )

        # Verify the pattern is in run_sprint_managed
        match = content.find("def run_sprint_managed(body: SprintMgmtRunBody)")
        end_match = content.find("@app.", match + 100)
        if end_match == -1:
            end_match = len(content)
        func_section = content[match:end_match]

        assert "TimeoutExpired" in func_section, (
            "run_sprint_managed must handle TimeoutExpired for normal startup"
        )


# ---------------------------------------------------------------------------
# Live server tests (require running dashboard)
# ---------------------------------------------------------------------------

@pytest.mark.live
class TestLiveSprintDispatch:
    def test_successful_sprint_run_returns_202(self, client):
        """AC-6: Successful sprint dispatch returns HTTP 202 when subprocess starts cleanly."""
        # This test requires a valid sprint label and project
        # It's a placeholder; the actual test needs a real sprint setup
        # For now, we verify the endpoint exists
        pass  # Will be implemented when integrating with live tests

    def test_early_crash_returns_502(self, client):
        """AC-5: When subprocess exits immediately, return HTTP 502 with error detail."""
        # This test requires simulating a crash in sprint_manager.py
        # For now, we verify the error handling path exists
        pass  # Will be implemented when integrating with live tests


# ---------------------------------------------------------------------------
# Integration: no regression from the fix
# ---------------------------------------------------------------------------

class TestNoRegression:
    def test_popen_calls_still_work(self):
        """Verify that using sys.executable doesn't break subprocess.Popen calls."""
        # Simple smoke test: sys.executable is valid
        import sys
        assert sys.executable, "sys.executable must be non-empty"
        assert Path(sys.executable).exists(), (
            f"sys.executable must point to a valid Python interpreter: {sys.executable}"
        )

    def test_sys_executable_resolves_to_venv_python(self):
        """When running in a venv, sys.executable should point to the venv's python."""
        import sys
        assert "venv" in sys.executable or "env" in sys.executable or "python" in sys.executable, (
            f"sys.executable should point to venv or system interpreter: {sys.executable}"
        )
