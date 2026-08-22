"""AC tests for issue #2336: the baseline-delta gate is blind to ERROR-status tests.

No live HTTP: everything here is pure computation over in-memory values, real
pytest-shaped transcript text, and a tmp_path directory.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from services.sprint_manager.merge_baseline import (  # noqa: E402
    Baseline,
    check_against_baseline,
    load_baseline,
    parse_errored_test_ids,
    parse_failed_test_ids,
    save_baseline,
)
from services.sprint_manager.suite_health_gate import _parse_pytest_output  # noqa: E402


# A real transcript carrying both FAILED and ERROR summary lines, including a
# collection-error line naming no test id (excluded from parse_errored_test_ids)
# and a nested subprocess run whose own summary line must not be picked up.
MIXED_TRANSCRIPT = """\
FAILED tests/test_alpha.py::test_one - AssertionError: nope
ERROR tests/test_gamma.py::test_needs_fixture - fixture 'db' not found
ERROR tests/test_delta.py - collection error
ERROR collecting tests/test_epsilon.py
some captured subprocess output: 5 passed, 999 failed in 0.10s
2377 failed, 7257 passed, 351 skipped, 469 errors in 741.96s (0:12:21)
"""


def _baseline_with_errors(errored: int, failed: int = 0, error_ids=None, failed_ids=None) -> Baseline:
    return Baseline(
        project="zealchaiwut/demo",
        failed=failed,
        passed=100,
        failed_test_ids=list(failed_ids or []),
        errored=errored,
        errored_test_ids=list(error_ids if error_ids is not None else []),
        recorded_at="2026-08-19T00:00:00+00:00",
        recorded_from_ref="develop",
    )


# --- AC1: _parse_pytest_output returns the error count from the summary line ---

def test_parse_pytest_output_reports_error_count_from_summary_line():
    counts = _parse_pytest_output(MIXED_TRANSCRIPT)
    # Must read the real summary line's counts, not the nested subprocess's.
    assert counts.passed == 7257
    assert counts.failed == 2377
    assert counts.errors == 469


def test_parse_pytest_output_still_unpacks_as_three_tuple():
    passed, failed, skipped = _parse_pytest_output(MIXED_TRANSCRIPT)
    assert (passed, failed, skipped) == (7257, 2377, 351)


def test_parse_pytest_output_zero_errors_when_none_reported():
    output = "1 failed, 5 passed in 0.30s\n"
    counts = _parse_pytest_output(output)
    assert counts.errors == 0


# --- AC2: Baseline records error count + ids, and round-trips them -----------

def test_baseline_roundtrip_carries_error_fields(tmp_path):
    baseline = _baseline_with_errors(
        errored=2,
        error_ids=["tests/test_gamma.py::test_needs_fixture", "tests/test_delta.py"],
    )
    save_baseline(baseline, tmp_path)
    loaded = load_baseline("zealchaiwut/demo", tmp_path)
    assert loaded.errored == 2
    assert loaded.errored_test_ids == [
        "tests/test_gamma.py::test_needs_fixture",
        "tests/test_delta.py",
    ]


def test_pre_2336_baseline_without_error_fields_still_loads(tmp_path):
    # Simulate a baseline recorded before this change: no "errored"/"errored_test_ids" keys.
    import json

    old_baseline_json = {
        "project": "zealchaiwut/demo",
        "fail": 5,
        "pass": 100,
        "skip": 2,
        "failed_test_ids": ["tests/test_a.py::test_x"],
        "recorded_at": "2026-01-01T00:00:00+00:00",
        "recorded_from_ref": "develop",
        "pytest_args": "tests/ -q",
    }
    path = tmp_path / "baselines" / "zealchaiwut-demo.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(old_baseline_json), encoding="utf-8")

    loaded = load_baseline("zealchaiwut/demo", tmp_path)
    assert loaded is not None
    assert loaded.failed == 5
    assert loaded.errored == 0
    assert loaded.errored_test_ids is None  # sentinel: "not recorded", not "recorded empty"


# --- AC3: check_against_baseline refuses on error-count rise -----------------

def test_error_count_rise_is_refused():
    check = check_against_baseline(
        failed_now=0,
        failing_test_ids_now=[],
        baseline=_baseline_with_errors(errored=1, error_ids=["tests/test_x.py::test_a"]),
        errored_now=2,
        erroring_test_ids_now=["tests/test_x.py::test_a", "tests/test_y.py::test_b"],
    )
    assert check.allowed is False
    assert "error" in check.reason.lower()
    assert check.errored_now == 2
    assert check.errored_baseline == 1


# --- AC3 (swap case): count unchanged but a different test now errors --------

def test_error_swap_same_count_new_test_erroring_is_refused():
    check = check_against_baseline(
        failed_now=0,
        failing_test_ids_now=[],
        baseline=_baseline_with_errors(
            errored=1, error_ids=["tests/test_old.py::test_erroring"]
        ),
        errored_now=1,
        erroring_test_ids_now=["tests/test_new.py::test_erroring"],
    )
    assert check.allowed is False
    assert check.new_erroring_tests == ["tests/test_new.py::test_erroring"]


def test_preexisting_errors_do_not_block():
    check = check_against_baseline(
        failed_now=0,
        failing_test_ids_now=[],
        baseline=_baseline_with_errors(
            errored=1, error_ids=["tests/test_old.py::test_erroring"]
        ),
        errored_now=1,
        erroring_test_ids_now=["tests/test_old.py::test_erroring"],
    )
    assert check.allowed is True


def test_fixture_break_hole_this_ticket_closes():
    """The exact hole from the ticket: errors rise, failures unchanged.

    Before #2336 this passed because check_against_baseline never looked at
    errored_now/erroring_test_ids_now at all.
    """
    baseline = _baseline_with_errors(errored=469, failed=2377, error_ids=[])
    check = check_against_baseline(
        failed_now=2377,  # unchanged
        failing_test_ids_now=[],
        baseline=baseline,
        errored_now=569,  # a broken fixture turned 100 passing tests into ERROR
        erroring_test_ids_now=[],
    )
    assert check.allowed is False
    assert "error" in check.reason.lower()


# --- AC4: refusal message distinguishes errors from failures -----------------

def test_refusal_summary_names_error_rise_not_failure_rise():
    check = check_against_baseline(
        failed_now=5,
        failing_test_ids_now=[],
        baseline=_baseline_with_errors(errored=1, failed=5, error_ids=["tests/test_x.py::test_a"]),
        errored_now=3,
        erroring_test_ids_now=["tests/test_x.py::test_a", "tests/test_y.py::test_b", "tests/test_z.py::test_c"],
    )
    summary = check.summary()
    assert "error count rose" in check.reason
    assert "failure count rose" not in check.reason
    assert "Erroring now: 3" in summary
    assert "Baseline: 1" in summary


def test_old_baseline_predating_error_tracking_skips_error_check():
    # errored_test_ids=None means "not recorded" — old baselines behave as before.
    baseline = _baseline_with_errors(errored=0, failed=0)
    baseline.errored_test_ids = None
    check = check_against_baseline(
        failed_now=0,
        failing_test_ids_now=[],
        baseline=baseline,
        errored_now=50,  # would refuse if the error check ran
        erroring_test_ids_now=["tests/test_new.py::test_broken"],
    )
    assert check.allowed is True


# --- AC6: real transcript through the parser + check, fixture-break case -----

def test_end_to_end_transcript_through_parser_and_check_refuses_on_new_errors():
    counts = _parse_pytest_output(MIXED_TRANSCRIPT)
    error_ids = parse_errored_test_ids(MIXED_TRANSCRIPT)
    failed_ids = parse_failed_test_ids(MIXED_TRANSCRIPT)

    # Collection-error progress line names no test id and must not appear.
    assert "collecting" not in " ".join(error_ids)
    assert error_ids == sorted(error_ids)

    baseline = _baseline_with_errors(
        errored=counts.errors - 1,  # baseline had one fewer error than this run
        failed=counts.failed,
        error_ids=[],
        failed_ids=failed_ids,
    )

    check = check_against_baseline(
        failed_now=counts.failed,
        failing_test_ids_now=failed_ids,
        baseline=baseline,
        passed_now=counts.passed,
        errored_now=counts.errors,
        erroring_test_ids_now=error_ids,
    )
    assert check.allowed is False
    assert check.errored_now == counts.errors
    assert "error" in check.reason.lower()
