#!/usr/bin/env python3
"""Merge a tested feature branch into develop.

Call this after tests pass. It:
  1. Fetches latest from origin
  2. Checks out the feature/<N>-* branch
  3. Merges it into the target branch with --no-ff
  4. Pushes the target branch
  5. Deletes the feature branch locally and on origin

On success, writes to stdout:
    FINISH_FEATURE_OUTCOME merged sha=<sha> branch=<branch>

On merge conflict: aborts cleanly, posts a comment, exits non-zero.
Label transitions are managed exclusively by sprint_manager via state_machine.transition().

Usage:
    python3 ~/commander/scripts/finish_feature.py --issue 42

Run from the git root of the repository (NOT from dashboard/).
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_DASHBOARD_DIR))
from dotenv import load_dotenv
load_dotenv(_DASHBOARD_DIR / ".env")
import github_client
from services.run_id import mint_run_id
from services.logging import log as structured_log


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
    _run_id = mint_run_id("manual")
    os.environ["COMMANDER_RUN_ID"] = _run_id
    structured_log.set_context(run_id=_run_id, source="finish_feature")

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
    structured_log.set_context(issue_num=args.issue)
    structured_log.info("feature_finish_start", f"merging feature branch for issue #{args.issue} into {target}", issue_num=args.issue)

    # Fetch so remote-only branches are visible
    sys.stdout.write(str("Fetching from origin…") + "\n")
    _run("git", "fetch", "origin")

    branch = find_branch(args.issue)
    if not branch:
        sys.stderr.write(str(f"Error: no branch found matching feature/{args.issue}-*\n"
            "Are you in the git root? Has start_feature.py been run?") + "\n")
        sys.exit(1)

    # Do NOT check out the feature branch locally — it may be checked out in
    # another worktree (e.g. the coder worktree), which causes git to refuse.
    # origin/<branch> is already current after the fetch above.

    sys.stdout.write(str(f"Merging {branch} → {target}…") + "\n")

    # Ensure target branch is available locally
    ok, _ = _try("git", "show-ref", "--verify", "--quiet", f"refs/heads/{target}")
    if ok:
        _run("git", "checkout", target)
        _run("git", "pull", "origin", target)
    else:
        _run("git", "checkout", "--track", f"origin/{target}")

    merge_msg = f"Merge {branch} into {target} (issue #{args.issue})"
    result = subprocess.run(
        ["git", "merge", "--no-ff", f"origin/{branch}", "-m", merge_msg],
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
        sys.stderr.write(str(conflict_comment) + "\n")
        try:
            github_client.add_comment(args.issue, conflict_comment, repo_name=args.repo)
        except Exception:
            pass
        sys.exit(1)

    ok, merge_sha = _try("git", "rev-parse", "HEAD")
    if not ok or not merge_sha:
        merge_sha = ""

    _run("git", "push", "origin", target)
    sys.stdout.write(str(f"Pushed {target}.") + "\n")

    # Clean up feature branch — use -D (force) since the branch may be checked
    # out in the coder worktree, where -d would be rejected by git.
    _try("git", "branch", "-D", branch)
    _try("git", "push", "origin", "--delete", branch)

    sys.stdout.write(str(f"✅  Merged {branch} into {target}") + "\n")
    sys.stdout.write(str(f"    Feature branch deleted locally and on origin") + "\n")
    # Signal to sprint_manager that merge succeeded; label transitions are
    # handled exclusively by sprint_manager via state_machine.transition().
    sys.stdout.write(str(f"FINISH_FEATURE_OUTCOME merged sha={merge_sha} branch={branch}") + "\n")


if __name__ == "__main__":
    main()
