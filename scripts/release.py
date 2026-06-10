#!/usr/bin/env python3
"""Merge develop into main and tag the release.

Run this manually after UAT sign-off. It is NOT wired to the dashboard
Approve button — run it yourself once you're happy with the UAT result.

Usage:
    python3 ~/commander/scripts/release.py --issue 42

Run from the git root of the repository (NOT from dashboard/).

What it does:
  1. Pulls latest main and develop
  2. Merges develop → main with --no-ff
  3. Pushes main
  4. Tags the commit as issue-<N> (annotated)
  5. Pushes the tag

On merge conflict: aborts, prints manual resolution steps, exits non-zero.
"""
import argparse
import subprocess
import sys
from pathlib import Path

_DASHBOARD_DIR = Path(__file__).parent.parent / "apps" / "dashboard"
sys.path.insert(0, str(_DASHBOARD_DIR))
from dotenv import load_dotenv
load_dotenv(_DASHBOARD_DIR / ".env")


def _run(*cmd) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()


def _try(*cmd) -> tuple[bool, str]:
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode == 0, r.stdout.strip()


def main():
    p = argparse.ArgumentParser(description="Merge develop into main and tag the release")
    p.add_argument("--issue", type=int, required=True, help="Issue number used for the tag")
    args = p.parse_args()

    tag = f"issue-{args.issue}"

    # Guard: tag must not already exist
    ok, _ = _try("git", "rev-parse", "--verify", tag)
    if ok:
        sys.stderr.write(str(f"Error: tag {tag} already exists. Nothing to do.") + "\n")
        sys.exit(1)

    sys.stdout.write(str(f"Releasing issue #{args.issue} — merging develop → main, tagging {tag}…") + "\n")

    # Update both branches
    _run("git", "fetch", "origin")
    _run("git", "checkout", "develop")
    _run("git", "pull", "origin", "develop")
    _run("git", "checkout", "main")
    _run("git", "pull", "origin", "main")

    # Merge
    merge_msg = f"Release {tag}: merge develop into main"
    result = subprocess.run(
        ["git", "merge", "--no-ff", "develop", "-m", merge_msg],
        capture_output=True, text=True,
    )

    if result.returncode != 0:
        _try("git", "merge", "--abort")
        sys.stderr.write(str("Error: merge conflict between develop and main.\n"
            "Resolve manually:\n"
            "  git checkout main\n"
            "  git merge develop\n"
            "  # fix conflicts\n"
            "  git add . && git commit\n"
            f"  git push origin main\n"
            f"  git tag -a {tag} -m 'Release issue #{args.issue}'\n"
            f"  git push origin {tag}") + "\n")
        sys.exit(1)

    _run("git", "push", "origin", "main")

    # Tag
    _run("git", "tag", "-a", tag, "-m", f"Release issue #{args.issue}")
    _run("git", "push", "origin", tag)

    sys.stdout.write(str(f"✅  main updated and tagged {tag}") + "\n")
    sys.stdout.write(str(f"    Push origin main — done") + "\n")
    sys.stdout.write(str(f"    Tag {tag} — pushed") + "\n")


if __name__ == "__main__":
    main()
