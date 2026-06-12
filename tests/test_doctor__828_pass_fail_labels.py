"""AC2: each check prints a labelled [PASS]/[FAIL] line with the check name."""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import doctor  # noqa: E402


def test_pass_line_format():
    r = doctor.CheckResult(name="claude CLI reachable", ok=True, fix="", detail="")
    lines = doctor.format_result(r)
    assert lines[0] == "[PASS] claude CLI reachable"


def test_fail_line_format():
    r = doctor.CheckResult(
        name="gh not found on service PATH", ok=False, fix="install gh", detail=""
    )
    lines = doctor.format_result(r)
    assert lines[0] == "[FAIL] gh not found on service PATH"


def test_every_check_line_is_labelled(capsys):
    rc = doctor.main([])
    out = capsys.readouterr().out
    status_lines = [
        ln for ln in out.splitlines() if ln.startswith("[PASS] ") or ln.startswith("[FAIL] ")
    ]
    # All seven checks emit a labelled status line.
    assert len(status_lines) >= 7
    for ln in status_lines:
        # Label is immediately followed by a non-empty check name.
        name = ln.split("] ", 1)[1].strip()
        assert name, f"status line has no check name: {ln!r}"
    assert rc in (0, 1)
