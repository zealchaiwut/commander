"""AC5: exits 0 only when all checks pass; non-zero with a summary otherwise."""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import doctor  # noqa: E402


def test_exit_zero_when_all_pass(capsys, monkeypatch):
    all_pass = [
        doctor.CheckResult("a", True, "", ""),
        doctor.CheckResult("b", True, "", ""),
    ]
    monkeypatch.setattr(doctor, "run_all_checks", lambda: all_pass)
    rc = doctor.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "All 2 checks passed" in out


def test_exit_nonzero_with_summary_when_any_fail(capsys, monkeypatch):
    mixed = [
        doctor.CheckResult("a", True, "", ""),
        doctor.CheckResult("b", False, "do the fix", ""),
    ]
    monkeypatch.setattr(doctor, "run_all_checks", lambda: mixed)
    rc = doctor.main([])
    assert rc != 0
    out = capsys.readouterr().out
    assert "FAILED" in out
    assert "b" in out
    assert "do the fix" in out


def test_run_doctor_exit_code_field_matches():
    report = doctor.run_doctor()
    expected = 0 if report["ok"] else 1
    assert report["exit_code"] == expected
