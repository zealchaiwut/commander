#!/usr/bin/env python3
"""Promote develop → master by creating a draft PR with sprint summary and changelog.

Usage:
    python scripts/promote_to_master.py [--dry-run] [--draft|--ready] [--title <title>]

Exit codes:
    0  success (or nothing to do)
    1  precondition failure
    2  GitHub API failure
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


# ── path setup ─────────────────────────────────────────────────────────────────

SCRIPTS_DIR = Path(__file__).parent
REPO_ROOT = SCRIPTS_DIR.parent

# Resolve .commander/logs/ — walk up from REPO_ROOT to find .commander/
def _find_commander_logs() -> Path:
    """Walk up from REPO_ROOT to find .commander/logs/; fall back to REPO_ROOT/.commander/logs/."""
    candidate = REPO_ROOT
    for _ in range(6):
        p = candidate / ".commander" / "logs"
        if p.parent.exists():
            return p
        candidate = candidate.parent
    return REPO_ROOT / ".commander" / "logs"


COMMANDER_LOGS_DIR = _find_commander_logs()


# ── git helpers ────────────────────────────────────────────────────────────────

def _run(*cmd: str, check: bool = True, cwd: Path | None = None) -> str:
    """Run a command and return stdout as a stripped string."""
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=check,
        cwd=str(cwd) if cwd else None,
    )
    return result.stdout.strip()


def _try(*cmd: str, cwd: Path | None = None) -> tuple[bool, str]:
    """Run a command; return (success, stdout)."""
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
    )
    return result.returncode == 0, result.stdout.strip()


# ── pre-condition checks ───────────────────────────────────────────────────────

def check_preconditions() -> None:
    """Run all pre-condition checks. Exit on failure."""

    # 1. No uncommitted changes (staged or unstaged, ignoring untracked files)
    ok, _ = _try("git", "diff", "--quiet", "HEAD")
    if not ok:
        print("Uncommitted changes detected; commit or stash first.", file=sys.stderr)
        sys.exit(1)

    # 2. Fetch remotes so we have up-to-date refs
    _run("git", "fetch", "origin", check=False)

    # 3. Both origin/master and origin/develop must exist
    ok_master, _ = _try("git", "rev-parse", "--verify", "origin/master")
    ok_develop, _ = _try("git", "rev-parse", "--verify", "origin/develop")
    if not ok_master:
        print("origin/master does not exist.", file=sys.stderr)
        sys.exit(1)
    if not ok_develop:
        print("origin/develop does not exist.", file=sys.stderr)
        sys.exit(1)

    # 4. develop must be ahead of master
    ahead_raw = _run(
        "git", "rev-list", "--count", "origin/master..origin/develop", check=False
    )
    try:
        ahead = int(ahead_raw)
    except ValueError:
        ahead = 0

    if ahead == 0:
        print("Nothing to promote — develop is at master's head.")
        sys.exit(0)

    # 5. No existing open PR from develop to master
    try:
        pr_list_raw = subprocess.run(
            [
                "gh", "pr", "list",
                "--base", "master",
                "--head", "develop",
                "--state", "open",
                "--json", "number,url",
            ],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        prs = json.loads(pr_list_raw) if pr_list_raw else []
    except subprocess.CalledProcessError as e:
        print(f"Failed to check existing PRs: {e.stderr.strip()}", file=sys.stderr)
        sys.exit(2)

    if prs:
        pr = prs[0]
        print(
            f"An open PR from develop already exists: #{pr['number']} {pr['url']}",
            file=sys.stderr,
        )
        sys.exit(1)


# ── commit analysis ────────────────────────────────────────────────────────────

def get_commits() -> list[tuple[str, str]]:
    """Return list of (short_sha, subject) for commits on develop not in master."""
    raw = _run("git", "log", "origin/master..origin/develop", "--oneline", check=False)
    if not raw:
        return []
    commits = []
    for line in raw.splitlines():
        parts = line.split(" ", 1)
        sha = parts[0] if parts else ""
        subject = parts[1] if len(parts) > 1 else ""
        commits.append((sha, subject))
    return commits


def extract_issue_refs(commits: list[tuple[str, str]]) -> list[int]:
    """Extract and deduplicate issue numbers from commit subjects."""
    pattern = re.compile(r"(?:closes?|fixes?|resolves?)\s+#(\d+)", re.IGNORECASE)
    seen: set[int] = set()
    result: list[int] = []
    for _, subject in commits:
        for m in pattern.finditer(subject):
            n = int(m.group(1))
            if n not in seen:
                seen.add(n)
                result.append(n)
    return result


def extract_sprint_numbers(commits: list[tuple[str, str]]) -> list[int]:
    """Extract unique sprint numbers referenced in commit subjects, sorted."""
    pattern = re.compile(r"\bsprint-(\d+)\b", re.IGNORECASE)
    seen: set[int] = set()
    for _, subject in commits:
        for m in pattern.finditer(subject):
            seen.add(int(m.group(1)))
    return sorted(seen)


def get_changed_files() -> list[str]:
    """Return list of files changed between master and develop."""
    raw = _run(
        "git", "diff", "--name-only", "origin/master..origin/develop", check=False
    )
    if not raw:
        return []
    return raw.splitlines()


def group_by_area(files: list[str]) -> dict[str, int]:
    """Group files by their top-level directory component."""
    area_counts: dict[str, int] = {}
    for f in files:
        parts = f.split("/")
        area = parts[0] if len(parts) > 1 else "(root)"
        area_counts[area] = area_counts.get(area, 0) + 1
    return dict(sorted(area_counts.items()))


# ── sprint summary issue lookup ───────────────────────────────────────────────

def lookup_sprint_summary(sprint_num: int) -> tuple[int | None, str | None]:
    """Search for a 'Sprint N Executive Summary' issue. Returns (number, url) or (None, None)."""
    try:
        raw = subprocess.run(
            [
                "gh", "issue", "list",
                "--search", f"Sprint {sprint_num} Executive Summary in:title",
                "--state", "all",
                "--json", "number,url",
                "--limit", "5",
            ],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        issues = json.loads(raw) if raw else []
        if issues:
            return issues[0]["number"], issues[0]["url"]
    except Exception:
        pass
    return None, None


# ── title generation ──────────────────────────────────────────────────────────

def generate_title(commit_count: int, sprint_numbers: list[int]) -> str:
    if not sprint_numbers:
        sprint_part = "no sprint refs"
    elif len(sprint_numbers) == 1:
        sprint_part = f"sprint-{sprint_numbers[0]}"
    else:
        sprint_part = f"sprint-{sprint_numbers[0]} through sprint-{sprint_numbers[-1]}"
    return f"release: develop → master ({commit_count} commit{'s' if commit_count != 1 else ''}, {sprint_part})"


# ── PR body generation ────────────────────────────────────────────────────────

def build_pr_body(
    commits: list[tuple[str, str]],
    issue_refs: list[int],
    sprint_numbers: list[int],
    changed_files: list[str],
    dry_run: bool = False,
) -> str:
    """Build the full PR body markdown."""

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Sprints included section
    sprints_lines = []
    for sn in sprint_numbers:
        num, url = lookup_sprint_summary(sn)
        if num is not None and url:
            sprints_lines.append(f"- [Sprint {sn} Executive Summary](#{num}) — {url}")
        else:
            sprints_lines.append(f"- sprint-{sn} (no summary issue)")
    if not sprints_lines:
        sprints_lines.append("- (no sprint references found in commits)")

    # Tickets shipping section
    ticket_lines = []
    for n in issue_refs:
        ticket_lines.append(f"- #{n}")
    if not ticket_lines:
        ticket_lines.append("- (no closes/fixes/resolves refs found in commits)")

    # Changes by area section
    area_counts = group_by_area(changed_files)
    area_lines = []
    for area, count in area_counts.items():
        area_lines.append(f"- `{area}`: {count} file{'s' if count != 1 else ''}")
    if not area_lines:
        area_lines.append("- (no file changes detected)")

    # Recent commits section (last 20)
    recent = commits[:20]
    commit_lines = []
    for sha, subject in recent:
        commit_lines.append(f"- `{sha}` {subject}")
    if not commit_lines:
        commit_lines.append("- (no commits)")

    # Summary one-liner
    if sprint_numbers:
        sprint_range = (
            f"sprint-{sprint_numbers[0]}"
            if len(sprint_numbers) == 1
            else f"sprint-{sprint_numbers[0]} through sprint-{sprint_numbers[-1]}"
        )
        summary = f"Shipping {len(commits)} commit{'s' if len(commits) != 1 else ''} from {sprint_range} to master."
    else:
        summary = f"Shipping {len(commits)} commit{'s' if len(commits) != 1 else ''} from develop to master."

    script_ref = "scripts/promote_to_master.py"

    body = f"""## Summary

{summary}

## Sprints included

{chr(10).join(sprints_lines)}

## Tickets shipping

{chr(10).join(ticket_lines)}

## Changes by area

{chr(10).join(area_lines)}

## Recent commits

{chr(10).join(commit_lines)}

## What to verify before merge

- [ ] Run dashboard locally: `python -m uvicorn server:app --reload` from `apps/dashboard/`
- [ ] Check `/api/health` returns `{{"status": "ok"}}`
- [ ] Verify all UAT-approved tickets are closed on GitHub
- [ ] No regressions in sprint view (sprints load, tickets display correctly)
- [ ] All tests pass: `pytest apps/dashboard/tests/ -q`

## Generated automatically

Created by [`{script_ref}`]({script_ref}) at {now_str}
"""
    return body


# ── log writing ───────────────────────────────────────────────────────────────

def write_log(pr_body: str, gh_output: str, timestamp: str) -> Path:
    """Write promote log to .commander/logs/promote-<timestamp>.log"""
    COMMANDER_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = COMMANDER_LOGS_DIR / f"promote-{timestamp}.log"
    log_content = f"=== PR Body ===\n{pr_body}\n\n=== gh output ===\n{gh_output}\n"
    log_path.write_text(log_content, encoding="utf-8")
    return log_path


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(
        description="Create a draft PR from develop → master with sprint summary and changelog.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--dry-run", action="store_true", help="Print PR body without making API calls")
    p.add_argument("--title", default=None, help="Override auto-generated PR title")
    state_group = p.add_mutually_exclusive_group()
    state_group.add_argument("--draft", dest="draft", action="store_true", default=True, help="Create PR as draft (default)")
    state_group.add_argument("--ready", dest="draft", action="store_false", help="Create PR as ready-for-review")
    args = p.parse_args()

    # Pre-condition checks (exit on failure; dry-run skips some)
    if not args.dry_run:
        check_preconditions()
    else:
        # For dry-run, still check git state but skip the gh API call
        ok, _ = _try("git", "diff", "--quiet", "HEAD")
        if not ok:
            print("Uncommitted changes detected; commit or stash first.", file=sys.stderr)
            sys.exit(1)
        _run("git", "fetch", "origin", check=False)

    # Gather data
    commits = get_commits()
    commit_count = len(commits)
    issue_refs = extract_issue_refs(commits)
    sprint_numbers = extract_sprint_numbers(commits)
    changed_files = get_changed_files()

    # Generate title
    title = args.title or generate_title(commit_count, sprint_numbers)

    # Build PR body
    pr_body = build_pr_body(
        commits=commits,
        issue_refs=issue_refs,
        sprint_numbers=sprint_numbers,
        changed_files=changed_files,
        dry_run=args.dry_run,
    )

    if args.dry_run:
        print("=== DRY RUN — no GitHub API calls will be made ===\n")
        print(f"Title: {title}\n")
        print(pr_body)
        draft_flag = "--draft" if args.draft else ""
        print(
            f"\nWould run:\n"
            f"  gh pr create --base master --head develop --title '{title}' "
            f"{draft_flag} --body-file -"
        )
        return

    # Create the PR
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    cmd = [
        "gh", "pr", "create",
        "--base", "master",
        "--head", "develop",
        "--title", title,
        "--body", pr_body,
    ]
    if args.draft:
        cmd.append("--draft")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )
        gh_output = result.stdout.strip() + result.stderr.strip()
        pr_url = result.stdout.strip()
    except subprocess.CalledProcessError as e:
        err = e.stderr.strip() or e.stdout.strip() or "unknown error"
        print(f"GitHub API call failed: {err}", file=sys.stderr)
        # Still write a log with the failure info
        log_path = write_log(pr_body, f"ERROR: {err}", timestamp)
        print(f"Log written to {log_path}", file=sys.stderr)
        sys.exit(2)

    # Write log
    log_path = write_log(pr_body, gh_output, timestamp)
    print(pr_url)
    print(f"Log written to {log_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
