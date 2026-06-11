"""AC7 / UAT step 2: claude missing from service PATH reports exactly that
failure with its fix, while all other checks still run."""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import doctor  # noqa: E402


def test_claude_missing_reports_that_failure_with_fix():
    r = doctor.check_claude_cli(which=lambda _n: None)
    assert not r.ok
    assert "claude" in r.name.lower()
    # Fix names the concrete remediation (PATH / launchd install).
    assert "PATH" in r.fix or "install_launchd" in r.fix.lower() or "path" in r.fix.lower()


def test_claude_present_but_probe_fails_reports_with_fix():
    import subprocess

    def _bad_probe(cmd, timeout=8):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

    r = doctor.check_claude_cli(which=lambda _n: "/usr/local/bin/claude", runner=_bad_probe)
    assert not r.ok
    assert r.fix.strip()


def test_other_checks_still_run_when_claude_missing(monkeypatch):
    # Even with claude failing, run_all_checks returns all seven results.
    monkeypatch.setattr(
        doctor,
        "CHECKS",
        [lambda: doctor.CheckResult("claude CLI reachable", False, "add claude to PATH", "")]
        + doctor.CHECKS[1:],
    )
    results = doctor.run_all_checks()
    assert len(results) == 7
    names = [r.name for r in results]
    assert "DB_PATH writable" in names  # a later check still executed


def test_claude_missing_makes_main_exit_nonzero(capsys, monkeypatch):
    crafted = [doctor.CheckResult("claude CLI reachable", False, "add claude dir to plist PATH", "")]
    crafted += [doctor.CheckResult(f"check {i}", True, "", "") for i in range(6)]
    monkeypatch.setattr(doctor, "run_all_checks", lambda: crafted)
    rc = doctor.main([])
    assert rc != 0
    out = capsys.readouterr().out
    assert "claude CLI reachable" in out
    assert "add claude dir to plist PATH" in out
