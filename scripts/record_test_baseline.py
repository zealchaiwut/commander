#!/usr/bin/env python3
"""Record the test baseline a merge is checked against (issue #2316).

Usage:
    python3 scripts/record_test_baseline.py --repo zealchaiwut/viral-radar
    python3 scripts/record_test_baseline.py --repo owner/repo --ref develop
    python3 scripts/record_test_baseline.py --repo owner/repo --dry-run

Runs the suite on `--ref` (default: develop) and writes the resulting failure
count and failing-test ids to `.commander/baselines/<owner>-<repo>.json`.

Baselines are deliberately explicit. `finish_feature.py` never infers one from
the branch being merged — a branch that set its own baseline would always pass.

Real baselines are not green: Commander's develop measured 2442 failed / 6999
passed / 363 skipped on 2026-08-20 (bare worktree, `tests/ -q`), and
viral-radar's develop measured 75 failed / 954 passed. The point is to pin what
is already broken so that only *new* breakage blocks a merge.

Measure where the check measures. `finish_feature.py` runs the suite in a bare
`git worktree`, which has no `node_modules/`, `.env` or `venv/`; this script runs
wherever you point `--repo-root`. Develop measures 2377 in the uat clone and 2442
in a worktree, so recording from a working clone injects ~65 phantom failures into
every comparison (#2329). Pass `--repo-root <worktree>`.
"""
from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
REPO_ROOT = SCRIPTS_DIR.parent

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

from services.sprint_manager.merge_baseline import (  # noqa: E402
    Baseline,
    baseline_path,
    collection_failed,
    measurement_is_empty,
    parse_errored_test_ids,
    parse_failed_test_ids,
    save_baseline,
)
from services.sprint_manager.suite_health_gate import _parse_pytest_output_ex  # noqa: E402


def _git_describe_ref(root: Path) -> str:
    """The branch (or short SHA when detached) actually checked out in `root`."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(root), capture_output=True, text=True, timeout=15,
        )
        name = (proc.stdout or "").strip()
        if proc.returncode != 0 or not name:
            return ""
        if name != "HEAD":
            return name
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(root), capture_output=True, text=True, timeout=15,
        )
        return (proc.stdout or "").strip()
    except Exception:
        return ""


def main() -> None:
    p = argparse.ArgumentParser(
        description="Record the test baseline used by the merge check.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--repo", required=True, help="owner/repo this baseline belongs to")
    p.add_argument("--ref", default="develop", help="git ref to measure (default: develop)")
    p.add_argument(
        "--pytest-args",
        default=os.environ.get("COMMANDER_BASELINE_PYTEST_ARGS", 'tests/ -q -m "not live_http"'),
        help=(
            "arguments passed to pytest "
            "(default: 'tests/ -q -m \"not live_http\"'). "
            "The live_http marker is applied by conftest.py to all files in "
            "tests/.live-http-allowlist. Excluding them makes baseline counts "
            "deterministic across consecutive runs of the same ref (issue #2339). "
            "Both this script and finish_feature.py must use the same scope."
        ),
    )
    p.add_argument("--timeout", type=int, default=900, help="seconds before giving up")
    p.add_argument("--repo-root", default=None, help="repo to run the suite in (default: cwd)")
    p.add_argument("--dry-run", action="store_true", help="measure and print, write nothing")
    args = p.parse_args()

    run_root = Path(args.repo_root) if args.repo_root else Path.cwd()

    # `--ref` is a label, not a checkout: this script measures whatever is in
    # run_root. Recording the claimed ref rather than the measured one produced a
    # baseline stamped `develop` that was actually measured on a feature branch —
    # precisely the "a branch sets its own baseline" case merge_baseline.py warns
    # about. Record what was really measured, and say so when they differ.
    measured_ref = _git_describe_ref(run_root) or args.ref
    if measured_ref != args.ref:
        print(
            f"NOTE: --ref says {args.ref!r} but {run_root} is on {measured_ref!r}. "
            f"This script measures the working tree, it does not check out --ref. "
            f"Recording the measured ref.",
            file=sys.stderr,
        )

    print(f"Measuring {args.repo} @ {measured_ref} in {run_root}…")

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", *shlex.split(args.pytest_args)],
            cwd=run_root, capture_output=True, text=True, timeout=args.timeout,
        )
    except subprocess.TimeoutExpired:
        print(
            f"ERROR: suite exceeded {args.timeout}s. Scope it with --pytest-args "
            "or raise --timeout; a baseline recorded from a partial run would be wrong.",
            file=sys.stderr,
        )
        sys.exit(1)

    output = (proc.stdout or "") + (proc.stderr or "")
    passed, failed, skipped, errors = _parse_pytest_output_ex(output)
    failed_ids = parse_failed_test_ids(output)
    error_ids = parse_errored_test_ids(output)

    # A baseline recorded from a suite that never ran is worse than no baseline:
    # `finish_feature.py` measures the branch the same way, also gets zero, and
    # the delta check compares 0 to 0 and passes every merge. Refuse to write one
    # (issue #2331).
    # The count of executed tests is the gate. `collection_failed` only sharpens
    # the message: matching pytest's phrases in free text cannot distinguish the
    # suite's own status line from the same words inside a captured traceback,
    # and commander's suite contains tests that run pytest and assert on its
    # output (issue #2331).
    if measurement_is_empty(passed, failed):
        if collection_failed(output):
            print(
                "ERROR: pytest aborted during collection — no tests ran, so there "
                "is nothing to record. Fix the collection errors below and re-run.\n",
                file=sys.stderr,
            )
            for line in output.splitlines():
                if line.startswith("ERROR collecting") or line.startswith("ERROR "):
                    print(f"  {line}", file=sys.stderr)
        else:
            print(
                f"ERROR: the suite executed no tests (passed={passed} failed={failed} "
                f"skipped={skipped}). A baseline of zero would make the merge check "
                f"inert. Check --pytest-args ({args.pytest_args!r}) selects real tests.",
                file=sys.stderr,
            )
        sys.exit(1)

    if failed and not failed_ids:
        print(
            "WARNING: the summary reports failures but no FAILED lines were parsed. "
            "The baseline will fall back to count-only comparison, which cannot "
            "detect a fixed-one/broke-one swap.",
            file=sys.stderr,
        )

    print(f"  passed={passed} failed={failed} skipped={skipped} errors={errors}")
    print(f"  failing test ids captured: {len(failed_ids)}")
    print(f"  erroring test ids captured: {len(error_ids)}")

    baseline = Baseline(
        project=args.repo,
        failed=failed,
        passed=passed,
        skipped=skipped,
        errored=errors,
        failed_test_ids=failed_ids,
        errored_test_ids=error_ids,
        recorded_at=datetime.now(timezone.utc).isoformat(),
        recorded_from_ref=measured_ref,
        pytest_args=args.pytest_args,
    )

    if args.dry_run:
        print("\n--dry-run: nothing written. Would write to:")
        print(f"  {baseline_path(args.repo, REPO_ROOT / '.commander')}")
        return

    path = save_baseline(baseline, REPO_ROOT / ".commander")
    print(f"\nWrote baseline to {path}")


if __name__ == "__main__":
    main()
