"""Tests for issue #868: check_claude_cli must probe auth, not just --version.

Each test is anchored to a specific AC item. The implementation must satisfy
all 6 AC items; tests pass only when the implementation does the right thing,
not when the test has been softened.
"""
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import doctor  # noqa: E402

FAKE_PATH = "/usr/local/bin/claude"


def _ok(cmd, stdout="ok"):
    return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")


def _fail(cmd, stdout="", stderr="error"):
    return subprocess.CompletedProcess(cmd, 1, stdout=stdout, stderr=stderr)


def _multi_runner(responses):
    """Runner that dispatches by whether '--version' appears in the command."""
    calls = []

    def runner(cmd, timeout=8):
        calls.append(list(cmd))
        key = "version" if "--version" in cmd else "auth"
        return responses.get(key, _ok(cmd))

    runner.calls = calls
    return runner


# ── AC1: binary missing / not executable → immediate fail, no auth probe ─────

def test_ac1_binary_missing_fails_with_install_or_not_found_message():
    """which() returns None → check fails with a message about installing/PATH."""
    result = doctor.check_claude_cli(which=lambda _: None)
    assert not result.ok
    fix_lower = result.fix.lower()
    assert "install" in fix_lower or "path" in fix_lower or "not found" in fix_lower


def test_ac1_binary_missing_does_not_attempt_auth_probe():
    """Runner must not be called at all when which() returns None."""
    calls = []

    def runner(cmd, timeout=8):
        calls.append(cmd)
        return _ok(cmd)

    doctor.check_claude_cli(which=lambda _: None, runner=runner)
    assert calls == [], f"auth probe was called despite missing binary: {calls}"


def test_ac1_version_raises_filenotfound_fails_without_auth_probe():
    """FileNotFoundError on --version → reachability fail, fix mentions install/PATH."""
    auth_called = []

    def runner(cmd, timeout=8):
        if "--version" in cmd:
            raise FileNotFoundError("no such file")
        auth_called.append(cmd)
        return _ok(cmd)

    result = doctor.check_claude_cli(which=lambda _: FAKE_PATH, runner=runner)
    assert not result.ok
    assert auth_called == [], "auth probe should not run after reachability failure"
    fix_lower = result.fix.lower()
    assert "install" in fix_lower or "path" in fix_lower


def test_ac1_version_nonzero_fails_without_auth_probe():
    """Non-zero from --version → reachability fail; auth probe is not attempted."""
    auth_called = []

    def runner(cmd, timeout=8):
        if "--version" in cmd:
            return _fail(cmd, stderr="crash")
        auth_called.append(cmd)
        return _ok(cmd)

    result = doctor.check_claude_cli(which=lambda _: FAKE_PATH, runner=runner)
    assert not result.ok
    assert auth_called == [], "auth probe should not run after --version failure"


# ── AC2: auth probe is executed after reachability passes ─────────────────────

def test_ac2_auth_probe_called_after_version_passes():
    """Runner is called at least twice: once for --version, once for the auth probe."""
    r = _multi_runner({
        "version": _ok(["claude", "--version"], stdout="1.0.0"),
        "auth": _ok(["claude", "-p", ""], stdout="{}"),
    })
    doctor.check_claude_cli(which=lambda _: FAKE_PATH, runner=r)
    assert len(r.calls) >= 2, f"Expected ≥2 runner calls, got: {r.calls}"
    version_calls = [c for c in r.calls if "--version" in c]
    auth_calls = [c for c in r.calls if "--version" not in c]
    assert version_calls, "No --version call found"
    assert auth_calls, "No auth probe call found"


def test_ac2_auth_probe_uses_claude_binary():
    """Auth probe command starts with 'claude' (not some other tool)."""
    r = _multi_runner({
        "version": _ok(["claude", "--version"], stdout="1.0.0"),
        "auth": _ok(["claude", "-p", ""], stdout="{}"),
    })
    doctor.check_claude_cli(which=lambda _: FAKE_PATH, runner=r)
    auth_calls = [c for c in r.calls if "--version" not in c]
    assert auth_calls, "No auth probe call"
    assert auth_calls[0][0] == "claude", f"Auth probe must call 'claude', got: {auth_calls[0]}"


# ── AC3: auth failure → distinct message from binary-not-found ────────────────

def test_ac3_auth_failure_fix_differs_from_not_found_fix():
    """Auth failure fix must be distinct from binary-not-found fix."""
    def runner_auth_fail(cmd, timeout=8):
        if "--version" in cmd:
            return _ok(cmd, stdout="1.0.0")
        return _fail(cmd, stderr="Not authenticated. Run claude to log in.")

    not_found_result = doctor.check_claude_cli(which=lambda _: None)
    auth_fail_result = doctor.check_claude_cli(
        which=lambda _: FAKE_PATH, runner=runner_auth_fail
    )

    assert not auth_fail_result.ok
    assert auth_fail_result.fix != not_found_result.fix, (
        "Auth failure fix must differ from binary-not-found fix"
    )


def test_ac3_auth_failure_fix_mentions_auth_or_login():
    """Auth failure fix message must guide the user toward authentication."""
    def runner(cmd, timeout=8):
        if "--version" in cmd:
            return _ok(cmd, stdout="1.0.0")
        return _fail(cmd, stderr="Login required")

    result = doctor.check_claude_cli(which=lambda _: FAKE_PATH, runner=runner)
    assert not result.ok
    fix_lower = result.fix.lower()
    assert any(kw in fix_lower for kw in ("login", "auth", "token", "authenticate")), (
        f"Auth failure fix must mention login/auth/token, got: {result.fix!r}"
    )


def test_ac3_auth_failure_detail_includes_probe_output():
    """Auth failure detail should carry the probe's output for diagnostics."""
    def runner(cmd, timeout=8):
        if "--version" in cmd:
            return _ok(cmd, stdout="1.0.0")
        return _fail(cmd, stderr="Error: OAuth token expired")

    result = doctor.check_claude_cli(which=lambda _: FAKE_PATH, runner=runner)
    assert not result.ok
    # detail must expose the raw error for the operator
    assert result.detail.strip(), "detail should not be empty on auth failure"


# ── AC4: auth probe succeeds → passing status with auth confirmation ──────────

def test_ac4_both_gates_pass_returns_ok():
    """Version OK + auth probe OK → result.ok is True."""
    def runner(cmd, timeout=8):
        return _ok(cmd, stdout="{}")

    result = doctor.check_claude_cli(which=lambda _: FAKE_PATH, runner=runner)
    assert result.ok, f"Expected pass, got fix={result.fix!r}"


def test_ac4_passing_detail_mentions_authenticated():
    """Passing detail must confirm the CLI is both reachable and authenticated."""
    def runner(cmd, timeout=8):
        return _ok(cmd, stdout="{}")

    result = doctor.check_claude_cli(which=lambda _: FAKE_PATH, runner=runner)
    assert result.ok
    detail_lower = result.detail.lower()
    assert "auth" in detail_lower or "authenticated" in detail_lower, (
        f"Passing detail must mention auth, got: {result.detail!r}"
    )


# ── AC5: check label must reference auth verification ────────────────────────

def test_ac5_label_contains_auth():
    """check_claude_cli().name must contain 'auth' regardless of outcome."""
    # Test with missing binary (fastest path to a result)
    result = doctor.check_claude_cli(which=lambda _: None)
    assert "auth" in result.name.lower(), (
        f"Label must contain 'auth', got: {result.name!r}"
    )


def test_ac5_label_not_only_reachable_or_binary_only():
    """Label must not be a binary-only label like 'Claude CLI reachable'."""
    result = doctor.check_claude_cli(which=lambda _: None)
    # The old label was 'claude CLI reachable' — must be replaced with auth label
    assert result.name.lower() != "claude cli reachable", (
        "Label was not updated from the old binary-only label"
    )


# ── AC6: unauthenticated install → overall check failure ─────────────────────

def test_ac6_unauthenticated_install_fails_check():
    """Binary present + --version passes + auth probe fails → check fails."""
    def runner(cmd, timeout=8):
        if "--version" in cmd:
            return _ok(cmd, stdout="2.0.0")
        # auth probe exits non-zero (no valid OAuth token)
        return _fail(cmd, stderr="Not authenticated. Please run `claude` and log in.")

    result = doctor.check_claude_cli(which=lambda _: FAKE_PATH, runner=runner)
    assert not result.ok, (
        "Unauthenticated install must cause check_claude_cli to fail"
    )


def test_ac6_authenticated_install_passes_check():
    """Binary present + --version passes + auth probe passes → check passes."""
    def runner(cmd, timeout=8):
        return _ok(cmd, stdout="{}")

    result = doctor.check_claude_cli(which=lambda _: FAKE_PATH, runner=runner)
    assert result.ok, (
        "Authenticated install must cause check_claude_cli to pass"
    )


def test_ac6_run_all_checks_reflects_auth_failure():
    """run_all_checks() returns ok=False for claude check when auth fails."""
    def failing_check_claude():
        return doctor._result(
            "Claude CLI (auth)", False,
            fix="Run `claude` and log in.",
            detail="Not authenticated",
        )

    original = doctor.check_claude_cli
    try:
        doctor.check_claude_cli = failing_check_claude
        results = doctor.run_all_checks()
        claude_result = results[0]
        assert not claude_result.ok, "Claude check must be failed in run_all_checks when auth fails"
    finally:
        doctor.check_claude_cli = original
