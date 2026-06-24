"""Tests for issue #1537 — lint_ticket_spec.py clean error on gh API failure.

AC: When _fetch_issue's subprocess.run raises CalledProcessError (gh
unauthenticated, offline, or issue not found), the script must:
  - Not propagate a raw CalledProcessError traceback
  - Print a human-readable error message to stderr
  - Include the gh stderr output in the error output
  - Exit with a non-zero exit code
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "lint_ticket_spec.py"


def _load_lint_module():
    spec = importlib.util.spec_from_file_location("lint_ticket_spec", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_called_process_error(stderr="gh: error: not authenticated", returncode=1):
    err = subprocess.CalledProcessError(
        returncode,
        ["gh", "api", "repos/owner/repo/issues/99"],
    )
    err.stderr = stderr
    err.stdout = ""
    return err


# ── AC: no raw CalledProcessError propagates — exits cleanly ─────────────────

def test_fetch_issue_gh_failure_no_raw_traceback():
    """_fetch_issue must not let CalledProcessError propagate to the caller."""
    mod = _load_lint_module()
    with patch.object(mod.subprocess, "run", side_effect=_make_called_process_error()):
        try:
            mod._fetch_issue(99, "owner/repo")
        except subprocess.CalledProcessError:
            pytest.fail("CalledProcessError escaped _fetch_issue — raw traceback would be shown")
        except SystemExit:
            pass  # expected — clean exit


# ── AC: exits with non-zero code ─────────────────────────────────────────────

def test_fetch_issue_gh_failure_exits_nonzero():
    """_fetch_issue must sys.exit with a non-zero code on gh failure."""
    mod = _load_lint_module()
    with patch.object(mod.subprocess, "run", side_effect=_make_called_process_error()):
        with pytest.raises(SystemExit) as exc_info:
            mod._fetch_issue(99, "owner/repo")
    assert exc_info.value.code != 0


def test_fetch_issue_gh_failure_exits_with_code_1():
    """Exit code must be 1 (standard error)."""
    mod = _load_lint_module()
    with patch.object(mod.subprocess, "run", side_effect=_make_called_process_error(returncode=1)):
        with pytest.raises(SystemExit) as exc_info:
            mod._fetch_issue(99, "owner/repo")
    assert exc_info.value.code == 1


# ── AC: gh stderr included in printed output ─────────────────────────────────

def test_fetch_issue_gh_failure_prints_gh_stderr(capsys):
    """The gh stderr must appear in the error output so the user knows what failed."""
    mod = _load_lint_module()
    with patch.object(mod.subprocess, "run", side_effect=_make_called_process_error(
        stderr="gh: HTTP 401: Bad credentials"
    )):
        with pytest.raises(SystemExit):
            mod._fetch_issue(99, "owner/repo")
    captured = capsys.readouterr()
    assert "gh: HTTP 401: Bad credentials" in captured.err


def test_fetch_issue_gh_failure_prints_gh_stderr_not_found(capsys):
    """404 / issue-not-found stderr also printed clearly."""
    mod = _load_lint_module()
    with patch.object(mod.subprocess, "run", side_effect=_make_called_process_error(
        stderr="gh: HTTP 404: Not Found"
    )):
        with pytest.raises(SystemExit):
            mod._fetch_issue(1537, "zealchaiwut/commander")
    captured = capsys.readouterr()
    assert "gh: HTTP 404: Not Found" in captured.err


# ── AC: human-readable message also printed ──────────────────────────────────

def test_fetch_issue_gh_failure_prints_readable_message(capsys):
    """A human-readable error line must appear in stderr, not just raw Python."""
    mod = _load_lint_module()
    with patch.object(mod.subprocess, "run", side_effect=_make_called_process_error()):
        with pytest.raises(SystemExit):
            mod._fetch_issue(99, "owner/repo")
    captured = capsys.readouterr()
    assert "error" in captured.err.lower()


def test_fetch_issue_gh_failure_message_mentions_issue_number(capsys):
    """The error message should reference the issue number so the user knows which call failed."""
    mod = _load_lint_module()
    with patch.object(mod.subprocess, "run", side_effect=_make_called_process_error()):
        with pytest.raises(SystemExit):
            mod._fetch_issue(42, "owner/repo")
    captured = capsys.readouterr()
    assert "42" in captured.err


# ── Sanity: success path still works ─────────────────────────────────────────

def test_fetch_issue_success_returns_dict():
    """Success path must be unaffected — still returns a dict with expected keys."""
    import json
    mod = _load_lint_module()
    fake_result = type("R", (), {
        "stdout": json.dumps({"number": 1, "title": "My title", "body": "## Acceptance Criteria\n\n- [ ] do thing\n"}),
        "returncode": 0,
        "stderr": "",
    })()
    with patch.object(mod.subprocess, "run", return_value=fake_result):
        result = mod._fetch_issue(1, "owner/repo")
    assert result == {"number": 1, "title": "My title", "body": "## Acceptance Criteria\n\n- [ ] do thing\n"}
