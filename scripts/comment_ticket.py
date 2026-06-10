#!/usr/bin/env python3
"""Post a comment on a GitHub issue.

Usage:
  python3 scripts/comment_ticket.py --issue 42 --body "🤖 agent-name picked this up at 14:32"

Prints:  #<number> <url>
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

_DASHBOARD_DIR = Path(__file__).parent.parent / "apps" / "dashboard"
sys.path.insert(0, str(_DASHBOARD_DIR))
import github_client


def _load_env():
    env = _DASHBOARD_DIR / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


def main():
    _load_env()

    parser = argparse.ArgumentParser(description="Comment on a GitHub issue")
    parser.add_argument("--issue", type=int, required=True)
    parser.add_argument("--body",  required=True)
    args = parser.parse_args()

    try:
        repo = github_client.repo()
    except ValueError as e:
        sys.exit(str(e))

    result = subprocess.run(
        ["gh", "issue", "comment", str(args.issue),
         "--repo", repo,
         "--body", args.body],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        sys.exit(f"Error: {result.stderr.strip()}")

    url = f"https://github.com/{repo}/issues/{args.issue}"
    sys.stdout.write(str(f"#{args.issue} {url}") + "\n")


if __name__ == "__main__":
    main()
