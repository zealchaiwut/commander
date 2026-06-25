"""Tests for issue #868: doctor.py claude check probes auth, not just --version.

Acceptance criteria:
1. check_claude_cli runs claude --version as a reachability gate
2. After reachability passes, executes a cheap auth probe (claude -p "")
3. Auth failure reports distinct failure status and message
4. Success reports passing status with auth verified
5. Displayed check label is "Claude CLI (auth)"
6. Unauthenticated install (binary present, no token) causes FAIL
"""
import subprocess
from pathlib import Path
from importlib.util import spec_from_file_location, module_from_spec


# Resolve repo root for import access to scripts.
REPO_ROOT = Path(__file__).resolve().parent.parent


# Import doctor.py dynamically since it's in scripts/ and not a package
def _import_doctor():
    doctor_path = REPO_ROOT / "scripts" / "doctor.py"
    spec = spec_from_file_location("doctor", doctor_path)
    doctor = module_from_spec(spec)
    spec.loader.exec_module(doctor)
    return doctor


def test_868__reachability_gate_missing_binary():
    """AC: reachability gate fails immediately when binary is absent."""
    doctor = _import_doctor()
    check_claude_cli = doctor.check_claude_cli

    # Mock which() to return None (binary not found)
    result = check_claude_cli(which=lambda x: None)

    assert not result.ok, "Expected FAIL when binary not found"
    assert "CLI not found" in result.fix or "Install Claude Code" in result.fix
    assert result.name == "Claude CLI (auth)"


def test_868__reachability_gate_binary_present():
    """AC: reachability gate passes when claude --version succeeds."""
    doctor = _import_doctor()
    check_claude_cli = doctor.check_claude_cli

    # Mock which() to return a path (binary found)
    def mock_which(tool):
        return "/usr/local/bin/claude" if tool == "claude" else None

    # Mock runner to return successful --version call
    def mock_runner_success(cmd, **kwargs):
        result = subprocess.CompletedProcess(
            cmd, returncode=0, stdout="Claude 1.0.0\n", stderr=""
        )
        return result

    # When reachability succeeds but auth fails, check should fail with auth message
    def mock_runner_auth_fail(cmd, **kwargs):
        if cmd[0] == "claude" and "--version" in cmd:
            return subprocess.CompletedProcess(
                cmd, returncode=0, stdout="Claude 1.0.0\n", stderr=""
            )
        # Auth probe fails
        return subprocess.CompletedProcess(
            cmd, returncode=1, stdout="", stderr="Error: not authenticated"
        )

    result = check_claude_cli(which=mock_which, runner=mock_runner_auth_fail)

    # Should fail, but with auth-specific message (not "CLI not found")
    assert not result.ok, "Expected FAIL when auth probe fails"
    assert "authenticated" in result.fix.lower() or "log in" in result.fix.lower()
    assert "not authenticated" in result.detail or "Error" in result.detail


def test_868__auth_probe_success():
    """AC: success reports passing status when both gates pass."""
    doctor = _import_doctor()
    check_claude_cli = doctor.check_claude_cli

    def mock_which(tool):
        return "/usr/local/bin/claude" if tool == "claude" else None

    def mock_runner_both_pass(cmd, **kwargs):
        # Both --version and auth probe succeed
        return subprocess.CompletedProcess(
            cmd, returncode=0, stdout="Claude 1.0.0\n" if "--version" in cmd else "", stderr=""
        )

    result = check_claude_cli(which=mock_which, runner=mock_runner_both_pass)

    assert result.ok, "Expected PASS when both reachability and auth succeed"
    assert "authenticated" in result.detail.lower() or "auth" in result.detail.lower()
    assert result.name == "Claude CLI (auth)"


def test_868__check_label_includes_auth():
    """AC: displayed check label is 'Claude CLI (auth)'."""
    doctor = _import_doctor()
    check_claude_cli = doctor.check_claude_cli

    def mock_which(tool):
        return "/usr/local/bin/claude" if tool == "claude" else None

    def mock_runner(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, returncode=0, stdout="Claude 1.0.0\n", stderr=""
        )

    result = check_claude_cli(which=mock_which, runner=mock_runner)

    assert result.name == "Claude CLI (auth)", f"Expected 'Claude CLI (auth)', got '{result.name}'"


def test_868__unauthenticated_install_fails():
    """AC: unauthenticated install (binary present, no token) causes FAIL."""
    doctor = _import_doctor()
    check_claude_cli = doctor.check_claude_cli

    def mock_which(tool):
        return "/usr/local/bin/claude" if tool == "claude" else None

    def mock_runner_unauth(cmd, **kwargs):
        if cmd[0] == "claude" and "--version" in cmd:
            # Binary reachable
            return subprocess.CompletedProcess(
                cmd, returncode=0, stdout="Claude 1.0.0\n", stderr=""
            )
        # Auth probe fails (no token)
        return subprocess.CompletedProcess(
            cmd, returncode=1, stdout="", stderr="Error: authentication required"
        )

    result = check_claude_cli(which=mock_which, runner=mock_runner_unauth)

    assert not result.ok, "Expected FAIL for unauthenticated install"
    # Ensure the fix message distinguishes auth failure from binary-not-found
    assert "authenticated" in result.fix.lower() or "/login" in result.fix.lower()
    assert "Install Claude Code" not in result.fix  # Should not suggest reinstall


def test_868__auth_error_distinct_from_binary_not_found():
    """AC: auth failure and binary-not-found produce distinct failure messages."""
    doctor = _import_doctor()
    check_claude_cli = doctor.check_claude_cli

    # Test 1: Binary not found
    binary_not_found = check_claude_cli(which=lambda x: None)
    assert not binary_not_found.ok

    # Test 2: Binary present, auth fails
    def mock_which(tool):
        return "/usr/local/bin/claude" if tool == "claude" else None

    def mock_runner_auth_fail(cmd, **kwargs):
        if "--version" in cmd:
            return subprocess.CompletedProcess(
                cmd, returncode=0, stdout="Claude 1.0.0\n", stderr=""
            )
        return subprocess.CompletedProcess(
            cmd, returncode=1, stdout="", stderr="authentication required"
        )

    auth_failed = check_claude_cli(which=mock_which, runner=mock_runner_auth_fail)
    assert not auth_failed.ok

    # Fix messages should be different
    assert binary_not_found.fix != auth_failed.fix, "Expected distinct fix messages"
    assert "Install" in binary_not_found.fix, "Binary-not-found should mention install"
    assert ("log in" in auth_failed.fix.lower() or "authenticated" in auth_failed.fix.lower()), \
        "Auth failure should mention login/authentication"
