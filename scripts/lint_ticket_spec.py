#!/usr/bin/env python3
"""Lint a GitHub issue body against the canonical ticket-spec format (issue #1485).

Fetches issue N and prints a per-section present/missing summary to stdout.

Usage:
    python3 scripts/lint_ticket_spec.py --issue 1485 [--repo owner/repo]

Exit codes:
    0  all four sections present
    1  one or more sections missing
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from services.sprint_manager.ticket_spec import parse_ticket_spec

_ENV_FILE = _REPO_ROOT / "apps" / "dashboard" / ".env"

_SECTIONS = [
    ("acceptance_criteria", "Acceptance Criteria"),
    ("design_refs", "Design References"),
    ("test_plan", "UAT Test Steps / Test Plan"),
    ("out_of_scope", "Out of Scope"),
]


def _load_env() -> None:
    if not _ENV_FILE.exists():
        return
    for line in _ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


def _fetch_issue(issue_num: int, repo: str) -> dict:
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{repo}/issues/{issue_num}"],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"error: gh API call failed for issue #{issue_num} in {repo}", file=sys.stderr)
        if e.stderr:
            print(e.stderr.rstrip(), file=sys.stderr)
        sys.exit(1)
    raw = json.loads(result.stdout)
    return {
        "number": raw.get("number"),
        "title": raw.get("title", ""),
        "body": raw.get("body") or "",
    }


def _detect_repo() -> str:
    try:
        result = subprocess.run(
            ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return "zealchaiwut/commander"


def lint(issue_num: int, repo: str) -> bool:
    """Lint issue N. Returns True if all sections present."""
    issue = _fetch_issue(issue_num, repo)
    spec = parse_ticket_spec(issue["body"])

    print(f"Issue #{issue_num}: {issue['title']}")
    print()

    all_present = True
    for key, label in _SECTIONS:
        value = spec[key]
        present = bool(value)
        status = "✅ present" if present else "❌ missing"
        print(f"  {status}  {label}")
        if not present:
            all_present = False

    print()
    if all_present:
        print("All sections present.")
    else:
        missing = [label for key, label in _SECTIONS if not spec[key]]
        print(f"Missing sections: {', '.join(missing)}")

    return all_present


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Lint a GitHub issue against the canonical ticket-spec format."
    )
    parser.add_argument("--issue", type=int, required=True, help="Issue number to lint")
    parser.add_argument("--repo", default=None, help="owner/repo (default: auto-detect)")
    args = parser.parse_args()

    _load_env()
    repo = args.repo or _detect_repo()
    ok = lint(args.issue, repo)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
