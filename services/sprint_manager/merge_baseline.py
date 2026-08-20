"""Baseline-delta check gating a tester merge (issue #2316).

The orchestrator's gate pipeline was deleted in the 2026-08 shrink, so nothing
objective stands between "tester says pass" and a merge into develop. This module
is the minimum replacement: one measurement, compared against a recorded baseline.

Why a delta and not "tests must pass": real baselines are not green. Commander's
own scoped health gate carries a documented ~25-failure baseline, and
zealchaiwut/viral-radar develop measured 75 failed / 954 passed / 35 skipped on
2026-08-19, stable across two runs. A must-be-green rule would block every merge
on such a repo forever; a delta rule is enforceable today.

The check refuses a merge when either:

  * the failure count rises above the baseline, or
  * a test fails that was not failing in the baseline.

The second condition matters because a count comparison alone is blind to a swap
— one pre-existing failure getting fixed while a new one appears leaves the count
unchanged and would sail through.

Baselines are explicit and recorded per project. They are never inferred at merge
time from the branch being merged, which would let a branch establish its own
baseline and pass trivially.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

BASELINE_DIRNAME = "baselines"

# `pytest -q` summary lines, e.g.
#   FAILED tests/test_foo.py::test_bar - AssertionError: ...
_FAILED_LINE = re.compile(r"^FAILED\s+(\S+?)(?:\s+-\s+.*)?$", re.MULTILINE)

# Paths that cannot change test outcomes, so a diff touching only these skips the
# check. Deliberately conservative: anything not listed here is treated as code.
_DOCS_ONLY_SUFFIXES = (".md", ".rst", ".txt")
_DOCS_ONLY_NAMES = ("LICENSE", "CODEOWNERS", ".gitignore")


# A genuine collection abort, which does *not* produce a `N failed` summary —
# pytest gives up before running anything, so a naive read of the summary reports
# zero failures for a suite that never ran (issue #2331).
#
# These markers are deliberately narrow. The short summary prints `ERROR <path>`
# for a collection error but `ERROR <path>::<test>` for a per-test fixture error,
# and a healthy run can carry hundreds of the latter — commander's own suite
# measured 469 errors alongside 7257 passing tests. Matching a bare `ERROR ` line
# would flag that run as an abort and refuse every merge.
_COLLECTION_ERROR = re.compile(
    r"(?:^ERROR collecting\s|"          # per-module collection error header
    r"\d+\s+errors?\s+during\s+collection|"  # summary line
    r"^!+\s*Interrupted:)",             # pytest gave up
    re.MULTILINE | re.IGNORECASE,
)


def collection_failed(output: str) -> bool:
    """True when pytest aborted during collection instead of running the suite.

    This is the failure mode that made the #2316 gate inert: three stale test
    modules importing a removed symbol aborted collection, both the recorded
    baseline and the per-merge run measured `0 failed`, and the delta check
    compared 0 to 0 and passed every merge.
    """
    return bool(_COLLECTION_ERROR.search(output or ""))


def measurement_is_empty(passed: int, failed: int) -> bool:
    """True when a run executed no tests, so its failure count means nothing.

    A suite that collected nothing is not a passing suite. Treating `0 failed`
    from an empty run as a real measurement is what let the gate wave merges
    through (issue #2331).
    """
    return (int(passed) + int(failed)) <= 0


def parse_failed_test_ids(output: str) -> list[str]:
    """Return the test ids named on `FAILED ...` lines of pytest -q output.

    Returns them sorted and de-duplicated so comparisons are order-independent.
    """
    return sorted({m.group(1) for m in _FAILED_LINE.finditer(output or "")})


@dataclass
class Baseline:
    """A recorded, explicit expectation of how bad the suite currently is."""

    project: str
    failed: int
    passed: int = 0
    skipped: int = 0
    failed_test_ids: list[str] = field(default_factory=list)
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
        return {
            "project": self.project,
            "fail": self.failed,
            "pass": self.passed,
            "skip": self.skipped,
            "failed_test_ids": list(self.failed_test_ids),
            "recorded_at": self.recorded_at,
            "recorded_from_ref": self.recorded_from_ref,
            "pytest_args": self.pytest_args,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Baseline":
        return cls(
            project=data.get("project", ""),
            failed=int(data.get("fail", 0)),
            passed=int(data.get("pass", 0)),
            skipped=int(data.get("skip", 0)),
            failed_test_ids=list(data.get("failed_test_ids", []) or []),
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
    failed_now: int = 0
    failed_baseline: int = 0
    skipped_check: bool = False

    def summary(self) -> str:
        """A message suitable for posting to the issue."""
        if self.skipped_check:
            return f"Baseline-delta check skipped: {self.reason}"
        if self.allowed:
            return (
                f"Baseline-delta check passed: {self.failed_now} failing vs "
                f"baseline {self.failed_baseline}."
            )
        lines = [
            f"Baseline-delta check REFUSED this merge: {self.reason}",
            f"Failing now: {self.failed_now}. Baseline: {self.failed_baseline}.",
        ]
        if self.new_failing_tests:
            lines.append("")
            lines.append("Tests failing that were not failing in the baseline:")
            lines.extend(f"  - {t}" for t in self.new_failing_tests)
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
) -> MergeCheck:
    """Compare a suite run against the recorded baseline.

    Refuses when the failure count rises, or when any test fails that was not
    failing in the baseline. A missing baseline refuses rather than waves the
    merge through: an unmeasured project is not a safe one, and recording a
    baseline is a one-line operation.
    """
    ids_now = sorted(set(failing_test_ids_now or []))

    if changed_files is not None and is_docs_only(changed_files):
        return MergeCheck(
            allowed=True,
            reason="diff touches documentation only",
            failed_now=failed_now,
            skipped_check=True,
        )

    # A run that collected nothing is not a green run. Both of these refuse
    # rather than read an empty measurement as zero failures (issue #2331).
    if collection_error:
        return MergeCheck(
            allowed=False,
            reason=(
                "pytest aborted during collection, so no tests ran — the failure "
                "count is meaningless. Fix the collection error and re-run; a merge "
                "cannot be judged against a suite that never executed"
            ),
            failed_now=failed_now,
            new_failing_tests=ids_now,
        )

    # passed_now defaults to -1 meaning "caller did not report it", which keeps
    # older callers working unchanged.
    if passed_now >= 0 and measurement_is_empty(passed_now, failed_now):
        return MergeCheck(
            allowed=False,
            reason=(
                "the suite executed no tests on this branch (0 passed, 0 failed) — "
                "an empty run cannot show that the branch adds no failures"
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
        )

    return MergeCheck(
        allowed=True,
        reason="no new failures",
        failed_now=failed_now,
        failed_baseline=baseline.failed,
    )
