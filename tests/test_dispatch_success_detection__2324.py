"""AC tests for agent success detection (issue #2324).

These cover the REAL judging path, which #2315 left untested — every test there
injects `spawn`, so `default_spawn` itself had zero coverage and shipped a bug
that reported ten passing steps for a run that did nothing.

Payloads below are modelled on genuine `claude -p --output-format json` output,
including the exact failure text observed in run 94914e4a8e47.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from services.sprint_manager.dispatch_runner import judge_agent_result  # noqa: E402

SUCCESS_ENVELOPE = json.dumps({
    "is_error": False,
    "subtype": "success",
    "result": "Implemented issue #80 and pushed feature/80-cache-post-image-bytes.",
    "type": "result",
})

ERROR_ENVELOPE = json.dumps({
    "is_error": True,
    "subtype": "error_during_execution",
    "result": "tool failure",
    "type": "result",
})

# The exact shape that fooled the runner: exit 0, no envelope, refusal text.
UNKNOWN_COMMAND_OUTPUT = "Unknown command: /coder. Did you mean /color?"


# --- The regression this ticket exists for --------------------------------

def test_unknown_command_with_exit_zero_is_a_failure():
    """Run 94914e4a8e47: rc=0, agent did nothing, runner said ok."""
    ok, detail = judge_agent_result(0, UNKNOWN_COMMAND_OUTPUT, "")
    assert ok is False
    assert "did not run the requested command" in detail


def test_unknown_command_variants_are_failures():
    for text in (
        "Unknown command: /tester",
        "No such command: /coder",
        "unknown command: /whatever. did you mean /color?",
    ):
        ok, _ = judge_agent_result(0, text, "")
        assert ok is False, f"should have failed: {text!r}"


# --- Envelope handling -----------------------------------------------------

def test_valid_success_envelope_passes():
    ok, detail = judge_agent_result(0, SUCCESS_ENVELOPE, "")
    assert ok is True
    assert "Implemented issue #80" in detail


def test_is_error_true_is_a_failure():
    ok, detail = judge_agent_result(0, ERROR_ENVELOPE, "")
    assert ok is False
    assert "is_error" in detail


def test_non_success_subtype_is_a_failure():
    payload = json.dumps({"is_error": False, "subtype": "error_max_turns", "result": "gave up"})
    ok, detail = judge_agent_result(0, payload, "")
    assert ok is False
    assert "error_max_turns" in detail


def test_envelope_is_read_from_the_last_json_line():
    """Agents emit progress lines before the result; the last object wins."""
    noisy = '{"type":"system","subtype":"init"}\n' + SUCCESS_ENVELOPE
    ok, _ = judge_agent_result(0, noisy, "")
    assert ok is True


# --- Unknown state must never resolve to success --------------------------

def test_unparseable_output_is_a_failure():
    ok, detail = judge_agent_result(0, "this is not json at all", "")
    assert ok is False
    assert "could not parse" in detail


def test_empty_output_is_a_failure():
    ok, detail = judge_agent_result(0, "", "")
    assert ok is False
    assert "no output" in detail


def test_truncated_json_is_a_failure():
    ok, _ = judge_agent_result(0, '{"is_error": false, "subty', "")
    assert ok is False


# --- Exit code still counts, belt and braces ------------------------------

def test_non_zero_exit_is_a_failure_even_with_a_success_envelope():
    ok, detail = judge_agent_result(1, SUCCESS_ENVELOPE, "")
    assert ok is False
    assert "exited 1" in detail


# --- The detail must be diagnosable without opening a log -----------------

def test_failure_detail_carries_the_agent_output():
    ok, detail = judge_agent_result(0, UNKNOWN_COMMAND_OUTPUT, "")
    assert ok is False
    assert "/coder" in detail, "operator must see what actually happened"


# --- default_spawn asks for the envelope it now depends on ---------------

def test_default_spawn_requests_json_output():
    src = (REPO_ROOT / "services" / "sprint_manager" / "dispatch_runner.py").read_text()
    assert '"--output-format", "json"' in src, (
        "success detection parses the JSON envelope, so the CLI must be asked for it"
    )


def test_default_spawn_does_not_judge_on_returncode_alone():
    """Guard against a regression back to `return proc.returncode == 0, tail`."""
    src = (REPO_ROOT / "services" / "sprint_manager" / "dispatch_runner.py").read_text()
    assert "return proc.returncode == 0" not in src
