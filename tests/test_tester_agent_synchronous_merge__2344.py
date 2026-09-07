"""Tests for issue #2344 — tester agent must block on finish_feature.py's exit
code and never assume background/notification semantics in headless dispatch.

AC coverage:
  AC1 — .claude/agents/tester.md's finish_feature.py invocation is explicit
        that the call is synchronous; the tester must block on it and observe
        its exit code before reporting completion.
  AC2 — the prompt makes clear that background/notification tooling (e.g.
        ScheduleWakeup) applies only to interactive/loop sessions, never to
        headless dispatch, and must never defer merge verification.
  AC3 — the final report does not claim success until the exit code (and/or
        ticket label) confirms the merge landed.
  AC4 — regression/repro: simulate finish_feature.py running slowly and
        assert the tester's turn does not end before the exit code is
        observed.

AC1, AC3, and AC4 are verified behaviorally: the actual bash snippet is
extracted verbatim from tester.md and executed against a stub
finish_feature.py, so these tests fail if the documented procedure is ever
edited into something that no longer blocks or no longer gates on the exit
code. AC2 is a natural-language instruction with no executable behavior to
drive (there is no in-process way to invoke the ScheduleWakeup tool against a
prompt); it is checked via targeted text assertions, consistent with the
existing precedent for agent-definition tickets (see
tests/test_agent_definition_stale_paths__2053.py).
"""
import re
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
TESTER_MD = REPO_ROOT / ".claude" / "agents" / "tester.md"

_CALL_MARKER = 'cd "$MAIN_REPO" && python3 scripts/finish_feature.py'


def _extract_synchronous_block() -> str:
    """Pull the literal bash snippet (issue #2344) out of tester.md."""
    text = TESTER_MD.read_text()
    start = text.index(_CALL_MARKER)
    end = text.index("\n```", start)
    return text[start:end]


def _stub_finish_feature(tmp_path: Path) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "finish_feature.py").write_text(
        "import os, sys, time\n"
        "time.sleep(float(os.environ.get('STUB_SLEEP', '0')))\n"
        "sys.exit(int(os.environ.get('STUB_EXIT', '0')))\n"
    )


def _run_block(tmp_path: Path, exit_code: int, sleep_s: float = 0.0):
    """Execute the extracted bash block against the stub, as the tester would."""
    _stub_finish_feature(tmp_path)
    block = _extract_synchronous_block().replace("<N>", "99999")
    script = f'export MAIN_REPO="{tmp_path}"\n{block}\n'
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "STUB_EXIT": str(exit_code),
        "STUB_SLEEP": str(sleep_s),
    }
    start = time.monotonic()
    proc = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    elapsed = time.monotonic() - start
    return proc, elapsed


# AC1 + AC4 — the block must actually block until the (slow) process exits,
# and must have the real exit code in hand once it resumes.


def test_synchronous_block_waits_for_slow_finish_feature_and_captures_exit_code():
    with tempfile.TemporaryDirectory() as td:
        proc, elapsed = _run_block(Path(td), exit_code=0, sleep_s=1.5)
    # AC4: a slow finish_feature.py (1.5s sleep) must not let the block finish early.
    assert elapsed >= 1.4, (
        f"extracted block returned in {elapsed:.2f}s despite a 1.5s sleep in "
        "finish_feature.py — it is not actually blocking on the call"
    )
    # AC1: the real exit code (0) must be observed and echoed.
    assert "finish_feature.py exit code: 0" in proc.stdout
    assert proc.returncode == 0


def test_synchronous_block_does_not_background_the_call():
    # AC1: the documented invocation itself must not use backgrounding syntax
    # (a trailing '&', 'run_in_background', 'nohup', or 'disown') that would
    # let the shell continue before finish_feature.py exits.
    block = _extract_synchronous_block()
    call_line = block.splitlines()[0]
    assert call_line.rstrip().endswith("<N>"), f"unexpected call line: {call_line!r}"
    for token in ("run_in_background", "nohup", "disown", "&\n", " & "):
        assert token not in block, f"synchronous block contains backgrounding token {token!r}"


# AC3 — the block's own branching must reflect the actual exit code, not an
# assumption; the report language is only correct if this holds.


@pytest.mark.parametrize(
    "exit_code,expect_error",
    [(0, False), (1, True), (17, True)],
)
def test_block_gates_error_message_on_actual_exit_code(exit_code, expect_error):
    with tempfile.TemporaryDirectory() as td:
        proc, _ = _run_block(Path(td), exit_code=exit_code, sleep_s=0.0)
    assert f"finish_feature.py exit code: {exit_code}" in proc.stdout
    if expect_error:
        assert "ERROR: finish_feature.py failed" in proc.stderr
        assert "Do NOT report as promoted" in proc.stderr
    else:
        assert "ERROR: finish_feature.py failed" not in proc.stderr


def test_report_mapping_conditions_merge_executed_on_exit_code():
    # AC3: the doc's post-block instructions must map exit code 0 -> "yes"
    # and non-zero -> "no ... exited $FINISH_EXIT_CODE", not an unconditional
    # success claim.
    text = TESTER_MD.read_text()
    idx = text.index("After this block, check `FINISH_EXIT_CODE`")
    mapping = text[idx: idx + 400]
    assert "`0` → merge landed" in mapping or "0` " in mapping
    assert "Merge executed: yes" in mapping
    assert "Merge executed: no" in mapping
    assert "blocked" in mapping


# AC2 — natural-language scoping of background/notification tooling to
# interactive sessions only. No executable behavior exists to drive here
# (ScheduleWakeup is a harness tool, not invocable in-process), so this is a
# targeted text check on the specific claim required by the AC.


def test_notification_semantics_scoped_to_interactive_sessions_only():
    text = TESTER_MD.read_text()
    assert "ScheduleWakeup" in text, "tester.md must name ScheduleWakeup explicitly (issue #2344)"

    # Must appear near the synchronous-call instructions in Step 10.
    sync_idx = text.index("SYNCHRONOUS CALL")
    sync_section = text[sync_idx: sync_idx + 1200]
    assert "ScheduleWakeup" in sync_section
    assert "interactive" in sync_section.lower() or "loop session" in sync_section.lower()
    assert "headless" in sync_section.lower()

    # Must also appear in the standalone notes section describing headless dispatch.
    notes_idx = text.index("Headless dispatch has no notification mechanism")
    notes_section = text[notes_idx: notes_idx + 900]
    assert "headless" in notes_section.lower()
    assert "interactive" in notes_section.lower()
    assert "2344" in notes_section


def test_headless_dispatch_note_forbids_deferring_verification():
    text = TESTER_MD.read_text()
    notes_idx = text.index("Headless dispatch has no notification mechanism")
    notes_section = text[notes_idx: notes_idx + 700]
    assert re.search(r"before.{0,20}(reports|resumes|continues)", notes_section, re.IGNORECASE) or \
        "must complete synchronously" in notes_section
    assert "silent failure" in notes_section.lower()
