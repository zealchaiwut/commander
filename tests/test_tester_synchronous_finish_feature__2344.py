"""Independent tester-authored verification for issue #2344.

Issue: the tester agent must treat `finish_feature.py` as a synchronous,
blocking call and must not assume background-job/notification semantics
(e.g. ScheduleWakeup) that only exist in interactive/loop sessions. A prior
dispatch run exited believing it would be "notified later" while the merge
never landed, and `dispatch_runner.py`'s verify() had to catch the stall.

AC coverage:
  AC1 - tester.md's finish_feature.py invocation is explicit that the call
        is synchronous / blocking, and the tester observes its exit code
        before reporting.
  AC2 - the prompt scopes background/notification tooling (ScheduleWakeup)
        to interactive/loop sessions only, never headless dispatch.
  AC3 - the final-report mapping never claims success without the exit
        code confirming the merge landed.
  AC4 - regression/repro: a slow finish_feature.py must not let the
        tester's turn end before the exit code is observed.

AC1/AC3/AC4 are exercised behaviorally: the literal bash block documented
in tester.md's Step 10 is extracted and actually run (against a stub
finish_feature.py), so a future edit that reintroduces backgrounding or
loosens the exit-code gate fails this suite. AC2 is a natural-language
scoping instruction consumed by an LLM, not executable code — there is no
process to invoke ScheduleWakeup against a markdown prompt — so it is
verified with a targeted text assertion, consistent with the merged
precedent for agent-definition tickets in
tests/test_agent_definition_stale_paths__2053.py.
"""
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
TESTER_MD = REPO_ROOT / ".claude" / "agents" / "tester.md"


def _step10_bash_blocks() -> list:
    """Every fenced bash block inside the 'Step 10 — Promote to UAT' section."""
    text = TESTER_MD.read_text()
    start = text.index("### Step 10 — Promote to UAT")
    end = text.index("### Step 11", start)
    section = text[start:end]
    blocks = []
    i = 0
    while True:
        open_idx = section.find("```bash", i)
        if open_idx == -1:
            break
        body_start = open_idx + len("```bash")
        close_idx = section.index("```", body_start)
        blocks.append(section[body_start:close_idx])
        i = close_idx + 3
    return blocks


def _merge_block() -> str:
    for block in _step10_bash_blocks():
        if "finish_feature.py --issue" in block:
            return block
    raise AssertionError("no finish_feature.py invocation found in Step 10")


def _write_stub(scripts_dir: Path) -> None:
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "finish_feature.py").write_text(
        "import os, sys, time\n"
        "time.sleep(float(os.environ.get('STUB_SLEEP', '0')))\n"
        "sys.exit(int(os.environ.get('STUB_EXIT', '0')))\n"
    )


def _exec_merge_block(main_repo: Path, exit_code: int, sleep_s: float):
    _write_stub(main_repo / "scripts")
    block = _merge_block().replace("<N>", "88888")
    script = f'export MAIN_REPO="{main_repo}"\n{block}\n'
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "STUB_EXIT": str(exit_code),
        "STUB_SLEEP": str(sleep_s),
    }
    t0 = time.monotonic()
    proc = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env, timeout=30
    )
    return proc, time.monotonic() - t0


# --- AC1 / AC4: the documented call actually blocks on a slow subprocess ---


def test_merge_block_blocks_until_slow_process_exits():
    with tempfile.TemporaryDirectory() as td:
        proc, elapsed = _exec_merge_block(Path(td), exit_code=0, sleep_s=2.0)
    assert elapsed >= 1.9, (
        f"Step 10's merge block returned after {elapsed:.2f}s despite a 2s "
        "sleep in finish_feature.py -- the documented procedure does not "
        "actually wait for the process to exit"
    )
    assert proc.returncode == 0


def test_merge_block_has_no_async_or_background_constructs():
    block = _merge_block()
    forbidden = ["&\n", " &$", "nohup", "disown", "run_in_background", "ScheduleWakeup"]
    for token in forbidden:
        assert token not in block, (
            f"Step 10's finish_feature.py invocation contains {token!r}, which "
            "would let the shell move on before the merge completes"
        )


# --- AC3: reporting is gated on the real exit code, both success and failure ---


@pytest.mark.parametrize("exit_code", [0, 1, 2, 42])
def test_exit_code_is_captured_faithfully_for_every_outcome(exit_code):
    with tempfile.TemporaryDirectory() as td:
        proc, _ = _exec_merge_block(Path(td), exit_code=exit_code, sleep_s=0.0)
    assert f"exit code: {exit_code}" in proc.stdout


def test_nonzero_exit_produces_a_do_not_report_promoted_warning():
    with tempfile.TemporaryDirectory() as td:
        proc, _ = _exec_merge_block(Path(td), exit_code=1, sleep_s=0.0)
    assert "Do NOT report as promoted" in proc.stderr


def test_zero_exit_produces_no_failure_warning():
    with tempfile.TemporaryDirectory() as td:
        proc, _ = _exec_merge_block(Path(td), exit_code=0, sleep_s=0.0)
    assert "ERROR" not in proc.stderr


def test_post_block_prose_maps_exit_code_to_merge_executed_field():
    text = TESTER_MD.read_text()
    idx = text.index("After this block, check `FINISH_EXIT_CODE`")
    mapping = text[idx : idx + 400]
    assert "Merge executed: yes" in mapping
    assert "Merge executed: no" in mapping
    assert "blocked" in mapping


# --- AC2: background/notification tooling is explicitly scoped away from headless dispatch ---


def test_scopes_schedule_wakeup_to_interactive_sessions_not_headless_dispatch():
    text = TESTER_MD.read_text()
    assert "SYNCHRONOUS CALL" in text
    sync_idx = text.index("SYNCHRONOUS CALL")
    sync_section = text[sync_idx : sync_idx + 1500]
    assert "ScheduleWakeup" in sync_section
    assert "headless" in sync_section.lower()
    assert "interactive" in sync_section.lower() or "loop session" in sync_section.lower()

    notes_idx = text.index("Headless dispatch has no notification mechanism")
    notes_section = text[notes_idx : notes_idx + 900]
    assert "ScheduleWakeup" in notes_section
    assert "2344" in notes_section
