#!/usr/bin/env python3
"""Create a GitHub issue and assign it to a sprint.

Usage:
  python3 scripts/create_ticket.py \\
    --title "Fix login bug" \\
    --body  "Steps to reproduce..." \\
    --sprint 1 \\
    --labels "bug,backend"

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

    parser = argparse.ArgumentParser(description="Create a GitHub issue")
    parser.add_argument("--title",  required=True)
    parser.add_argument("--body",   default="")
    parser.add_argument("--sprint", type=int, required=True)
    parser.add_argument("--labels", default="", help="Comma-separated extra labels")
    args = parser.parse_args()

    try:
        repo = github_client.repo()
    except ValueError as e:
        sys.exit(str(e))

    labels = [f"sprint-{args.sprint}"]
    if args.labels:
        labels += [l.strip() for l in args.labels.split(",") if l.strip()]

    result = subprocess.run(
        ["gh", "issue", "create",
         "--repo",  repo,
         "--title", args.title,
         "--body",  args.body,
         "--label", ",".join(labels)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        sys.exit(f"Error: {result.stderr.strip()}")

    url    = result.stdout.strip()
    number = url.rstrip("/").split("/")[-1]
    print(f"#{number} {url}")


if __name__ == "__main__":
    main()
