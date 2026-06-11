"""UAT step 6: two independent failures both appear in the summary; only those
two are FAIL; script exits non-zero."""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import doctor  # noqa: E402


def test_two_independent_failures_both_summarised(capsys, monkeypatch):
    results = [
        doctor.CheckResult("claude CLI reachable", True, "", ""),
        doctor.CheckResult("gh authenticated", False, "gh auth login", ""),
        doctor.CheckResult("git identity configured", True, "", ""),
        doctor.CheckResult("venv and packages importable", False, "create the venv", ""),
        doctor.CheckResult("DB_PATH writable", True, "", ""),
    ]
    monkeypatch.setattr(doctor, "run_all_checks", lambda: results)
    rc = doctor.main([])
    out = capsys.readouterr().out

    assert rc != 0
    # Both failures named in the summary with their fixes.
    assert "gh authenticated" in out
    assert "gh auth login" in out
    assert "venv and packages importable" in out
    assert "create the venv" in out
    # Exactly two FAIL status lines.
    fail_lines = [ln for ln in out.splitlines() if ln.startswith("[FAIL] ")]
    assert len(fail_lines) == 2
    assert "2 check(s) FAILED" in out
