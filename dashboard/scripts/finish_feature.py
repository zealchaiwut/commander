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
    python3 ~/commander/dashboard/scripts/finish_feature.py --issue 42

Run from the git root of the repository (NOT from dashboard/).
"""
import argparse
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
    p = argparse.ArgumentParser(description="Merge feature branch into develop after tests pass")
    p.add_argument("--issue", type=int, required=True, help="Issue number")
    p.add_argument("--repo",  default=None,            help="owner/repo override")
    args = p.parse_args()

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

    print(f"Merging {branch} → develop…")

    _run("git", "checkout", "develop")
    _run("git", "pull", "origin", "develop")

    merge_msg = f"Merge {branch} into develop (issue #{args.issue})"
    result = subprocess.run(
        ["git", "merge", "--no-ff", branch, "-m", merge_msg],
        capture_output=True, text=True,
    )

    if result.returncode != 0:
        _try("git", "merge", "--abort")
        conflict_comment = (
            f"❌ **Merge conflict** — could not merge `{branch}` into `develop` automatically.\n\n"
            f"Resolve manually:\n"
            f"```bash\n"
            f"git checkout develop\n"
            f"git merge {branch}\n"
            f"# fix conflicts, then:\n"
            f"git add . && git commit\n"
            f"git push origin develop\n"
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

    _run("git", "push", "origin", "develop")
    print("Pushed develop.")

    # Apply UAT label before deleting the branch so the safeguard can verify
    # the merge-base check (it needs the branch ref to still exist on origin).
    update_ticket = Path(__file__).parent / "update_ticket.py"
    label_result = subprocess.run(
        [sys.executable, str(update_ticket), "--issue", str(args.issue), "--status", "uat"],
        capture_output=True, text=True,
    )
    if label_result.returncode != 0:
        print(f"Warning: failed to apply UAT label — {label_result.stderr.strip()}", file=sys.stderr)
    else:
        print(f"UAT label applied to issue #{args.issue}.")

    # Clean up feature branch
    _try("git", "branch", "-d", branch)
    _try("git", "push", "origin", "--delete", branch)

    print(f"✅  Merged {branch} into develop")
    print(f"    Feature branch deleted locally and on origin")


if __name__ == "__main__":
    main()
