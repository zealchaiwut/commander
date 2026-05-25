#!/usr/bin/env python3
"""Create a feature branch for an issue and prepare it for development.

Usage:
    python3 ~/commander/dashboard/scripts/start_feature.py --issue 42

Run from the git root of the repository being worked on (NOT from dashboard/).
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")
import github_client


def _run(*cmd) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()


def _try(*cmd) -> tuple[bool, str]:
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode == 0, r.stdout.strip()


def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")[:40]


def ensure_develop(repo_name: str | None) -> None:
    """Create develop off main if it doesn't exist locally or remotely."""
    ok, _ = _try("git", "show-ref", "--verify", "--quiet", "refs/heads/develop")
    if ok:
        return

    ok, _ = _try("git", "ls-remote", "--exit-code", "origin", "refs/heads/develop")
    if ok:
        print("Fetching develop from origin…")
        _run("git", "fetch", "origin")
        _run("git", "checkout", "--track", "origin/develop")
        return

    print("develop branch not found — creating off main…")
    _run("git", "checkout", "main")
    _run("git", "pull", "origin", "main")
    _run("git", "checkout", "-b", "develop")
    _run("git", "push", "-u", "origin", "develop")
    print("Created and pushed develop branch.")


def main():
    p = argparse.ArgumentParser(description="Create a feature branch for a GitHub issue")
    p.add_argument("--issue", type=int, required=True, help="Issue number")
    p.add_argument("--repo",  default=None,            help="owner/repo override")
    p.add_argument(
        "--base-branch",
        default="develop",
        help="Branch to base the feature branch off (default: develop)",
    )
    args = p.parse_args()

    issue = github_client.get_issue(args.issue, repo_name=args.repo)
    title  = issue.get("title", f"issue-{args.issue}")
    slug   = slugify(title)
    branch = f"feature/{args.issue}-{slug}"
    short  = args.issue
    base   = args.base_branch

    print(f"Issue #{short}: {title}")
    print(f"Branch:         {branch}")
    print(f"Base:           {base}")

    ensure_develop(args.repo)

    # Already exists locally → just check out
    ok, _ = _try("git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}")
    if ok:
        print(f"Branch {branch} already exists locally — checking out.")
        _run("git", "checkout", branch)
        github_client.update_labels(short, add=["in-progress"], remove=[], repo_name=args.repo)
        print(f"✅  Checked out {branch} (label: in-progress)")
        return

    # Already exists on remote → track it
    ok, _ = _try("git", "ls-remote", "--exit-code", "origin", f"refs/heads/{branch}")
    if ok:
        print(f"Branch {branch} exists on remote — checking out.")
        _run("git", "fetch", "origin")
        _run("git", "checkout", "--track", f"origin/{branch}")
        github_client.update_labels(short, add=["in-progress"], remove=[], repo_name=args.repo)
        print(f"✅  Checked out {branch} (label: in-progress)")
        return

    # Fresh branch off base branch
    _run("git", "fetch", "origin")
    ok, _ = _try("git", "show-ref", "--verify", "--quiet", f"refs/heads/{base}")
    if ok:
        _run("git", "checkout", base)
        _try("git", "pull", "origin", base)
    else:
        # base branch only on remote (e.g. sprint branch)
        _run("git", "checkout", "--track", f"origin/{base}")
    _run("git", "checkout", "-b", branch)
    _run("git", "push", "-u", "origin", branch)

    github_client.update_labels(
        short,
        add=["in-progress"],
        remove=["backlog", "needs-rework"],
        repo_name=args.repo,
    )
    github_client.add_comment(
        short,
        f"🌿 Started work on branch `{branch}` (based off `{base}`)",
        repo_name=args.repo,
    )

    print(f"✅  Created and pushed {branch} (based off {base})")
    print(f"    Label updated to: in-progress")
    print(branch)   # last line: branch name for scripts that capture output


if __name__ == "__main__":
    main()
