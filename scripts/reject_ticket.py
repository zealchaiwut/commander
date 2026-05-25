#!/usr/bin/env python3
"""Reject a UAT issue: swap labels, reopen if closed, post rejection comment.

Usage:
  python3 scripts/reject_ticket.py --issue 42 --reason "Step 3 fails on mobile"

Prints:  #<number> <url>
Exits non-zero with a human-readable message on failure.
--reason is required; omitting it exits non-zero with a usage error.
"""
import argparse
import os
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

    parser = argparse.ArgumentParser(description="Reject a UAT issue")
    parser.add_argument("--issue",  type=int, required=True, help="Issue number")
    parser.add_argument("--reason", required=True,           help="Rejection reason (required)")
    args = parser.parse_args()

    try:
        repo = github_client.repo()
    except ValueError as e:
        sys.exit(str(e))

    try:
        github_client.reject_issue(args.issue, args.reason, repo)
    except Exception as e:
        sys.exit(f"Error rejecting issue #{args.issue}: {e}")

    url = f"https://github.com/{repo}/issues/{args.issue}"
    print(f"#{args.issue} {url}")


if __name__ == "__main__":
    main()
