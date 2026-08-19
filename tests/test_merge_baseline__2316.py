"""AC tests for the baseline-delta merge check (issue #2316).

No live HTTP: everything here is pure computation over in-memory values and a
tmp_path directory.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from services.sprint_manager.merge_baseline import (  # noqa: E402
    Baseline,
    check_against_baseline,
    is_docs_only,
    load_baseline,
    parse_failed_test_ids,
    save_baseline,
)

PYTEST_OUTPUT = """\
FAILED tests/test_alpha.py::test_one - AssertionError: nope
FAILED tests/test_beta.py::test_two - ValueError
3 failed, 954 passed, 35 skipped in 67.31s
"""


def _baseline(failed: int, ids=None) -> Baseline:
    return Baseline(
        project="zealchaiwut/demo",
        failed=failed,
        failed_test_ids=list(ids or []),
        recorded_at="2026-08-19T00:00:00+00:00",
        recorded_from_ref="develop",
    )


# --- AC: a merge adding no new failures is allowed -------------------------

def test_no_new_failures_is_allowed():
    check = check_against_baseline(
        failed_now=75,
        failing_test_ids_now=["tests/test_a.py::test_x"],
        baseline=_baseline(75, ["tests/test_a.py::test_x"]),
    )
    assert check.allowed is True
    assert check.new_failing_tests == []


# --- AC: a merge adding one new failure is refused, naming the test --------

def test_new_failure_is_refused_and_names_the_test():
    check = check_against_baseline(
        failed_now=76,
        failing_test_ids_now=["tests/test_a.py::test_x", "tests/test_new.py::test_bad"],
        baseline=_baseline(75, ["tests/test_a.py::test_x"]),
    )
    assert check.allowed is False
    assert "tests/test_new.py::test_bad" in check.new_failing_tests
    # The refusal message must carry the name, not just a count (AC).
    assert "tests/test_new.py::test_bad" in check.summary()
    assert "76" in check.summary() and "75" in check.summary()


# --- AC: pre-existing failures never block on a non-green baseline ---------

def test_preexisting_failures_do_not_block():
    """A repo with a 75-failure baseline still merges when nothing got worse."""
    ids = [f"tests/test_{i}.py::test_it" for i in range(75)]
    check = check_against_baseline(
        failed_now=75,
        failing_test_ids_now=ids,
        baseline=_baseline(75, ids),
    )
    assert check.allowed is True


# --- The count-blind swap: same total, different test failing -------------

def test_swap_same_count_but_new_test_failing_is_refused():
    """One pre-existing failure fixed, one new failure appears: count unchanged.

    A pure count comparison would allow this. It must not.
    """
    check = check_against_baseline(
        failed_now=2,
        failing_test_ids_now=["tests/test_a.py::test_x", "tests/test_new.py::test_bad"],
        baseline=_baseline(2, ["tests/test_a.py::test_x", "tests/test_old.py::test_gone"]),
    )
    assert check.allowed is False
    assert check.new_failing_tests == ["tests/test_new.py::test_bad"]


def test_fixing_a_failure_without_adding_one_is_allowed():
    check = check_against_baseline(
        failed_now=1,
        failing_test_ids_now=["tests/test_a.py::test_x"],
        baseline=_baseline(2, ["tests/test_a.py::test_x", "tests/test_old.py::test_gone"]),
    )
    assert check.allowed is True


# --- AC: docs-only diffs skip the check -----------------------------------

@pytest.mark.parametrize(
    "files",
    [
        ["docs/architecture.md"],
        ["README.md", "docs/quickstart.md"],
        ["CHANGELOG.md", "LICENSE"],
    ],
)
def test_docs_only_diff_skips_the_check(files):
    check = check_against_baseline(
        failed_now=999,
        failing_test_ids_now=["tests/test_boom.py::test_boom"],
        baseline=_baseline(0),
        changed_files=files,
    )
    assert check.allowed is True
    assert check.skipped_check is True


@pytest.mark.parametrize(
    "files",
    [
        ["docs/architecture.md", "services/sprint_manager/foo.py"],
        ["scripts/finish_feature.py"],
        ["static/app.js"],
    ],
)
def test_code_in_diff_means_check_runs(files):
    assert is_docs_only(files) is False


def test_empty_diff_is_not_treated_as_docs_only():
    """An empty change set means diff detection failed; do not skip on it."""
    assert is_docs_only([]) is False


# --- AC: baseline is explicit, never inferred -----------------------------

def test_missing_baseline_refuses_rather_than_passing():
    check = check_against_baseline(
        failed_now=0,
        failing_test_ids_now=[],
        baseline=None,
    )
    assert check.allowed is False
    assert "no baseline" in check.reason


# --- Parsing ---------------------------------------------------------------

def test_parse_failed_test_ids():
    ids = parse_failed_test_ids(PYTEST_OUTPUT)
    assert ids == ["tests/test_alpha.py::test_one", "tests/test_beta.py::test_two"]


def test_parse_failed_test_ids_is_deduped_and_sorted():
    out = "FAILED tests/b.py::t2\nFAILED tests/a.py::t1\nFAILED tests/b.py::t2\n"
    assert parse_failed_test_ids(out) == ["tests/a.py::t1", "tests/b.py::t2"]


def test_parse_handles_empty_output():
    assert parse_failed_test_ids("") == []


# --- Persistence -----------------------------------------------------------

def test_baseline_roundtrip(tmp_path):
    b = _baseline(75, ["tests/test_a.py::test_x"])
    path = save_baseline(b, tmp_path)
    assert path.exists()

    loaded = load_baseline("zealchaiwut/demo", tmp_path)
    assert loaded is not None
    assert loaded.failed == 75
    assert loaded.failed_test_ids == ["tests/test_a.py::test_x"]


def test_baseline_path_is_slash_safe(tmp_path):
    save_baseline(_baseline(1), tmp_path)
    written = list((tmp_path / "baselines").glob("*.json"))
    assert len(written) == 1
    assert "/" not in written[0].name


def test_corrupt_baseline_loads_as_none(tmp_path):
    save_baseline(_baseline(1), tmp_path)
    target = next((tmp_path / "baselines").glob("*.json"))
    target.write_text("{not json", encoding="utf-8")
    assert load_baseline("zealchaiwut/demo", tmp_path) is None


def test_old_baseline_without_ids_falls_back_to_count(tmp_path):
    """A baseline holding only a count still enforces the count rule."""
    path = tmp_path / "baselines" / "zealchaiwut-demo.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"project": "zealchaiwut/demo", "fail": 5}), encoding="utf-8")

    loaded = load_baseline("zealchaiwut/demo", tmp_path)
    assert loaded is not None and loaded.failed_test_ids == []

    assert check_against_baseline(
        failed_now=6, failing_test_ids_now=[], baseline=loaded
    ).allowed is False
    assert check_against_baseline(
        failed_now=5, failing_test_ids_now=[], baseline=loaded
    ).allowed is True
