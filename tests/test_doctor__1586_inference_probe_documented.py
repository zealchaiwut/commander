"""Issue #1586 — doctor.py Claude auth probe makes a real inference round-trip.

Each test is anchored to one acceptance criterion of #1586. The Gate-2 Claude
auth probe (`claude -p "" --output-format json`, inherited from #868) issues a
real, minimal model inference call and consumes subscription quota on every
`doctor.py` run; this ticket requires that cost be documented for operators
without altering Gate-2 pass/fail behaviour.
"""
import inspect
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import doctor  # noqa: E402

DOCTOR_PATH = REPO_ROOT / "scripts" / "doctor.py"


def _ok_proc(cmd, timeout=8):
    """A runner that makes every `claude` invocation succeed (returncode 0)."""
    return subprocess.CompletedProcess(cmd, 0, stdout="1.0.0", stderr="")


# ── AC1: a comment/log near the probe states it makes a real (minimal) ────────
#         inference call and consumes subscription quota on every run.
def test_ac1_source_documents_inference_call_and_quota_cost():
    src = inspect.getsource(doctor.check_claude_cli)
    low = src.lower()
    assert "inference" in low, "Gate-2 probe must be documented as making an inference call"
    assert "quota" in low, "Gate-2 probe must document that it consumes subscription quota"


# ── AC2: the operator-visible Gate-2 output indicates a model inference ───────
#         check, not a silent auth-only check.
def test_ac2_status_line_indicates_inference_check():
    result = doctor.check_claude_cli(
        which=lambda _n: "/usr/local/bin/claude", runner=_ok_proc
    )
    assert result.ok
    # The name renders into the printed [PASS]/[FAIL] status line.
    line = doctor.format_result(result)[0]
    assert "inference" in result.name.lower(), (
        f"check name must signal a model inference check, got: {result.name!r}"
    )
    assert "inference" in line.lower()


# ── AC3: the existing `claude -p "" --output-format json` invocation is ───────
#         preserved unchanged — no functional regression to pass/fail logic.
def test_ac3_probe_invocation_is_preserved():
    calls = []

    def _recording(cmd, timeout=8):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="1.0.0", stderr="")

    doctor.check_claude_cli(which=lambda _n: "/usr/local/bin/claude", runner=_recording)
    assert ["claude", "-p", "", "--output-format", "json"] in calls, (
        f"Gate-2 must invoke the auth probe unchanged; saw: {calls!r}"
    )


def test_ac3_passfail_logic_unchanged():
    # Probe succeeds → PASS.
    good = doctor.check_claude_cli(
        which=lambda _n: "/usr/local/bin/claude", runner=_ok_proc
    )
    assert good.ok

    # Probe returns non-zero (auth failure) → FAIL with a fix.
    def _auth_fail(cmd, timeout=8):
        # --version succeeds (reachable); the -p auth probe fails.
        if "-p" in cmd:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="not logged in")
        return subprocess.CompletedProcess(cmd, 0, stdout="1.0.0", stderr="")

    bad = doctor.check_claude_cli(
        which=lambda _n: "/usr/local/bin/claude", runner=_auth_fail
    )
    assert not bad.ok
    assert bad.fix.strip()


# ── AC4: no documented auth-only `claude` flag exists, so Gate-2 stays on ──────
#         the `-p ""` probe and the quota-consumption note remains present.
def test_ac4_probe_not_switched_to_authonly_flag_and_note_remains():
    src = inspect.getsource(doctor.check_claude_cli)
    # Still uses the inference probe, not some (nonexistent) auth-only flag.
    assert '"-p", ""' in src or "'-p', ''" in src
    # The quota note is still present because no auth-only flag is available.
    assert "quota" in src.lower()


# ── AC5: `doctor.py --help` / usage text notes a minimal Claude API call ──────
def test_ac5_help_text_mentions_minimal_claude_call():
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.suppress(SystemExit), contextlib.redirect_stdout(buf):
        doctor.main(["--help"])
    help_text = buf.getvalue().lower()
    assert "claude" in help_text
    assert "inference" in help_text or "api call" in help_text


def test_ac5_module_usage_doc_mentions_minimal_call():
    text = DOCTOR_PATH.read_text().lower()
    assert "minimal" in text and ("inference" in text or "api call" in text)
