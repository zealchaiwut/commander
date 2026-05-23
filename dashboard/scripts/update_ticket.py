#!/usr/bin/env python3
"""Move a GitHub issue to a new status column by swapping labels.

Usage:
  python3 scripts/update_ticket.py --issue 42 --status in-progress
  python3 scripts/update_ticket.py --issue 42 --status sit
  python3 scripts/update_ticket.py --issue 42 --status uat
  python3 scripts/update_ticket.py --issue 42 --status blocked
  python3 scripts/update_ticket.py --issue 42 --status uat-approved

Prints:  #<number> <url>
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import github_client

STATUS_MAP = {
    "in-progress": {
        "add":    ["in-progress"],
        "remove": ["SIT", "UAT", "needs-rework", "blocked"],
        "close":  None,
    },
    "sit": {
        "add":    ["SIT"],
        "remove": ["in-progress", "UAT", "needs-rework", "blocked"],
        "close":  None,
    },
    "uat": {
        "add":    ["UAT"],
        "remove": ["in-progress", "SIT", "needs-rework", "blocked"],
        "close":  None,
    },
    "blocked": {
        "add":    ["blocked"],
        "remove": [],
        "close":  None,
    },
    "uat-approved": {
        "add":    ["UAT-approved"],
        "remove": ["UAT"],
        "close":  "completed",
    },
}


def _load_env():
    env = Path(__file__).parent.parent / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


def _find_feature_branch(issue: int) -> str | None:
    """Return the feature/<N>-* branch name if it exists locally or on origin."""
    pattern = f"feature/{issue}-*"

    r = subprocess.run(["git", "branch", "--list", pattern], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        name = line.strip().lstrip("*+ ").strip()
        if name:
            return name

    r = subprocess.run(["git", "branch", "-r", "--list", f"origin/{pattern}"], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        name = line.strip().removeprefix("origin/").strip()
        if name:
            return name

    return None


def _branch_merged_into_develop(branch: str) -> bool:
    """Return True if branch tip is an ancestor of origin/develop."""
    subprocess.run(["git", "fetch", "--quiet", "origin", "develop"], capture_output=True)

    for ref in (f"origin/{branch}", branch):
        r = subprocess.run(["git", "rev-parse", ref], capture_output=True, text=True)
        if r.returncode == 0:
            tip = r.stdout.strip()
            ancestor = subprocess.run(
                ["git", "merge-base", "--is-ancestor", tip, "origin/develop"],
                capture_output=True,
            )
            return ancestor.returncode == 0

    return False


def _check_uat_safeguard(issue: int, force: bool) -> None:
    """Enforce feature-branch-merged gate before UAT label is applied."""
    branch = _find_feature_branch(issue)

    if branch is None:
        msg = (
            f"UAT safeguard: no branch matching 'feature/{issue}-*' found locally "
            f"or on origin. Merge the feature branch into develop first, "
            f"or use --force to override."
        )
        if force:
            print(f"WARNING: {msg}", file=sys.stderr)
        else:
            sys.exit(msg)
        return

    if not _branch_merged_into_develop(branch):
        msg = (
            f"UAT safeguard: branch '{branch}' exists but has not been merged into "
            f"develop. Merge it first, or use --force to override."
        )
        if force:
            print(f"WARNING: {msg}", file=sys.stderr)
        else:
            sys.exit(msg)


def main():
    _load_env()

    parser = argparse.ArgumentParser(description="Update issue status")
    parser.add_argument("--issue",  type=int, required=True)
    parser.add_argument("--status", required=True, choices=list(STATUS_MAP))
    parser.add_argument("--force",  action="store_true",
                        help="Skip UAT merge safeguard (prints warning to stderr)")
    args = parser.parse_args()

    if args.status == "uat":
        _check_uat_safeguard(args.issue, args.force)

    try:
        repo = github_client.repo()
    except ValueError as e:
        sys.exit(str(e))

    mapping = STATUS_MAP[args.status]
    cmd = ["gh", "issue", "edit", str(args.issue), "--repo", repo]
    for lbl in mapping["add"]:
        cmd += ["--add-label", lbl]
    for lbl in mapping["remove"]:
        cmd += ["--remove-label", lbl]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"Error: {result.stderr.strip()}")

    if mapping["close"]:
        close_result = subprocess.run(
            ["gh", "issue", "close", str(args.issue), "--repo", repo,
             "--reason", mapping["close"]],
            capture_output=True, text=True,
        )
        if close_result.returncode != 0:
            sys.exit(f"Error closing issue: {close_result.stderr.strip()}")

    url = f"https://github.com/{repo}/issues/{args.issue}"
    print(f"#{args.issue} {url}")


if __name__ == "__main__":
    main()
