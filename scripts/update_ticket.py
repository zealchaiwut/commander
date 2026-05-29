#!/usr/bin/env python3
"""Move a GitHub issue to a new status column by swapping labels.

Usage:
  python3 scripts/update_ticket.py --issue 42 --status in-progress
  python3 scripts/update_ticket.py --issue 42 --status sit
  python3 scripts/update_ticket.py --issue 42 --status uat
  python3 scripts/update_ticket.py --issue 42 --status blocked
  python3 scripts/update_ticket.py --issue 42 --status uat-approved
  python3 scripts/update_ticket.py --issue 42 --status estimated
  python3 scripts/update_ticket.py --issue 42 --status needs-rework

Prints:  #<number> <url>
Exits with code 0 on full success, code 2 if any label operation failed after
all retries (a warning comment is posted to the issue in that case).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_DASHBOARD_DIR))
import github_client
from services.logging import log as structured_log
from services.run_id import mint_run_id

# Sprint labels (sprint-N) are protected: they must never be removed by a
# status transition. This guard matches any "sprint-<digits>" label name.
_SPRINT_LABEL_RE = re.compile(r"^sprint-\d+$")

STATUS_MAP = {
    "in-progress": {
        "add":    ["in-progress"],
        "remove": ["SIT", "UAT", "needs-rework", "blocked"],
        "close":  None,
    },
    "sit": {
        "add":    ["SIT"],
        "remove": ["in-progress", "UAT", "needs-rework", "blocked"],
        "close":  None,
    },
    "uat": {
        "add":    ["UAT"],
        "remove": ["in-progress", "SIT", "needs-rework", "blocked"],
        "close":  None,
    },
    "blocked": {
        "add":    ["blocked"],
        "remove": [],
        "close":  None,
    },
    "uat-approved": {
        "add":    ["UAT-approved"],
        "remove": ["UAT"],
        "close":  "completed",
    },
    "estimated": {
        "add":    ["estimated"],
        "remove": [],
        "close":  None,
    },
    "needs-rework": {
        "add":    ["needs-rework"],
        "remove": ["in-progress", "SIT", "UAT", "UAT-approved"],
        "close":  None,
    },
}

_BACKOFFS = (2, 5, 10)


def _load_env():
    env = _DASHBOARD_DIR / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


def _find_feature_branch(issue: int) -> str | None:
    """Return the feature/<N>-* branch name if it exists locally or on origin."""
    pattern = f"feature/{issue}-*"

    r = subprocess.run(["git", "branch", "--list", pattern], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        name = line.strip().lstrip("*+ ").strip()
        if name:
            return name

    r = subprocess.run(["git", "branch", "-r", "--list", f"origin/{pattern}"], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        name = line.strip().removeprefix("origin/").strip()
        if name:
            return name

    return None


def _branch_merged_into_target(branch: str, target: str) -> bool:
    """Return True if branch tip is an ancestor of origin/<target>."""
    for ref in (f"origin/{branch}", branch):
        r = subprocess.run(["git", "rev-parse", ref], capture_output=True, text=True)
        if r.returncode == 0:
            tip = r.stdout.strip()
            ancestor = subprocess.run(
                ["git", "merge-base", "--is-ancestor", tip, f"origin/{target}"],
                capture_output=True,
            )
            return ancestor.returncode == 0

    return False


def _check_uat_safeguard_with_sha(sha: str, target: str, force: bool) -> None:
    """Verify merge SHA is an ancestor of origin/<target> (authoritative, no race)."""
    subprocess.run(["git", "fetch", "--quiet", "origin", target], capture_output=True)
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", sha, f"origin/{target}"],
        capture_output=True,
    )
    if ancestor.returncode != 0:
        msg = (
            f"UAT safeguard: merge SHA {sha!r} is not an ancestor of 'origin/{target}'. "
            f"Ensure the merge was pushed, or use --force to override."
        )
        if force:
            print(f"WARNING: {msg}", file=sys.stderr)
        else:
            sys.exit(msg)


def _check_uat_safeguard(issue: int, target: str, force: bool) -> None:
    """Enforce feature-branch-merged gate before UAT label is applied."""
    # Prune stale refs so deleted branches don't cause false positives.
    subprocess.run(["git", "fetch", "--prune", "origin"], capture_output=True)

    branch = _find_feature_branch(issue)

    if branch is None:
        msg = (
            f"UAT safeguard: no branch matching 'feature/{issue}-*' found locally "
            f"or on origin. Merge the feature branch into {target} first, "
            f"or use --force to override."
        )
        if force:
            print(f"WARNING: {msg}", file=sys.stderr)
        else:
            sys.exit(msg)
        return

    if not _branch_merged_into_target(branch, target):
        msg = (
            f"UAT safeguard: branch '{branch}' exists but has not been merged into "
            f"'{target}'. Merge it first, or use --force to override."
        )
        if force:
            print(f"WARNING: {msg}", file=sys.stderr)
        else:
            sys.exit(msg)


def _fetch_current_labels(issue: int, repo: str) -> set[str]:
    """Pre-fetch the current labels on the issue from GitHub."""
    result = subprocess.run(
        ["gh", "issue", "view", str(issue), "--repo", repo, "--json", "labels"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        # If we can't fetch labels, return empty set — removes will be attempted
        return set()
    try:
        data = json.loads(result.stdout)
        return {lbl["name"] for lbl in data.get("labels", [])}
    except (json.JSONDecodeError, KeyError):
        return set()


def _run_with_retry(cmd: list[str], label: str, operation: str, issue_num: int = 0) -> tuple[bool, str]:
    """Run a gh label command with up to 3 retries and backoff.

    Returns (success, last_stderr).
    """
    last_stderr = ""
    for attempt in range(len(_BACKOFFS) + 1):
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            return True, ""
        last_stderr = result.stderr.strip()
        if attempt < len(_BACKOFFS):
            wait = _BACKOFFS[attempt]
            structured_log.warn(
                "ticket_update_retry",
                f"{operation} label \"{label}\" attempt {attempt + 1} failed — retrying in {wait}s",
                issue_num=issue_num,
                label=label,
                operation=operation,
                attempt=attempt + 1,
                retry_delay_secs=wait,
                subprocess_stderr=last_stderr,
            )
            time.sleep(wait)
    return False, last_stderr


def main():
    _load_env()

    _run_id = mint_run_id("manual")
    os.environ["COMMANDER_RUN_ID"] = _run_id
    structured_log.set_context(run_id=_run_id, source="update_ticket")

    parser = argparse.ArgumentParser(description="Update issue status")
    parser.add_argument("--issue",  type=int, required=True)
    parser.add_argument("--status", required=True, choices=list(STATUS_MAP))
    parser.add_argument("--force",  action="store_true",
                        help="Skip UAT merge safeguard (prints warning to stderr)")
    parser.add_argument("--repo",   default=None,
                        help="Override repo (owner/name).  Defaults to auto-detected repo.")
    parser.add_argument("--merge-sha", default=None,
                        help="Merge commit SHA; when provided the UAT safeguard uses "
                             "ancestry check instead of branch-ref presence (avoids race).")
    parser.add_argument("--target-branch",
                        default=os.environ.get("COMMANDER_MERGE_TARGET", "develop"),
                        help="Branch to verify merge against (default: COMMANDER_MERGE_TARGET or 'develop')")
    args = parser.parse_args()
    structured_log.set_context(issue_num=args.issue)
    structured_log.info("ticket_update_start", f"updating issue #{args.issue} to status {args.status!r}", issue_num=args.issue, status=args.status)

    if args.status == "uat":
        if args.merge_sha:
            _check_uat_safeguard_with_sha(args.merge_sha, args.target_branch, args.force)
        else:
            _check_uat_safeguard(args.issue, args.target_branch, args.force)

    if args.repo:
        repo = args.repo
    else:
        try:
            repo = github_client.repo()
        except ValueError as e:
            sys.exit(str(e))

    mapping = STATUS_MAP[args.status]

    # Pre-fetch current labels to allow smart skipping of removes
    current_labels = _fetch_current_labels(args.issue, repo)

    failed_ops = []
    attempted_ops = []

    # Apply each add label individually with retry
    for lbl in mapping["add"]:
        op = f'add "{lbl}"'
        attempted_ops.append(op)
        cmd = ["gh", "issue", "edit", str(args.issue), "--repo", repo, "--add-label", lbl]
        success, last_stderr = _run_with_retry(cmd, lbl, "add", issue_num=args.issue)
        if success:
            print(f'applied label "{lbl}"')
        else:
            print(f'failed to apply label "{lbl}"', file=sys.stderr)
            failed_ops.append((op, last_stderr))

    # Handle each remove label individually
    for lbl in mapping["remove"]:
        op = f'remove "{lbl}"'
        if _SPRINT_LABEL_RE.match(lbl):
            print(f'skipped remove "{lbl}" (sprint label protected)', file=sys.stderr)
            continue
        if lbl not in current_labels:
            print(f'skipped remove "{lbl}" (not present)')
            continue
        attempted_ops.append(op)
        cmd = ["gh", "issue", "edit", str(args.issue), "--repo", repo, "--remove-label", lbl]
        success, last_stderr = _run_with_retry(cmd, lbl, "remove", issue_num=args.issue)
        if success:
            print(f'removed label "{lbl}"')
        else:
            print(f'failed to remove label "{lbl}"', file=sys.stderr)
            failed_ops.append((op, last_stderr))

    if failed_ops:
        # Post a warning comment
        failed_lines = "\n".join(
            f"- `{op}`: {stderr}" for op, stderr in failed_ops
        )
        attempted_lines = "\n".join(f"- `{op}`" for op in attempted_ops)
        warning_comment = (
            f"**Label update failed for `--status {args.status}`.**\n\n"
            f"Some label operations failed after {len(_BACKOFFS) + 1} attempts "
            f"and require manual review.\n\n"
            f"**Target status:** `{args.status}`\n\n"
            f"**Attempted operations:**\n{attempted_lines}\n\n"
            f"**Failed operations (with last error):**\n{failed_lines}\n\n"
            f"Please apply or remove the above labels manually."
        )
        try:
            subprocess.run(
                ["gh", "issue", "comment", str(args.issue), "--repo", repo,
                 "--body", warning_comment],
                capture_output=True,
            )
        except Exception:
            pass
        sys.exit(2)

    if mapping["close"]:
        close_result = subprocess.run(
            ["gh", "issue", "close", str(args.issue), "--repo", repo,
             "--reason", mapping["close"]],
            capture_output=True, text=True,
        )
        if close_result.returncode != 0:
            sys.exit(f"Error closing issue: {close_result.stderr.strip()}")

    url = f"https://github.com/{repo}/issues/{args.issue}"
    print(f"#{args.issue} {url}")


if __name__ == "__main__":
    main()
