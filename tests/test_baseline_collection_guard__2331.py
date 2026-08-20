"""AC tests for issue #2331: the baseline-delta gate must refuse empty measurements.

The defect: three test modules imported a symbol removed during the 2026-08
shrink, so `pytest tests/ -q` aborted during collection and reported
`0 passed / 0 failed`. Both `record_test_baseline.py` and `finish_feature.py`
measure the suite the same way, so the recorded baseline was 0 and every branch
also measured 0 — the delta check compared 0 to 0 and allowed every merge.

These tests feed real pytest transcripts through the real functions and assert
the refusal. No source-text assertions (CLAUDE.md #1746), no HTTP.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(REPO_ROOT), str(REPO_ROOT / "apps" / "dashboard")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.sprint_manager.merge_baseline import (  # noqa: E402
    Baseline,
    check_against_baseline,
    collection_failed,
    measurement_is_empty,
)

# The transcript this ticket was filed from, trimmed to the shape that matters.
COLLECTION_ERROR_OUTPUT = """\
==================================== ERRORS ====================================
_______ ERROR collecting tests/test_1845__remove_dead_milestone_fetch.py _______
ImportError while importing test module 'tests/test_1845__remove_dead_milestone_fetch.py'.
E   ImportError: cannot import name '_enrich_home_artifact' from 'routers.brief'
=========================== short test summary info ============================
ERROR tests/test_1845__remove_dead_milestone_fetch.py
ERROR tests/test_1845__tester_verification.py
ERROR tests/test_enrich_home_artifact__1798.py
!!!!!!!!!!!!!!!!!!! Interrupted: 3 errors during collection !!!!!!!!!!!!!!!!!!!!
2 skipped, 1 deselected, 2 warnings, 3 errors in 2.25s
"""

HEALTHY_OUTPUT = """\
=========================== short test summary info ============================
FAILED tests/test_alpha.py::test_one - AssertionError
1 failed, 40 passed, 2 skipped in 12.31s
"""


# ---------------------------------------------------------------------------
# AC: a collection abort is recognised as such, not read as "zero failures"
# ---------------------------------------------------------------------------

def test_collection_failure_detected_in_real_transcript():
    assert collection_failed(COLLECTION_ERROR_OUTPUT) is True


def test_healthy_run_is_not_flagged_as_a_collection_failure():
    assert collection_failed(HEALTHY_OUTPUT) is False


def test_empty_measurement_predicate():
    assert measurement_is_empty(0, 0) is True
    assert measurement_is_empty(0, 3) is False
    assert measurement_is_empty(40, 0) is False


# ---------------------------------------------------------------------------
# AC: the merge check refuses when the branch run collected nothing
# ---------------------------------------------------------------------------

def _baseline(**kw) -> Baseline:
    defaults = dict(project="zealchaiwut/commander", failed=25, passed=900, skipped=2)
    defaults.update(kw)
    return Baseline(**defaults)


def test_merge_refused_when_branch_run_aborted_at_collection():
    """The exact regression: 0-vs-0 must not be read as 'no new failures'."""
    check = check_against_baseline(
        failed_now=0,
        failing_test_ids_now=[],
        baseline=_baseline(),
        passed_now=0,
        collection_error=True,
    )
    assert check.allowed is False
    assert "collection" in check.reason.lower()


def test_merge_refused_when_branch_run_executed_nothing():
    """Empty run without an explicit collection error is refused too."""
    check = check_against_baseline(
        failed_now=0,
        failing_test_ids_now=[],
        baseline=_baseline(),
        passed_now=0,
    )
    assert check.allowed is False
    assert "no tests" in check.reason.lower()


def test_merge_refused_when_the_baseline_itself_measured_nothing():
    """A 0/0 baseline is a recording failure, not a clean suite."""
    check = check_against_baseline(
        failed_now=3,
        failing_test_ids_now=["tests/test_x.py::test_y"],
        baseline=_baseline(failed=0, passed=0, skipped=2),
        passed_now=900,
    )
    assert check.allowed is False
    assert "baseline" in check.reason.lower()


def test_healthy_run_against_healthy_baseline_still_passes():
    """The guards must not break the normal allow path."""
    check = check_against_baseline(
        failed_now=25,
        failing_test_ids_now=[],
        baseline=_baseline(),
        passed_now=900,
    )
    assert check.allowed is True


def test_existing_callers_without_passed_now_are_unaffected():
    """passed_now defaults to 'not reported' so older call sites keep working."""
    check = check_against_baseline(
        failed_now=25,
        failing_test_ids_now=[],
        baseline=_baseline(),
    )
    assert check.allowed is True


# ---------------------------------------------------------------------------
# AC: the baseline carries the scope it was measured with
# ---------------------------------------------------------------------------

def test_baseline_round_trips_pytest_args_and_collected():
    b = _baseline(pytest_args="tests/ -q")
    restored = Baseline.from_dict(b.to_dict())
    assert restored.pytest_args == "tests/ -q"
    assert restored.collected == 925


def test_baseline_from_legacy_dict_without_pytest_args():
    restored = Baseline.from_dict({"project": "o/r", "fail": 1, "pass": 2})
    assert restored.pytest_args == ""
    assert restored.collected == 3


# ---------------------------------------------------------------------------
# AC: the recorder refuses to write a baseline from a suite that did not run
# ---------------------------------------------------------------------------

@pytest.fixture()
def broken_tree(tmp_path: Path) -> Path:
    """A minimal repo whose test module fails to import."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_broken.py").write_text(
        "from nonexistent_module_2331 import missing_symbol\n\n"
        "def test_never_runs():\n    assert True\n",
        encoding="utf-8",
    )
    return tmp_path


def test_recorder_refuses_a_tree_that_fails_to_collect(broken_tree: Path, tmp_path: Path):
    """Regression guard: the recorder silently wrote a 0/0 baseline before."""
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "record_test_baseline.py"),
            "--repo", "zealchaiwut/does-not-exist-2331",
            "--repo-root", str(broken_tree),
            "--dry-run",
        ],
        capture_output=True, text=True, timeout=180, cwd=str(REPO_ROOT),
    )
    assert proc.returncode != 0, (
        "recorder exited 0 on a tree that collects nothing — this is the #2331 defect\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    combined = proc.stdout + proc.stderr
    assert "collection" in combined.lower() or "no tests" in combined.lower()


def test_recorder_writes_nothing_for_a_refused_measurement(broken_tree: Path):
    """The refusal must precede the write, not follow it."""
    target = REPO_ROOT / ".commander" / "baselines" / "zealchaiwut-does-not-exist-2331.json"
    existed_before = target.exists()
    subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "record_test_baseline.py"),
            "--repo", "zealchaiwut/does-not-exist-2331",
            "--repo-root", str(broken_tree),
        ],
        capture_output=True, text=True, timeout=180, cwd=str(REPO_ROOT),
    )
    assert target.exists() == existed_before, "a refused measurement was still written"
