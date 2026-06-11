"""AC4: every FAIL line is followed by the exact fix command/instruction."""
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import doctor  # noqa: E402


def test_format_fail_includes_fix_line():
    r = doctor.CheckResult(
        name="DB_PATH writable", ok=False, fix="chmod u+w ./commander.db", detail=""
    )
    lines = doctor.format_result(r)
    assert lines[0].startswith("[FAIL]")
    joined = "\n".join(lines)
    assert "fix:" in joined
    assert "chmod u+w ./commander.db" in joined


def test_format_pass_has_no_fix_line():
    r = doctor.CheckResult(name="DB_PATH writable", ok=True, fix="", detail="ok")
    lines = doctor.format_result(r)
    assert not any("fix:" in ln for ln in lines)


def _fail(result):
    assert not result.ok
    assert result.fix.strip(), f"FAIL result for '{result.name}' has no fix"


def test_each_check_provides_a_fix_when_failing():
    # Force every check into its failure branch and confirm a non-empty fix.
    _fail(doctor.check_claude_cli(which=lambda _n: None))
    _fail(doctor.check_gh_cli(which=lambda _n: None))

    def _empty_git(cmd, timeout=8):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    _fail(doctor.check_git_identity(runner=_empty_git))
    _fail(doctor.check_venv_packages(venv_dir=str(REPO_ROOT / "does-not-exist-venv")))
    _fail(doctor.check_db_path_writable(db_path=""))


def test_runtime_summary_lists_each_failure_with_fix(capsys, monkeypatch):
    crafted = [
        doctor.CheckResult("check A", False, "run fix-a", ""),
        doctor.CheckResult("check B", True, "", "ok"),
    ]
    monkeypatch.setattr(doctor, "run_all_checks", lambda: crafted)
    doctor.main([])
    out = capsys.readouterr().out
    assert "run fix-a" in out
