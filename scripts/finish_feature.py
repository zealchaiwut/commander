#!/usr/bin/env python3
"""Merge a tested feature branch into develop and promote the ticket to UAT.

Call this after tests pass. It:
  1. Fetches latest from origin
  2. Checks out the feature/<N>-* branch
  3. Merges it into develop with --no-ff
  4. Pushes develop
  5. Applies the UAT label via update_ticket.py (branch still exists at this point)
  6. Deletes the feature branch locally and on origin

On merge conflict: aborts cleanly, posts a comment, exits non-zero.

Usage:
    python3 ~/commander/scripts/finish_feature.py --issue 42

Run from the git root of the repository (NOT from dashboard/).
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

_DASHBOARD_DIR = Path(__file__).parent.parent / "apps" / "dashboard"
sys.path.insert(0, str(_DASHBOARD_DIR))
from dotenv import load_dotenv
load_dotenv(_DASHBOARD_DIR / ".env")
import github_client


def _run(*cmd) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()


def _try(*cmd) -> tuple[bool, str]:
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode == 0, r.stdout.strip()


def find_branch(issue_num: int) -> str | None:
    """Find feature/<N>-* locally first, then on remote."""
    ok, out = _try("git", "branch", "--list", f"feature/{issue_num}-*")
    if ok and out.strip():
        return out.strip().splitlines()[0].strip().lstrip("*+ ")

    ok, out = _try("git", "branch", "-r", "--list", f"origin/feature/{issue_num}-*")
    if ok and out.strip():
        return out.strip().splitlines()[0].strip().removeprefix("origin/")

    return None


def main():
    p = argparse.ArgumentParser(description="Merge feature branch into target branch after tests pass")
    p.add_argument("--issue", type=int, required=True, help="Issue number")
    p.add_argument("--repo",  default=None,            help="owner/repo override")
    p.add_argument(
        "--target-branch",
        default=os.environ.get("COMMANDER_MERGE_TARGET", "develop"),
        help="Branch to merge into (default: COMMANDER_MERGE_TARGET env var or 'develop')",
    )
    args = p.parse_args()
    target = args.target_branch

    # Fetch so remote-only branches are visible
    print("Fetching from origin…")
    _run("git", "fetch", "origin")

    branch = find_branch(args.issue)
    if not branch:
        print(
            f"Error: no branch found matching feature/{args.issue}-*\n"
            "Are you in the git root? Has start_feature.py been run?",
            file=sys.stderr,
        )
        sys.exit(1)

    # Ensure local branch is up to date
    ok, _ = _try("git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}")
    if not ok:
        _run("git", "checkout", "--track", f"origin/{branch}")
    else:
        _run("git", "checkout", branch)
        _try("git", "pull", "origin", branch)

    print(f"Merging {branch} → {target}…")

    # Ensure target branch is available locally
    ok, _ = _try("git", "show-ref", "--verify", "--quiet", f"refs/heads/{target}")
    if ok:
        _run("git", "checkout", target)
        _run("git", "pull", "origin", target)
    else:
        _run("git", "checkout", "--track", f"origin/{target}")

    merge_msg = f"Merge {branch} into {target} (issue #{args.issue})"
    result = subprocess.run(
        ["git", "merge", "--no-ff", branch, "-m", merge_msg],
        capture_output=True, text=True,
    )

    if result.returncode != 0:
        _try("git", "merge", "--abort")
        conflict_comment = (
            f"❌ **Merge conflict** — could not merge `{branch}` into `{target}` automatically.\n\n"
            f"Resolve manually:\n"
            f"```bash\n"
            f"git checkout {target}\n"
            f"git merge {branch}\n"
            f"# fix conflicts, then:\n"
            f"git add . && git commit\n"
            f"git push origin {target}\n"
            f"git branch -d {branch}\n"
            f"git push origin --delete {branch}\n"
            f"```"
        )
        print(conflict_comment, file=sys.stderr)
        try:
            github_client.add_comment(args.issue, conflict_comment, repo_name=args.repo)
        except Exception:
            pass
        sys.exit(1)

    _run("git", "push", "origin", target)
    print(f"Pushed {target}.")

    # Apply UAT label before deleting the branch so the safeguard can verify
    # the merge-base check (it needs the branch ref to still exist on origin).
    update_ticket = Path(__file__).parent / "update_ticket.py"
    _UAT_BACKOFFS = (2, 5, 10)
    label_result = None
    for attempt in range(len(_UAT_BACKOFFS) + 1):
        label_result = subprocess.run(
            [sys.executable, str(update_ticket), "--issue", str(args.issue), "--status", "uat"],
            capture_output=True, text=True,
        )
        if label_result.returncode == 0:
            break
        if attempt < len(_UAT_BACKOFFS):
            wait = _UAT_BACKOFFS[attempt]
            print(
                f"Warning: UAT label attempt {attempt + 1} failed — retrying in {wait}s…",
                file=sys.stderr,
            )
            time.sleep(wait)

    if label_result.returncode != 0:
        stderr_text = label_result.stderr.strip()
        warning_comment = (
            f"**Merge succeeded but UAT label could not be applied.**\n\n"
            f"The merge of this feature branch into `{target}` completed successfully, "
            f"but `update_ticket.py --status uat` failed after {len(_UAT_BACKOFFS) + 1} attempts.\n\n"
            f"Last error:\n"
            f"```\n"
            f"{stderr_text}\n"
            f"```\n\n"
            f"Please apply the **UAT** label manually so this ticket reaches the UAT queue."
        )
        print(
            f"Error: failed to apply UAT label after {len(_UAT_BACKOFFS) + 1} attempts — {stderr_text}",
            file=sys.stderr,
        )
        try:
            github_client.add_comment(args.issue, warning_comment, repo_name=args.repo)
        except Exception as exc:
            print(f"Warning: could not post warning comment — {exc}", file=sys.stderr)
        sys.exit(2)
    else:
        print(f"UAT label applied to issue #{args.issue}.")

    # Clean up feature branch
    _try("git", "branch", "-d", branch)
    _try("git", "push", "origin", "--delete", branch)

    print(f"✅  Merged {branch} into {target}")
    print(f"    Feature branch deleted locally and on origin")


if __name__ == "__main__":
    main()
