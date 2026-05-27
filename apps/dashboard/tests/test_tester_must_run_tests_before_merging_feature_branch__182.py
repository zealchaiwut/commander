"""Tests for issue #182: Tester Must Run Tests Before Merging Feature Branch"""
import os
import subprocess
import pytest


# UAT_BASE_URL not required — all ACs are verified via agent instruction file content,
# not HTTP endpoints. No server calls are made by any test in this file.
BASE_URL = os.environ.get("UAT_BASE_URL", "http://localhost:8001")


def _repo_root():
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True,
        cwd=os.path.dirname(os.path.abspath(__file__)),
    )
    if result.returncode != 0:
        raise RuntimeError(f"git rev-parse failed: {result.stderr.strip()}")
    return result.stdout.strip()


@pytest.fixture(scope="module")
def tester_md():
    path = os.path.join(_repo_root(), ".claude", "agents", "tester.md")
    assert os.path.isfile(path), f"tester.md not found at {path}"
    with open(path) as f:
        return f.read()


def test_tester_must_run_tests_before_merging_feature_branch__pytest_mandatory_before_finish_feature(tester_md):
    # AC1: pytest must be executed before calling finish_feature.py
    assert "MANDATORY" in tester_md, "tester.md must contain a MANDATORY directive for pytest"
    # The MANDATORY block must explicitly tie pytest execution to the finish_feature.py gate
    mandatory_idx = tester_md.find("MANDATORY")
    mandatory_block = tester_md[mandatory_idx:mandatory_idx + 300]
    assert "pytest" in mandatory_block, "MANDATORY directive must reference pytest"
    assert "finish_feature.py" in mandatory_block, \
        "MANDATORY directive must explicitly reference finish_feature.py as the guarded action"


def test_tester_must_run_tests_before_merging_feature_branch__non_zero_exit_blocks_merge_labels_blocked(tester_md):
    # AC2: non-zero pytest exit code → label blocked, no merge
    non_zero_idx = tester_md.find("non-zero")
    assert non_zero_idx != -1, "tester.md must describe non-zero exit code handling"
    segment = tester_md[non_zero_idx:non_zero_idx + 600]
    assert "blocked" in segment.lower(), "non-zero exit handling must move label to blocked"
    assert "Do not call `finish_feature.py`" in segment or "not merge" in segment.lower(), \
        "non-zero exit must prevent merge"


def test_tester_must_run_tests_before_merging_feature_branch__invocation_failure_blocks_merge(tester_md):
    # AC3: pytest invocation failure → label blocked, no merge
    invoc_idx = tester_md.find("cannot be invoked")
    assert invoc_idx != -1, "tester.md must describe behavior when pytest cannot be invoked"
    segment = tester_md[invoc_idx:invoc_idx + 500]
    assert "blocked" in segment.lower(), "invocation failure must move label to blocked"
    assert "Do not call `finish_feature.py`" in segment or "not merge" in segment.lower() or \
        "Stop here" in segment, "invocation failure must prevent merge"


def test_tester_must_run_tests_before_merging_feature_branch__report_has_pytest_output_section(tester_md):
    # AC4: test report template must include a ## Pytest Output section
    assert "## Pytest Output" in tester_md, \
        "report template must contain a '## Pytest Output' section"


def test_tester_must_run_tests_before_merging_feature_branch__report_states_exit_code_and_merge_decision(tester_md):
    # AC5: report template must include exit code line and merge-executed line
    assert "Pytest exit code:" in tester_md, \
        "report template must include 'Pytest exit code:' field"
    assert "Merge executed:" in tester_md, \
        "report template must include 'Merge executed:' field"


def test_tester_must_run_tests_before_merging_feature_branch__merge_only_when_exit_code_zero(tester_md):
    # AC6: finish_feature.py called only when PYTEST_EXIT_CODE is 0
    assert "PYTEST_EXIT_CODE" in tester_md, "tester.md must use PYTEST_EXIT_CODE variable"
    only_call_idx = tester_md.find("Only call `finish_feature.py`")
    assert only_call_idx != -1, \
        "tester.md must state 'Only call finish_feature.py' with the condition"
    segment = tester_md[only_call_idx:only_call_idx + 200]
    assert "`0`" in segment or "exit code 0" in segment.lower(), \
        "merge condition must require exit code 0"


def test_tester_must_run_tests_before_merging_feature_branch__instructions_explicitly_state_pytest_requirement(tester_md):
    # AC7: instructions must explicitly say "You must run pytest and confirm a green exit code before calling finish_feature.py"
    assert "You must run" in tester_md, \
        "tester.md must contain 'You must run' directive"
    assert "green exit code" in tester_md, \
        "tester.md must mention 'green exit code'"
    # Ensure finish_feature.py is referenced in the same mandatory instruction
    mandatory_section_start = tester_md.find("MANDATORY")
    mandatory_section = tester_md[mandatory_section_start:mandatory_section_start + 400]
    assert "finish_feature.py" in mandatory_section, \
        "MANDATORY section must reference finish_feature.py"
