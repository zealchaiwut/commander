#!/usr/bin/env python3
"""Move a GitHub issue to a new status column by swapping labels.

Usage:
  python3 scripts/update_ticket.py --issue 42 --status in-progress
  python3 scripts/update_ticket.py --issue 42 --status sit
  python3 scripts/update_ticket.py --issue 42 --status uat
  python3 scripts/update_ticket.py --issue 42 --status blocked

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
    },
    "sit": {
        "add":    ["SIT"],
        "remove": ["in-progress", "UAT", "needs-rework", "blocked"],
    },
    "uat": {
        "add":    ["UAT"],
        "remove": ["in-progress", "SIT", "needs-rework", "blocked"],
    },
    "blocked": {
        "add":    ["blocked"],
        "remove": [],
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


def main():
    _load_env()

    parser = argparse.ArgumentParser(description="Update issue status")
    parser.add_argument("--issue",  type=int, required=True)
    parser.add_argument("--status", required=True, choices=list(STATUS_MAP))
    args = parser.parse_args()

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

    url = f"https://github.com/{repo}/issues/{args.issue}"
    print(f"#{args.issue} {url}")


if __name__ == "__main__":
    main()
