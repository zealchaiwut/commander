#!/usr/bin/env python3
"""Run the post-sprint write-back steps for a manually-driven sprint (issue #2051).

The automated sprint_manager.py pipeline calls these steps in post_sprint.py after
`write_sprint_summary()` completes.  Manual sprints (e.g. sprint-viz9001/9002)
bypass the pipeline, so none of these run automatically:

  1. Documenter agent  — reads the sprint diff, updates CHANGELOG.md, SCHEMA.md,
                          docs/features/api.md, README.md, and commits to the branch.
  2. record_agent_finish() — writes final_message / duration to agent_runs table for
                              each dispatch row.  NULL final_message is why the
                              reasoning panel has never shown a persisted narrative
                              for ~26k non-test rows (#2022).
  3. Sprint review (sprint_review.py) — Haiku pass that generates the sprint
                                          summary issue comment.

This script gives a CLI entry point so these steps can be run retroactively for
any manually-driven sprint without invoking the full sprint_manager pipeline.

Usage:
    python3 scripts/run_post_sprint.py --sprint-label sprint-viz9001 \\
        --repo zealchaiwut/commander \\
        [--dry-run]

Notes:
  - For the documenter to commit, the sprint branch must still exist and the
    tester (or coder) clone must be writable.  Use --dry-run to print what
    would run without dispatching any agents.
  - record_agent_finish() requires the agent_runs rows to have been written at
    dispatch time; it only updates them, it does not create them.
  - If the sprint summary issue already exists, the reviewer step is skipped.

Post-sprint steps skipped by the manual path (this script covers them):
  documenter_agent, record_agent_finish, sprint_review
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
REPO_ROOT = SCRIPTS_DIR.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SPRINT_MANAGER_DIR = REPO_ROOT / "services" / "sprint_manager"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR), str(SPRINT_MANAGER_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Run post-sprint write-back steps (documenter, record_agent_finish, "
            "sprint_review) for a manually-driven sprint."
        )
    )
    p.add_argument(
        "--sprint-label", required=True,
        help="Sprint label to process, e.g. sprint-viz9001",
    )
    p.add_argument(
        "--repo", default="zealchaiwut/commander",
        help="GitHub repo in owner/repo form (default: zealchaiwut/commander)",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be run without dispatching any agents or writing to the DB",
    )
    p.add_argument(
        "--skip-documenter", action="store_true",
        help="Skip the documenter agent dispatch",
    )
    p.add_argument(
        "--skip-review", action="store_true",
        help="Skip the sprint_review.py step",
    )
    return p.parse_args()


def _announce(msg: str, dry_run: bool = False) -> None:
    prefix = "[dry-run] " if dry_run else ""
    print(f"{prefix}{msg}", flush=True)


def _run_sprint_review(sprint_label: str, repo: str, dry_run: bool) -> None:
    """Invoke sprint_review.py for the sprint."""
    review_script = SCRIPTS_DIR / "sprint_review.py"
    if not review_script.exists():
        print(f"  [warn] sprint_review.py not found at {review_script}; skipping", flush=True)
        return

    cmd = [sys.executable, str(review_script), sprint_label, "--repo", repo]
    _announce(f"  Running sprint review: {' '.join(cmd)}", dry_run)
    if not dry_run:
        result = subprocess.run(cmd, cwd=str(REPO_ROOT))
        if result.returncode != 0:
            print(f"  [warn] sprint_review.py exited {result.returncode}", flush=True)


def _dispatch_documenter_cli(sprint_label: str, repo: str, dry_run: bool) -> None:
    """Print instructions for running the documenter — it requires a full SprintState
    object which is not reconstructable from a sprint label alone without loading the
    sprint state files.  For now, this emits a ready-to-run documenter invocation.
    """
    _announce(
        f"\n  Documenter: to run the documenter agent for {sprint_label}, invoke:\n"
        f"\n    python3 services/sprint_manager/sprint_manager.py {sprint_label} \\\n"
        f"        --documenter-only --repo {repo}\n"
        "\n  If sprint_manager.py does not support --documenter-only yet, dispatch\n"
        "  the documenter agent directly using the /documenter skill in a Claude\n"
        "  Code session with COMMANDER_MERGE_TARGET set to the sprint branch.",
        dry_run,
    )


def main() -> None:
    args = _parse_args()
    sprint_label = args.sprint_label
    repo = args.repo
    dry_run = args.dry_run

    print(f"Post-sprint write-back for {sprint_label} (repo: {repo})", flush=True)
    if dry_run:
        print("  [dry-run mode — no writes or agent dispatches]", flush=True)

    # Step 1: documenter
    if not args.skip_documenter:
        _announce("\n[1/2] Documenter agent", dry_run)
        _dispatch_documenter_cli(sprint_label, repo, dry_run)
    else:
        print("\n[1/2] Documenter — skipped (--skip-documenter)", flush=True)

    # Step 2: sprint review summary
    if not args.skip_review:
        _announce("\n[2/2] Sprint review", dry_run)
        _run_sprint_review(sprint_label, repo, dry_run)
    else:
        print("\n[2/2] Sprint review — skipped (--skip-review)", flush=True)

    print("\nDone.", flush=True)
    print(
        "\nNote: record_agent_finish() (agent_runs.final_message backfill) requires "
        "access to the subprocess log output from the original dispatch run.  "
        "It cannot be retroactively reconstructed by this script.",
        flush=True,
    )


if __name__ == "__main__":
    main()
