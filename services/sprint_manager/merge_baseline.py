"""Baseline-delta check gating a tester merge (issue #2316).

The orchestrator's gate pipeline was deleted in the 2026-08 shrink, so nothing
objective stands between "tester says pass" and a merge into develop. This module
is the minimum replacement: one measurement, compared against a recorded baseline.

Why a delta and not "tests must pass": real baselines are not green. Commander's
develop measured 2442 failed / 6999 passed / 363 skipped on 2026-08-20 (bare
worktree, `tests/ -q`), and zealchaiwut/viral-radar develop measured 75 failed /
954 passed / 35 skipped on 2026-08-19.

This file used to cite a "~25-failure baseline" from a "scoped health gate".
Both were wrong: `suite_health_gate.py` runs the full suite, and the ~25 figure
survived only because collection aborted and reported `0 passed / 0 failed`, so
nothing contradicted it (#2331). Re-measure rather than trusting any figure
written here. A must-be-green rule would block every merge on such a repo
forever; a delta rule is enforceable today.

The check refuses a merge when any of these holds:

  * the failure count rises above the baseline,
  * a test fails that was not failing in the baseline,
  * the error count rises above the baseline, or
  * a test errors that was not erroring in the baseline.

The second and fourth conditions matter because a count comparison alone is blind
to a swap — one pre-existing failure getting fixed while a new one appears leaves
the count unchanged and would sail through.

The error conditions are the third and fourth because pytest reports errors as a
category of its own, never folded into `failed` (issue #2336). Commander's develop
carried 469 of them entirely outside this gate: a branch that broke a fixture and
turned 100 passing tests into `ERROR` left `failed` untouched, so neither failure
condition fired and the merge was allowed. Errors are now compared the same way
failures are.

Baselines are explicit and recorded per project. They are never inferred at merge
time from the branch being merged, which would let a branch establish its own
baseline and pass trivially.

## Live-HTTP tests and determinism (issue #2339)

106 test files in tests/.live-http-allowlist make live HTTP calls to a running
UAT server. Two worktree runs of near-identical trees measured 2442 and 2457
failures — a difference of ~15 attributable to live-HTTP tests timing out under
suite load, not code regressions. A merge gate driven by flaky live-HTTP counts
is not objective.

Decision: the default COMMANDER_BASELINE_PYTEST_ARGS in both record_test_baseline.py
and finish_feature.py is now `tests/ -q -m "not live_http"`. The root conftest.py
applies the `live_http` marker to every file in tests/.live-http-allowlist, so
`-m "not live_http"` deselects all 106 files on both sides of the comparison.

Tolerance with live_http excluded: two consecutive runs of the same ref on the
same machine should agree to within ±5 failures. Any wider spread indicates a
non-HTTP flake in the remaining suite that should be investigated separately.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

BASELINE_DIRNAME = "baselines"

# `pytest -q` summary lines, e.g.
#   FAILED tests/test_foo.py::test_bar - AssertionError: ...
_FAILED_LINE = re.compile(r"^FAILED\s+(\S+?)(?:\s+-\s+.*)?$", re.MULTILINE)

# `pytest -q` short-summary error lines, e.g.
#   ERROR tests/test_foo.py::test_bar - fixture 'x' not found
#   ERROR tests/test_foo.py  (collection error — whole module)
# `ERROR collecting <path>` is pytest's *progress* line for the same event and
# names no test id, so it is excluded rather than recorded as a test called
# "collecting".
_ERROR_LINE = re.compile(
    r"^ERROR\s+(?!collecting\b)(\S+?)(?:\s+-\s+.*)?$", re.MULTILINE
)

# Paths that cannot change test outcomes, so a diff touching only these skips the
# check. Deliberately conservative: anything not listed here is treated as code.
_DOCS_ONLY_SUFFIXES = (".md", ".rst", ".txt")
_DOCS_ONLY_NAMES = ("LICENSE", "CODEOWNERS", ".gitignore")


# Markers of a genuine collection abort. Every alternative is anchored to the
# start of a line: pytest's own status lines begin at column zero, while the same
# phrases appearing inside a captured traceback are prefixed (`  E   !!!! ...`).
# Commander's suite contains tests that run pytest as a subprocess and assert on
# its output, so an unanchored phrase match reads their tracebacks as the outer
# suite's status — which is exactly how an earlier draft of this refused a run
# with 7257 passing tests (issue #2331).
_COLLECTION_ERROR = re.compile(
    r"^(?:ERROR collecting\s"
    r"|!+\s*Interrupted:.*during collection"
    r"|\d+\s+errors?\s+during\s+collection)",
    re.MULTILINE | re.IGNORECASE,
)


def collection_failed(output: str) -> bool:
    """True when pytest's own status lines report a collection abort.

    This is a *message* helper, not the gate. Text matching alone proved too
    fragile to gate on — see the anchoring note above. The authoritative signal
    that nothing was measured is `measurement_is_empty`, because a genuine
    collection abort always leaves zero tests executed.
    """
    return bool(_COLLECTION_ERROR.search(output or ""))


def measurement_is_empty(passed: int, failed: int) -> bool:
    """True when a run executed no tests, so its failure count means nothing.

    This is the authoritative check. A suite that collected nothing is not a
    passing suite, and reading `0 failed` from an empty run as success is what
    let the #2316 gate wave every merge through (issue #2331).
    """
    return (int(passed) + int(failed)) <= 0


def parse_failed_test_ids(output: str) -> list[str]:
    """Return the test ids named on `FAILED ...` lines of pytest -q output.

    Returns them sorted and de-duplicated so comparisons are order-independent.
    """
    return sorted({m.group(1) for m in _FAILED_LINE.finditer(output or "")})


def parse_errored_test_ids(output: str) -> list[str]:
    """Return the test ids named on `ERROR ...` lines of pytest -q output.

    Captures both per-test fixture errors (e.g. `ERROR tests/foo.py::test_bar`)
    and collection errors (e.g. `ERROR tests/foo.py`).  Returns them sorted and
    de-duplicated so comparisons are order-independent (issue #2336).
    """
    return sorted({m.group(1) for m in _ERROR_LINE.finditer(output or "")})


@dataclass
class Baseline:
    """A recorded, explicit expectation of how bad the suite currently is."""

    project: str
    failed: int
    passed: int = 0
    skipped: int = 0
    errored: int = 0
    failed_test_ids: list[str] = field(default_factory=list)
    # None means "not recorded" (baseline written before issue #2336).  An empty
    # list means "recorded, but zero errors".  check_against_baseline skips the
    # error check when this is None so old baselines behave as before.
    errored_test_ids: Optional[List[str]] = None
    recorded_at: str = ""
    recorded_from_ref: str = ""
    # The pytest scope this baseline was measured with. The per-merge run must
    # use the same scope or the two numbers are not comparable (issue #2331).
    pytest_args: str = ""

    @property
    def collected(self) -> int:
        """Tests actually executed when this baseline was recorded."""
        return self.passed + self.failed

    def to_dict(self) -> dict:
        d: dict = {
            "project": self.project,
            "fail": self.failed,
            "pass": self.passed,
            "skip": self.skipped,
            "errored": self.errored,
            "failed_test_ids": list(self.failed_test_ids),
            "recorded_at": self.recorded_at,
            "recorded_from_ref": self.recorded_from_ref,
            "pytest_args": self.pytest_args,
        }
        # Only write errored_test_ids when explicitly recorded.  Absent means
        # "old baseline" and keeps the None sentinel intact after a round-trip,
        # so check_against_baseline skips the error check for pre-#2336 baselines.
        if self.errored_test_ids is not None:
            d["errored_test_ids"] = list(self.errored_test_ids)
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Baseline":
        # errored_test_ids absent → None (old baseline, skip error check).
        # errored_test_ids present → list (new baseline, apply error check).
        errored_ids: Optional[List[str]] = (
            list(data["errored_test_ids"]) if "errored_test_ids" in data else None
        )
        return cls(
            project=data.get("project", ""),
            failed=int(data.get("fail", 0)),
            passed=int(data.get("pass", 0)),
            skipped=int(data.get("skip", 0)),
            errored=int(data.get("errored", 0)),
            failed_test_ids=list(data.get("failed_test_ids", []) or []),
            errored_test_ids=errored_ids,
            recorded_at=data.get("recorded_at", ""),
            recorded_from_ref=data.get("recorded_from_ref", ""),
            pytest_args=data.get("pytest_args", ""),
        )


@dataclass
class MergeCheck:
    """Outcome of comparing a run against its baseline."""

    allowed: bool
    reason: str
    new_failing_tests: list[str] = field(default_factory=list)
    new_erroring_tests: list[str] = field(default_factory=list)
    failed_now: int = 0
    failed_baseline: int = 0
    errored_now: int = 0
    errored_baseline: int = 0
    skipped_check: bool = False

    def summary(self) -> str:
        """A message suitable for posting to the issue."""
        if self.skipped_check:
            return f"Baseline-delta check skipped: {self.reason}"
        if self.allowed:
            msg = (
                f"Baseline-delta check passed: {self.failed_now} failing vs "
                f"baseline {self.failed_baseline}."
            )
            if self.errored_now or self.errored_baseline:
                msg += (
                    f" {self.errored_now} erroring vs baseline "
                    f"{self.errored_baseline}."
                )
            return msg
        lines = [
            f"Baseline-delta check REFUSED this merge: {self.reason}",
            f"Failing now: {self.failed_now}. Baseline: {self.failed_baseline}.",
        ]
        if self.errored_now or self.errored_baseline:
            lines.append(
                f"Erroring now: {self.errored_now}. Baseline: {self.errored_baseline}."
            )
        if self.new_failing_tests:
            lines.append("")
            lines.append("Tests failing that were not failing in the baseline:")
            lines.extend(f"  - {t}" for t in self.new_failing_tests)
        if self.new_erroring_tests:
            lines.append("")
            lines.append("Tests erroring that were not erroring in the baseline:")
            lines.extend(f"  - {t}" for t in self.new_erroring_tests)
        return "\n".join(lines)


def baseline_path(project: str, commander_dir: Path) -> Path:
    safe = project.replace("/", "-")
    return commander_dir / BASELINE_DIRNAME / f"{safe}.json"


def load_baseline(project: str, commander_dir: Path) -> Optional[Baseline]:
    """Load a project's recorded baseline; None when absent or unreadable."""
    path = baseline_path(project, commander_dir)
    if not path.exists():
        return None
    try:
        return Baseline.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return None


def save_baseline(baseline: Baseline, commander_dir: Path) -> Path:
    """Persist a baseline, creating the directory when needed."""
    path = baseline_path(baseline.project, commander_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not baseline.recorded_at:
        baseline.recorded_at = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(baseline.to_dict(), indent=2), encoding="utf-8")
    return path


def is_docs_only(changed_files: Iterable[str]) -> bool:
    """True when every changed path is documentation and cannot affect tests.

    An empty diff is *not* docs-only — an empty change set more likely means the
    caller failed to compute a diff than that nothing changed, and silently
    skipping the check on a detection failure is the wrong default.
    """
    files = [f.strip() for f in changed_files if f and f.strip()]
    if not files:
        return False
    for f in files:
        name = f.rsplit("/", 1)[-1]
        if name in _DOCS_ONLY_NAMES:
            continue
        if f.startswith("docs/") or any(f.endswith(s) for s in _DOCS_ONLY_SUFFIXES):
            continue
        return False
    return True


def check_against_baseline(
    *,
    failed_now: int,
    failing_test_ids_now: Iterable[str],
    baseline: Optional[Baseline],
    changed_files: Optional[Iterable[str]] = None,
    passed_now: int = -1,
    collection_error: bool = False,
    errored_now: int = 0,
    erroring_test_ids_now: Iterable[str] = (),
) -> MergeCheck:
    """Compare a suite run against the recorded baseline.

    Refuses when the failure count rises, or when any test fails that was not
    failing in the baseline; and likewise for errors (issue #2336).  A missing
    baseline refuses rather than waves the merge through: an unmeasured project
    is not a safe one, and recording a baseline is a one-line operation.
    """
    ids_now = sorted(set(failing_test_ids_now or []))

    if changed_files is not None and is_docs_only(changed_files):
        return MergeCheck(
            allowed=True,
            reason="diff touches documentation only",
            failed_now=failed_now,
            skipped_check=True,
        )

    # A run that executed nothing is not a green run. The count of executed tests
    # is the gate; `collection_error` only sharpens the wording, because matching
    # pytest's phrases in free text is not reliable enough to refuse a merge on
    # (issue #2331). passed_now defaults to -1 meaning "caller did not report it",
    # which keeps older callers working unchanged.
    if passed_now >= 0 and measurement_is_empty(passed_now, failed_now):
        detail = (
            "pytest aborted during collection, so no tests ran"
            if collection_error
            else "the suite executed no tests on this branch (0 passed, 0 failed)"
        )
        return MergeCheck(
            allowed=False,
            reason=(
                f"{detail} — the failure count is meaningless, and an empty run "
                f"cannot show that the branch adds no failures"
            ),
            failed_now=failed_now,
            new_failing_tests=ids_now,
        )

    if baseline is None:
        return MergeCheck(
            allowed=False,
            reason=(
                "no baseline recorded for this project — record one before merging "
                "(a baseline must be explicit, never inferred from the branch being merged)"
            ),
            failed_now=failed_now,
            new_failing_tests=ids_now,
        )

    if measurement_is_empty(baseline.passed, baseline.failed):
        return MergeCheck(
            allowed=False,
            reason=(
                f"the recorded baseline for {baseline.project} measured no tests "
                f"(0 passed, 0 failed) — it was recorded from a suite that did not "
                f"run. Re-record it before merging"
            ),
            failed_now=failed_now,
            failed_baseline=baseline.failed,
            new_failing_tests=ids_now,
        )

    baseline_ids = set(baseline.failed_test_ids or [])
    # Only meaningful when the baseline actually recorded ids; an older baseline
    # holding just a count falls back to the count comparison alone.
    new_failures = sorted(set(ids_now) - baseline_ids) if baseline_ids else []

    if failed_now > baseline.failed:
        return MergeCheck(
            allowed=False,
            reason=f"failure count rose from {baseline.failed} to {failed_now}",
            new_failing_tests=new_failures or ids_now,
            failed_now=failed_now,
            failed_baseline=baseline.failed,
            errored_now=errored_now,
            errored_baseline=baseline.errored or 0,
        )

    if new_failures:
        return MergeCheck(
            allowed=False,
            reason=(
                f"{len(new_failures)} test(s) failing that were not failing in the "
                f"baseline (count unchanged at {failed_now} — a pre-existing failure "
                f"was fixed while a new one appeared)"
            ),
            new_failing_tests=new_failures,
            failed_now=failed_now,
            failed_baseline=baseline.failed,
            errored_now=errored_now,
            errored_baseline=baseline.errored or 0,
        )

    # Error checks mirror the two failure conditions above.  Skipped when the
    # baseline was written before issue #2336 added error tracking (signalled by
    # errored_test_ids=None) so old baselines behave exactly as before.
    if baseline.errored_test_ids is not None:
        errored_ids_now = sorted(set(erroring_test_ids_now or []))
        baseline_errored_ids = set(baseline.errored_test_ids)
        new_erroring = (
            sorted(set(errored_ids_now) - baseline_errored_ids)
            if baseline_errored_ids
            else []
        )

        if errored_now > (baseline.errored or 0):
            return MergeCheck(
                allowed=False,
                reason=f"error count rose from {baseline.errored} to {errored_now}",
                new_erroring_tests=new_erroring or errored_ids_now,
                errored_now=errored_now,
                errored_baseline=baseline.errored or 0,
                failed_now=failed_now,
                failed_baseline=baseline.failed,
            )

        if new_erroring:
            return MergeCheck(
                allowed=False,
                reason=(
                    f"{len(new_erroring)} test(s) erroring that were not erroring in the "
                    f"baseline (count unchanged at {errored_now} — a pre-existing error "
                    f"was fixed while a new one appeared)"
                ),
                new_erroring_tests=new_erroring,
                errored_now=errored_now,
                errored_baseline=baseline.errored or 0,
                failed_now=failed_now,
                failed_baseline=baseline.failed,
            )

    return MergeCheck(
        allowed=True,
        reason=(
            "no new failures or errors"
            if baseline.errored_test_ids is not None
            else "no new failures (this baseline predates error tracking)"
        ),
        failed_now=failed_now,
        failed_baseline=baseline.failed,
        errored_now=errored_now,
        errored_baseline=baseline.errored or 0,
    )
