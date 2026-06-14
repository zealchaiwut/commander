#!/usr/bin/env python3
"""Run the full pytest suite health gate for a sprint and record results.

Usage:
    python3 scripts/run_suite_health_gate.py --sprint-label sprint-72
    python3 scripts/run_suite_health_gate.py --sprint-label sprint-72 --sprints-dir .commander/sprints
    python3 scripts/run_suite_health_gate.py --sprint-label sprint-72 --timeout 120

Results are written to <sprints-dir>/<sprint-label>-suite-health.json and
appended to the structured event log.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
REPO_ROOT   = SCRIPTS_DIR.parent

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))
sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))

from dotenv import load_dotenv
load_dotenv(REPO_ROOT / "apps" / "dashboard" / ".env")

from services.sprint_manager.suite_health_gate import (
    run_gate,
    load_gate_result,
    SUITE_HEALTH_TIMEOUT_DEFAULT,
)


def main() -> None:
    p = argparse.ArgumentParser(
        description="Run the full pytest suite health gate for a sprint.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--sprint-label",
        required=True,
        metavar="LABEL",
        help="Sprint label (e.g. sprint-72)",
    )
    p.add_argument(
        "--sprints-dir",
        default=None,
        metavar="PATH",
        help="Directory to write health JSON (default: auto-discover from .commander/sprints)",
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=SUITE_HEALTH_TIMEOUT_DEFAULT,
        metavar="SECONDS",
        help=f"Timeout for pytest run in seconds (default: {SUITE_HEALTH_TIMEOUT_DEFAULT})",
    )
    p.add_argument(
        "--repo-root",
        default=None,
        metavar="PATH",
        help="Repo root where tests/ directory lives (default: auto-detect from git)",
    )
    p.add_argument(
        "--show",
        action="store_true",
        help="Print the existing health result without re-running the gate",
    )

    args = p.parse_args()

    # Resolve sprints_dir
    sprints_dir: Path
    if args.sprints_dir:
        sprints_dir = Path(args.sprints_dir).expanduser().resolve()
    else:
        # Auto-discover from .commander/sprints (walk up from cwd)
        cwd = Path.cwd()
        sprints_dir = _discover_sprints_dir(cwd)
    sprints_dir.mkdir(parents=True, exist_ok=True)

    # Resolve repo_root
    repo_root: Path = Path(args.repo_root).expanduser().resolve() if args.repo_root else REPO_ROOT

    if args.show:
        result = load_gate_result(args.sprint_label, sprints_dir)
        if result is None:
            sys.stdout.write(str(f"No health data found for {args.sprint_label!r} in {sprints_dir}") + "\n")
            sys.exit(1)
        sys.stdout.write(str(json.dumps(result.to_dict(), indent=2)) + "\n")
        return

    sys.stdout.write(str(f"[health-gate] Running full test suite for {args.sprint_label!r} ...") + "\n")
    sys.stdout.write(str(f"  sprints_dir  : {sprints_dir}") + "\n")
    sys.stdout.write(str(f"  repo_root    : {repo_root}") + "\n")
    sys.stdout.write(str(f"  timeout      : {args.timeout}s") + "\n")

    result = run_gate(
        sprint_label=args.sprint_label,
        sprints_dir=sprints_dir,
        repo_root=repo_root,
        timeout_seconds=args.timeout,
    )

    sys.stdout.write(str(f"[health-gate] Done. Status: {result.status}") + "\n")
    sys.stdout.write(str(
        f"  passed={result.passed}  failed={result.failed}  "
        f"skipped={result.skipped}  duration={result.duration_seconds}s"
    ) + "\n")

    if result.timed_out:
        sys.stdout.write(str("  ⚠ SUITE TIMEOUT") + "\n")
        sys.exit(2)
    elif result.failed > 0:
        sys.stdout.write(str(f"  ⚠ SUITE FAILING ({result.failed} failed)") + "\n")
        sys.exit(1)
    else:
        sys.stdout.write(str("  ✅ Suite passing") + "\n")


def _discover_sprints_dir(cwd: Path) -> Path:
    """Walk up from cwd looking for .commander/sprints."""
    for parent in [cwd, *cwd.parents]:
        candidate = parent / ".commander" / "sprints"
        if candidate.is_dir():
            return candidate
    # Fall back to creating under cwd
    return cwd / ".commander" / "sprints"


if __name__ == "__main__":
    main()
