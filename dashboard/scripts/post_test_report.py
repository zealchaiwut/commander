#!/usr/bin/env python3
"""Post a structured test report comment to a GitHub issue.

Usage:
    python3 scripts/post_test_report.py --issue 42 --report-file /tmp/report.md
    python3 scripts/post_test_report.py --issue 42 --report-file /tmp/report.md --repo owner/repo
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import github_client


def main():
    p = argparse.ArgumentParser(description="Post a test report to a GitHub issue")
    p.add_argument("--issue",       type=int, required=True,  help="Issue number")
    p.add_argument("--report-file", required=True,             help="Path to markdown report file")
    p.add_argument("--repo",        default=None,              help="owner/repo (auto-detected if omitted)")
    args = p.parse_args()

    report_path = Path(args.report_file)
    if not report_path.exists():
        print(f"Error: report file not found: {report_path}", file=sys.stderr)
        sys.exit(1)

    body = report_path.read_text().strip()
    if not body:
        print("Error: report file is empty", file=sys.stderr)
        sys.exit(1)

    github_client.add_comment(args.issue, body, repo_name=args.repo)
    print(f"✅  Posted test report to issue #{args.issue}")


if __name__ == "__main__":
    main()
