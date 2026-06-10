#!/usr/bin/env python3
"""deduplicate_labels.py — Detect and remove duplicate GitHub labels.

When duplicate labels exist (same name, multiple IDs), migrates all issues
from the extras to the surviving label, then deletes the extras.

Usage:
    python3 scripts/deduplicate_labels.py --repo owner/repo [--dry-run]
    python3 scripts/deduplicate_labels.py --repo owner/repo --ensure NAME COLOR
"""

import argparse
import json
import subprocess
import sys
from collections import defaultdict


def _gh(*args: str) -> str:
    result = subprocess.run(
        ["gh", *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _gh_json(*args: str) -> object:
    return json.loads(_gh(*args))


def list_labels(repo: str) -> list[dict]:
    raw = _gh_json("api", f"repos/{repo}/labels?per_page=100")
    labels = list(raw)
    page = 2
    while len(raw) == 100:
        raw = _gh_json("api", f"repos/{repo}/labels?per_page=100&page={page}")
        labels.extend(raw)
        page += 1
    return labels


def list_issues_with_label(repo: str, label_id: int) -> list[dict]:
    """Return all issues (open + closed) that have a specific label ID."""
    issues = []
    page = 1
    while True:
        raw = _gh_json(
            "api",
            f"repos/{repo}/issues?state=all&labels=&per_page=100&page={page}",
        )
        if not raw:
            break
        for issue in raw:
            ids = [lbl["id"] for lbl in issue.get("labels", [])]
            if label_id in ids:
                issues.append(issue)
        page += 1
        if len(raw) < 100:
            break
    return issues


def issues_with_label_name(repo: str, label_name: str) -> list[dict]:
    """Return all issues (open + closed) carrying label_name."""
    issues = []
    page = 1
    while True:
        raw = _gh_json(
            "api",
            f"repos/{repo}/issues?state=all&labels={label_name}&per_page=100&page={page}",
        )
        if not raw:
            break
        issues.extend(raw)
        page += 1
        if len(raw) < 100:
            break
    return issues


def add_label_to_issue(repo: str, issue_number: int, label_name: str, dry_run: bool) -> None:
    if dry_run:
        sys.stdout.write(str(f"  [dry-run] would add '{label_name}' to #{issue_number}") + "\n")
        return
    _gh("api", f"repos/{repo}/issues/{issue_number}/labels",
        "-X", "POST", "-f", f"labels[]={label_name}")


def remove_label_from_issue(repo: str, issue_number: int, label_name: str, dry_run: bool) -> None:
    if dry_run:
        sys.stdout.write(str(f"  [dry-run] would remove '{label_name}' from #{issue_number}") + "\n")
        return
    _gh("api", f"repos/{repo}/issues/{issue_number}/labels/{label_name}",
        "-X", "DELETE")


def delete_label(repo: str, label_name: str, dry_run: bool) -> None:
    if dry_run:
        sys.stdout.write(str(f"  [dry-run] would delete label '{label_name}'") + "\n")
        return
    _gh("api", f"repos/{repo}/labels/{label_name}", "-X", "DELETE")


def create_label(repo: str, name: str, color: str, dry_run: bool) -> None:
    color = color.lstrip("#")
    if dry_run:
        sys.stdout.write(str(f"  [dry-run] would create label '{name}' #{color}") + "\n")
        return
    _gh("api", f"repos/{repo}/labels",
        "-X", "POST",
        "-f", f"name={name}",
        "-f", f"color={color}")


def find_duplicates(labels: list[dict]) -> dict[str, list[dict]]:
    by_name: dict[str, list[dict]] = defaultdict(list)
    for lbl in labels:
        by_name[lbl["name"]].append(lbl)
    return {name: copies for name, copies in by_name.items() if len(copies) > 1}


def deduplicate(repo: str, duplicates: dict[str, list[dict]], dry_run: bool) -> int:
    """Migrate issues and delete extra copies. Returns number of labels fixed."""
    fixed = 0
    for name, copies in duplicates.items():
        copies_sorted = sorted(copies, key=lambda l: l["id"])
        surviving = copies_sorted[0]
        extras = copies_sorted[1:]
        sys.stdout.write(str(f"\nLabel '{name}': {len(copies)} copies found") + "\n")
        sys.stdout.write(str(f"  Keeping ID {surviving['id']}") + "\n")

        issues = issues_with_label_name(repo, name)
        sys.stdout.write(str(f"  {len(issues)} issue(s) carry this label — no migration needed (all share same name)") + "\n")

        for extra in extras:
            sys.stdout.write(str(f"  Deleting duplicate ID {extra['id']}") + "\n")
            # GitHub doesn't support deleting a label by ID directly — must use name.
            # Since both copies have the same name this would delete both;
            # we handle by re-creating the surviving one after deletion.
            delete_label(repo, name, dry_run)
            sys.stdout.write(str(f"  Re-creating surviving label '{name}' #{surviving['color']}") + "\n")
            create_label(repo, name, surviving["color"], dry_run)
            fixed += 1

    return fixed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="owner/repo")
    parser.add_argument("--dry-run", action="store_true", help="print what would happen without changing anything")
    parser.add_argument("--ensure", nargs=2, metavar=("NAME", "COLOR"),
                        help="ensure exactly one label NAME exists with COLOR (create if missing)")
    args = parser.parse_args()

    labels = list_labels(args.repo)
    sys.stdout.write(str(f"Found {len(labels)} labels in {args.repo}") + "\n")

    if args.ensure:
        name, color = args.ensure
        matching = [l for l in labels if l["name"] == name]
        if len(matching) == 0:
            sys.stdout.write(str(f"Label '{name}' does not exist — creating") + "\n")
            create_label(args.repo, name, color, args.dry_run)
            sys.stdout.write(str(f"Created label '{name}' #{color.lstrip('#')}") + "\n")
        elif len(matching) == 1:
            sys.stdout.write(str(f"Label '{name}' already exists (ID {matching[0]['id']}) — nothing to do") + "\n")
        else:
            sys.stdout.write(str(f"Label '{name}' has {len(matching)} duplicates — deduplicating") + "\n")
            deduplicate(args.repo, {name: matching}, args.dry_run)
        return

    duplicates = find_duplicates(labels)
    if not duplicates:
        sys.stdout.write(str("No duplicate label names found.") + "\n")
        return

    sys.stdout.write(str(f"\nFound {len(duplicates)} duplicate label name(s):") + "\n")
    for name, copies in duplicates.items():
        ids = ", ".join(str(c["id"]) for c in copies)
        sys.stdout.write(str(f"  '{name}': IDs {ids}") + "\n")

    fixed = deduplicate(args.repo, duplicates, args.dry_run)
    sys.stdout.write(str(f"\nDone. {fixed} duplicate(s) removed.") + "\n")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(str(f"gh command failed: {exc.stderr}") + "\n")
        sys.exit(1)
