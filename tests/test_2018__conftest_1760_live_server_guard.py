"""Tests for issue #2018: conftest.py live-server guard for 1760 module.

The 1760 test module does NOT self-skip when UAT_BASE_URL/UAT_PORT are unset.
BASE_URL fallback is "http://localhost:" which passes the startswith("http")
check (no RuntimeError), but tests fail with httpx.ConnectError at runtime
without the _LIVE_SERVER_TEST_MODULES guard.

AC1: 1760 HTTP tests are SKIPPED (not FAILED/ERROR) when UAT_BASE_URL and
     UAT_PORT are absent from the environment.
AC2: conftest.py has an inline comment on the 1760 entry documenting why it
     cannot self-skip and therefore needs the guard.
"""
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
_TARGET_FILE = "tests/test_bulk_move_new_sprint_clear_selection__1760.py"


def _run_1760_without_uat() -> subprocess.CompletedProcess:
    """Run the 1760 suite without UAT env vars, stripping them from the inherited env."""
    env = {k: v for k, v in os.environ.items() if k not in ("UAT_BASE_URL", "UAT_PORT")}
    return subprocess.run(
        [sys.executable, "-m", "pytest", _TARGET_FILE, "-v", "--no-header", "--tb=short"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
    )


def test_1760_skipped_not_failed_when_uat_unset():
    """AC1: 1760 HTTP tests are SKIPPED (not FAILED/ERROR) when UAT_BASE_URL/UAT_PORT absent.

    Without the _LIVE_SERVER_TEST_MODULES guard, the two HTTP tests would run
    against BASE_URL="http://localhost:" and fail with httpx.ConnectError.
    """
    result = _run_1760_without_uat()
    out = result.stdout + result.stderr
    assert "FAILED" not in out, f"1760 tests must not FAIL without UAT env vars:\n{out}"
    assert "ERROR" not in out, f"1760 tests must not ERROR without UAT env vars:\n{out}"
    assert "skipped" in out.lower(), (
        f"1760 tests must be SKIPPED when UAT is unreachable:\n{out}"
    )


def test_1760_guard_has_inline_comment():
    """AC2: conftest.py must have an inline comment on the 1760 _LIVE_SERVER_TEST_MODULES
    entry explaining why it cannot self-skip and needs the guard.
    """
    source = (REPO_ROOT / "conftest.py").read_text()
    for line in source.splitlines():
        if "test_bulk_move_new_sprint_clear_selection__1760" in line and "__1760" in line:
            if "_PERMANENTLY_DESELECTED" in line:
                continue  # skip the deselected-nodeids entry; only check the modules set
            assert "#" in line, (
                "The 1760 entry in _LIVE_SERVER_TEST_MODULES must have an inline comment "
                f"explaining why it cannot self-skip. Got: {line!r}"
            )
            comment = line[line.index("#"):]
            assert any(kw in comment for kw in ("self-skip", "BASE_URL", "ConnectError")), (
                "The inline comment must contain the rationale for why the module cannot "
                f"self-skip (expected 'self-skip', 'BASE_URL', or 'ConnectError'). Got: {comment!r}"
            )
            return
    raise AssertionError(
        "1760 entry not found in _LIVE_SERVER_TEST_MODULES — was it removed?"
    )
