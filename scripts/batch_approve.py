#!/usr/bin/env python3
"""Approve all open UAT issues for a repo, optionally scoped to a sprint.

Usage:
  python3 scripts/batch_approve.py --repo <owner/repo>
  python3 scripts/batch_approve.py --repo <owner/repo> --sprint <N>

Prints:  #<number> <url> for each approved issue.
Prints:  No open UAT issues found.  when there is nothing to do (exits 0).
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

    parser = argparse.ArgumentParser(description="Batch-approve open UAT issues")
    parser.add_argument("--repo",   required=True, help="owner/repo (e.g. zealchaiwut/commander)")
    parser.add_argument("--sprint", type=int,      help="Only approve issues in sprint-N")
    args = parser.parse_args()

    try:
        issues = github_client.list_open_uat_issues(repo_name=args.repo, sprint=args.sprint)
    except Exception as e:
        sys.exit(f"Error fetching UAT issues: {e}")

    if not issues:
        print("No open UAT issues found.")
        sys.exit(0)

    for issue in issues:
        num = issue["number"]
        try:
            github_client.approve_issue(num, repo_name=args.repo)
        except Exception as e:
            sys.exit(f"Error approving issue #{num}: {e}")
        url = issue.get("url") or f"https://github.com/{args.repo}/issues/{num}"
        print(f"#{num} {url}")


if __name__ == "__main__":
    main()
