#!/usr/bin/env python3
"""Backfill the sprint-summary label on legacy summary issues.

Reads projects.json, finds open issues whose title matches
  ^Sprint N[.M] Executive Summary$
and adds the 'sprint-summary' label to any that don't already have it.

Default: dry-run (prints what would change).
Pass --apply to actually mutate labels.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

PROJECTS_FILE = Path(__file__).parent.parent / "apps" / "dashboard" / "projects.json"
TITLE_RE = re.compile(r"^Sprint \d+(\.\d+)?\s+Executive Summary$")
LABEL = "sprint-summary"


def list_open_issues(repo: str) -> list[dict]:
    result = subprocess.run(
        [
            "gh", "issue", "list",
            "--repo", repo,
            "--state", "open",
            "--json", "number,title,labels",
            "--limit", "200",
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"  [warn] gh issue list failed for {repo}: {result.stderr.strip()}", file=sys.stderr)
        return []
    return json.loads(result.stdout or "[]")


def add_label(repo: str, issue_num: int, dry_run: bool) -> bool:
    if dry_run:
        return True
    result = subprocess.run(
        ["gh", "issue", "edit", str(issue_num), "--repo", repo, "--add-label", LABEL],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"  [error] failed to add label to #{issue_num}: {result.stderr.strip()}", file=sys.stderr)
        return False
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Actually add labels (default: dry-run)")
    args = parser.parse_args()
    dry_run = not args.apply

    if dry_run:
        print("DRY RUN — no labels will be changed. Pass --apply to mutate.")
        print()

    if not PROJECTS_FILE.exists():
        print(f"projects.json not found at {PROJECTS_FILE}", file=sys.stderr)
        sys.exit(1)

    projects = json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
    total_missing = 0
    repos_affected = 0

    for proj in projects:
        repo = proj.get("repo")
        if not repo:
            continue

        issues = list_open_issues(repo)
        missing = []
        for iss in issues:
            if not TITLE_RE.match(iss.get("title", "") or ""):
                continue
            has_label = any(lbl["name"] == LABEL for lbl in iss.get("labels", []))
            if not has_label:
                missing.append(iss)

        if not missing:
            print(f"{repo}: no issues missing the label")
            continue

        repos_affected += 1
        total_missing += len(missing)
        print(f"{repo}: {len(missing)} issue(s) missing '{LABEL}':")
        for iss in missing:
            action = "  [would add]" if dry_run else "  [adding]"
            print(f"  {action} #{iss['number']}: {iss['title']}")
            if not dry_run:
                ok = add_label(repo, iss["number"], dry_run=False)
                if ok:
                    print(f"    -> label added")

    print()
    if dry_run:
        print(
            f"Found {total_missing} summary issue(s) missing the label "
            f"across {repos_affected} project(s)."
        )
        if total_missing > 0:
            print("Run with --apply to add the label.")
    else:
        print(f"Done. Processed {total_missing} issue(s) across {repos_affected} project(s).")


if __name__ == "__main__":
    main()
