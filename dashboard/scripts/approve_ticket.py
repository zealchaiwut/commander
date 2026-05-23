#!/usr/bin/env python3
"""Approve a UAT issue: swap labels, close with reason "completed", optionally comment.

Usage:
  python3 scripts/approve_ticket.py --issue 42
  python3 scripts/approve_ticket.py --issue 42 --comment "LGTM, shipping"

Prints:  #<number> <url>
Exits non-zero with a human-readable message on failure.
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import github_client


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

    parser = argparse.ArgumentParser(description="Approve a UAT issue")
    parser.add_argument("--issue",   type=int, required=True, help="Issue number")
    parser.add_argument("--comment", default="",              help="Optional comment to post")
    args = parser.parse_args()

    try:
        repo = github_client.repo()
    except ValueError as e:
        sys.exit(str(e))

    try:
        github_client.approve_issue(args.issue, repo)
    except Exception as e:
        sys.exit(f"Error approving issue #{args.issue}: {e}")

    if args.comment:
        try:
            github_client.add_comment(args.issue, args.comment, repo)
        except Exception as e:
            sys.exit(f"Issue approved but failed to post comment: {e}")

    url = f"https://github.com/{repo}/issues/{args.issue}"
    print(f"#{args.issue} {url}")


if __name__ == "__main__":
    main()
