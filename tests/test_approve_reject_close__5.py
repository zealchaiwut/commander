"""
Tests for issue #5: Fix approve/reject — close on approve, reopen on reject, add CLI scripts.

AC1: reject_issue calls gh issue reopen before adding labels.
AC2: approve_issue calls gh issue close --reason completed.
AC3: approve_ticket.py exits 0, prints #N url, swaps labels, closes with reason.
AC4: reject_ticket.py exits 0, prints #N url, swaps labels, reopens, posts comment.
AC5: update_ticket.py accepts --status uat-approved and produces same end state.
AC6: Both scripts print #N url on success and exit non-zero on failure.
"""
import inspect
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# ── path setup ────────────────────────────────────────────────────────────────

DASHBOARD_DIR = Path(__file__).parent.parent / "dashboard"
sys.path.insert(0, str(DASHBOARD_DIR))
sys.path.insert(0, str(DASHBOARD_DIR / "scripts"))

import github_client  # noqa: E402


# ── AC1: reject_issue reopens before labelling ────────────────────────────────

def test_ac1_reject_issue_reopens_before_labelling():
    """reject_issue must call 'gh issue reopen' before editing labels."""
    call_order = []

    def mock_run(*args):
        call_order.append(list(args))
        return ""

    with patch.object(github_client, "_run", side_effect=mock_run):
        with patch.object(github_client, "invalidate"):
            github_client.reject_issue(99, "some reason", "owner/repo")

    # Find reopen and edit calls
    reopen_idx = None
    edit_idx = None
    for i, c in enumerate(call_order):
        if "reopen" in c:
            reopen_idx = i
        if "edit" in c and "--add-label" in c:
            edit_idx = i

    assert reopen_idx is not None, "gh issue reopen was never called"
    assert edit_idx is not None, "gh issue edit was never called"
    assert reopen_idx < edit_idx, (
        f"reopen (call #{reopen_idx}) must happen before label edit (call #{edit_idx})"
    )


# ── AC2: approve_issue closes with --reason completed ─────────────────────────

def test_ac2_approve_issue_closes_with_reason_completed():
    """approve_issue must pass --reason completed to gh issue close."""
    close_args_captured = []

    def mock_run(*args):
        if "close" in args:
            close_args_captured.extend(args)
        return ""

    with patch.object(github_client, "_run", side_effect=mock_run):
        with patch.object(github_client, "invalidate"):
            github_client.approve_issue(99, "owner/repo")

    assert "--reason" in close_args_captured, (
        f"'--reason' flag missing from close call. Got: {close_args_captured}"
    )
    idx = close_args_captured.index("--reason")
    assert close_args_captured[idx + 1] == "completed", (
        f"Expected reason 'completed', got '{close_args_captured[idx + 1]}'"
    )


# ── AC3: approve_ticket.py CLI exits 0 and prints #N url ─────────────────────

def test_ac3_approve_ticket_script_exists_and_is_executable():
    """approve_ticket.py must exist in scripts/."""
    script = DASHBOARD_DIR / "scripts" / "approve_ticket.py"
    assert script.exists(), f"Missing script: {script}"


def test_ac3_approve_ticket_help_exits_zero():
    """approve_ticket.py --help must exit 0 (sanity: argparse wired up)."""
    result = subprocess.run(
        [sys.executable, str(DASHBOARD_DIR / "scripts" / "approve_ticket.py"), "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"--help failed: {result.stderr}"
    assert "--issue" in result.stdout, "'--issue' not in help output"
    assert "--comment" in result.stdout, "'--comment' not in help output"


def test_ac3_approve_ticket_output_format(monkeypatch):
    """approve_ticket.py must print '#N url' when approve succeeds (exits normally)."""
    import importlib
    import builtins

    approve_mod = importlib.import_module("approve_ticket")

    def fake_approve(issue_id, repo_name=None):
        pass

    def fake_repo():
        return "zealchaiwut/commander"

    monkeypatch.setattr(github_client, "approve_issue", fake_approve)
    monkeypatch.setattr(github_client, "repo", fake_repo)

    printed = []
    monkeypatch.setattr(builtins, "print", lambda *a, **kw: printed.append(" ".join(str(x) for x in a)))
    monkeypatch.setattr(sys, "argv", ["approve_ticket.py", "--issue", "5"])

    # main() returns normally on success (no SystemExit)
    approve_mod.main()

    assert printed, "approve_ticket.py produced no output"
    assert printed[0].startswith("#5 "), f"Expected '#5 <url>', got: {printed[0]!r}"
    assert "zealchaiwut/commander/issues/5" in printed[0]


# ── AC4: reject_ticket.py CLI exits 0, reopens, posts comment ────────────────

def test_ac4_reject_ticket_script_exists():
    """reject_ticket.py must exist in scripts/."""
    script = DASHBOARD_DIR / "scripts" / "reject_ticket.py"
    assert script.exists(), f"Missing script: {script}"


def test_ac4_reject_ticket_requires_reason():
    """reject_ticket.py without --reason must exit non-zero."""
    result = subprocess.run(
        [sys.executable, str(DASHBOARD_DIR / "scripts" / "reject_ticket.py"),
         "--issue", "5"],
        capture_output=True, text=True,
    )
    assert result.returncode != 0, (
        "reject_ticket.py must exit non-zero when --reason is omitted"
    )


def test_ac4_reject_ticket_output_format(monkeypatch):
    """reject_ticket.py must print '#N url' when reject succeeds (exits normally)."""
    import importlib
    import builtins

    reject_mod = importlib.import_module("reject_ticket")

    def fake_reject(issue_id, reason, repo_name=None):
        pass

    def fake_repo():
        return "zealchaiwut/commander"

    monkeypatch.setattr(github_client, "reject_issue", fake_reject)
    monkeypatch.setattr(github_client, "repo", fake_repo)

    printed = []
    monkeypatch.setattr(builtins, "print", lambda *a, **kw: printed.append(" ".join(str(x) for x in a)))
    monkeypatch.setattr(sys, "argv", ["reject_ticket.py", "--issue", "5", "--reason", "test"])

    # main() returns normally on success (no SystemExit)
    reject_mod.main()

    assert printed, "reject_ticket.py produced no output"
    assert printed[0].startswith("#5 "), f"Expected '#5 <url>', got: {printed[0]!r}"
    assert "zealchaiwut/commander/issues/5" in printed[0]


# ── AC5: update_ticket.py accepts --status uat-approved ───────────────────────

def test_ac5_update_ticket_accepts_uat_approved():
    """update_ticket.py must list 'uat-approved' as a valid --status choice."""
    result = subprocess.run(
        [sys.executable, str(DASHBOARD_DIR / "scripts" / "update_ticket.py"), "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "uat-approved" in result.stdout, (
        "'uat-approved' not listed in update_ticket.py --help"
    )


def test_ac5_update_ticket_uat_approved_closes_with_completed():
    """update_ticket.py STATUS_MAP for uat-approved must close with reason 'completed'."""
    import importlib
    update_mod = importlib.import_module("update_ticket")

    mapping = update_mod.STATUS_MAP.get("uat-approved")
    assert mapping is not None, "STATUS_MAP missing 'uat-approved' key"
    assert "UAT-approved" in mapping.get("add", []), (
        "uat-approved mapping must add 'UAT-approved' label"
    )
    assert "UAT" in mapping.get("remove", []), (
        "uat-approved mapping must remove 'UAT' label"
    )
    assert mapping.get("close") == "completed", (
        f"Expected close reason 'completed', got: {mapping.get('close')!r}"
    )


# ── AC6: scripts print #N url on success, exit non-zero on failure ────────────

def test_ac6_approve_ticket_exits_nonzero_on_missing_issue_flag():
    """approve_ticket.py without --issue must exit non-zero."""
    result = subprocess.run(
        [sys.executable, str(DASHBOARD_DIR / "scripts" / "approve_ticket.py")],
        capture_output=True, text=True,
    )
    assert result.returncode != 0, (
        "approve_ticket.py should exit non-zero when --issue is missing"
    )


def test_ac6_reject_ticket_exits_nonzero_on_missing_flags():
    """reject_ticket.py without any flags must exit non-zero."""
    result = subprocess.run(
        [sys.executable, str(DASHBOARD_DIR / "scripts" / "reject_ticket.py")],
        capture_output=True, text=True,
    )
    assert result.returncode != 0, (
        "reject_ticket.py should exit non-zero when --issue and --reason are missing"
    )


def test_ac6_reject_ticket_exits_nonzero_missing_reason():
    """reject_ticket.py with --issue but no --reason must exit non-zero."""
    result = subprocess.run(
        [sys.executable, str(DASHBOARD_DIR / "scripts" / "reject_ticket.py"),
         "--issue", "5"],
        capture_output=True, text=True,
    )
    assert result.returncode != 0, (
        "reject_ticket.py must exit non-zero when --reason is omitted"
    )
