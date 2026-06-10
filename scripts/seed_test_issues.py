#!/usr/bin/env python3
"""Seed the sandbox GitHub repo with realistic mock issues for tester isolation.

Usage:
    python3 scripts/seed_test_issues.py              # idempotent — create missing issues only
    python3 scripts/seed_test_issues.py --wipe       # close all open issues, then create fresh
    python3 scripts/seed_test_issues.py --append     # create all 15 regardless of duplicates
    python3 scripts/seed_test_issues.py --hard-delete # alias for --wipe (GitHub cannot delete issues)
    python3 scripts/seed_test_issues.py --include-branches  # also create feature branches
    python3 scripts/seed_test_issues.py --dry-run    # print what would happen, no API calls

The fixture file is scripts/test_fixtures/issues.yaml.
All operations target the repo from GITHUB_ISSUE_TEST_REPO (or COMMANDER_TEST_REPO)
env var.  Set one of these before running, or pass --repo explicitly.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.stderr.write(str("ERROR: PyYAML not installed. Run: pip install pyyaml") + "\n")
    sys.exit(1)

_DASHBOARD_DIR = Path(__file__).parent.parent / "apps" / "dashboard"
sys.path.insert(0, str(_DASHBOARD_DIR))

from dotenv import load_dotenv
load_dotenv(_DASHBOARD_DIR / ".env")

from config import TEST_GITHUB_REPO

FIXTURE_FILE = Path(__file__).parent / "test_fixtures" / "issues.yaml"

# All label names referenced by fixtures — script ensures these exist.
REQUIRED_LABELS: dict[str, tuple[str, str]] = {
    "backlog":      ("ededed", "Backlog items"),
    "in-progress":  ("0075ca", "Currently being worked on"),
    "SIT":          ("e4e669", "System integration testing"),
    "UAT":          ("ff9f00", "User acceptance testing"),
    "UAT-approved": ("0e8a16", "UAT sign-off complete"),
    "needs-rework": ("d93f0b", "Needs to be fixed"),
    "blocked":      ("b60205", "Blocked by dependency"),
    "sprint-1":     ("0052CC", "Sprint 1 issues"),
    "sprint-2":     ("0052CC", "Sprint 2 issues"),
    "sprint-3":     ("0052CC", "Sprint 3 issues"),
    "size-S":       ("bfd4f2", "Small — 1–5 min"),
    "size-M":       ("bfd4f2", "Medium — ~15 min"),
    "size-L":       ("bfd4f2", "Large — ~30 min"),
}


def _gh(*args, capture: bool = True) -> subprocess.CompletedProcess:
    cmd = ["gh", *args]
    if capture:
        return subprocess.run(cmd, capture_output=True, text=True, check=True)
    return subprocess.run(cmd, text=True, check=True)


def _gh_json(*args) -> object:
    r = _gh(*args)
    return json.loads(r.stdout)


def log(msg: str) -> None:
    sys.stdout.write(str(msg) + "\n")
    sys.stdout.flush()


def validate_fixture(data: dict) -> list[dict]:
    """Validate fixture YAML and return the issues list."""
    if not isinstance(data, dict):
        raise ValueError("Fixture YAML must be a mapping with an 'issues' key")
    if "issues" not in data:
        raise ValueError("Fixture YAML missing 'issues' key")
    issues = data["issues"]
    if not isinstance(issues, list):
        raise ValueError("'issues' must be a list")
    for i, issue in enumerate(issues):
        if not isinstance(issue, dict):
            raise ValueError(f"Issue at index {i} must be a mapping")
        if "title" not in issue:
            raise ValueError(f"Issue at index {i} missing required 'title' field")
        if "body" not in issue:
            raise ValueError(f"Issue at index {i} missing required 'body' field")
        state = issue.get("state", "open")
        if state not in ("open", "closed"):
            raise ValueError(f"Issue {i!r} has invalid state {state!r} (must be 'open' or 'closed')")
    return issues


def ensure_labels(repo: str, dry_run: bool) -> None:
    """Create any missing labels on the sandbox repo."""
    log(f"Ensuring labels exist on {repo} ...")
    try:
        existing_raw = _gh_json("label", "list", "--repo", repo, "--json", "name", "--limit", "200")
        existing = {lbl["name"] for lbl in existing_raw}
    except subprocess.CalledProcessError as e:
        log(f"  WARNING: could not list labels: {e.stderr.strip()}")
        existing = set()

    for name, (color, description) in REQUIRED_LABELS.items():
        if name in existing:
            continue
        if dry_run:
            log(f"  [dry-run] would create label '{name}' (#{color})")
        else:
            try:
                _gh("label", "create", name, "--repo", repo,
                    "--color", color, "--description", description)
                log(f"  created label '{name}'")
            except subprocess.CalledProcessError:
                log(f"  label '{name}' already exists (race condition ignored)")


def list_open_issues(repo: str) -> list[dict]:
    """Return all open issues on the repo."""
    try:
        return _gh_json(
            "issue", "list", "--repo", repo,
            "--state", "open",
            "--json", "number,title",
            "--limit", "200",
        )
    except subprocess.CalledProcessError:
        return []


def close_all_open(repo: str, dry_run: bool) -> None:
    """Close every open issue on the sandbox repo."""
    open_issues = list_open_issues(repo)
    if not open_issues:
        log("  No open issues to close.")
        return
    for issue in open_issues:
        num = issue["number"]
        title = issue["title"]
        if dry_run:
            log(f"  [dry-run] would close issue #{num}: {title}")
        else:
            _gh("issue", "close", str(num), "--repo", repo,
                "--comment", "Closed by seed_test_issues.py --wipe")
            log(f"  closed issue #{num}: {title}")


def existing_titles(repo: str) -> set[str]:
    """Return titles of all open issues (for idempotency check)."""
    issues = list_open_issues(repo)
    return {i["title"] for i in issues}


def create_issue(repo: str, issue: dict, dry_run: bool) -> int | None:
    """Create a single issue and return its number (or None for dry-run)."""
    title = issue["title"]
    body = issue.get("body", "")
    labels = issue.get("labels", [])
    state = issue.get("state", "open")

    label_args = []
    if labels:
        label_args = ["--label", ",".join(labels)]

    if dry_run:
        log(f"  [dry-run] would create issue: {title!r} labels={labels} state={state}")
        return None

    cmd = ["issue", "create", "--repo", repo, "--title", title, "--body", body]
    cmd += label_args
    url = _gh(*cmd).stdout.strip()
    number = int(url.rstrip("/").split("/")[-1])
    log(f"  created issue #{number}: {title}")

    if state == "closed":
        _gh("issue", "close", str(number), "--repo", repo)
        log(f"  closed issue #{number} (fixture state=closed)")

    comments = issue.get("comments") or []
    for comment_body in comments:
        _gh("issue", "comment", str(number), "--repo", repo, "--body", comment_body)
        log(f"  added comment to issue #{number}")

    return number


def create_feature_branch(repo: str, issue_num: int, title: str, dry_run: bool) -> None:
    """Create a feature branch for an issue on the sandbox repo."""
    slug = title.lower()
    slug = "".join(c if c.isalnum() else "-" for c in slug)
    slug = slug[:40].strip("-")
    branch = f"feature/{issue_num}-{slug}"
    if dry_run:
        log(f"  [dry-run] would create branch {branch} on {repo}")
        return
    try:
        _gh("api", f"repos/{repo}/git/refs",
            "--method", "POST",
            "--field", f"ref=refs/heads/{branch}",
            "--field", f"sha=$(gh api repos/{repo}/git/refs/heads/main --jq '.object.sha')",
            )
        log(f"  created branch {branch}")
    except subprocess.CalledProcessError:
        # Fall back: use gh api to get default branch SHA then create ref
        try:
            default_branch_info = _gh_json("api", f"repos/{repo}")
            default_branch = default_branch_info.get("default_branch", "main")
            ref_info = _gh_json("api", f"repos/{repo}/git/refs/heads/{default_branch}")
            sha = ref_info["object"]["sha"]
            _gh("api", f"repos/{repo}/git/refs",
                "--method", "POST",
                "--input", "-",
                )
            import json as _json
            payload = _json.dumps({"ref": f"refs/heads/{branch}", "sha": sha})
            r = subprocess.run(
                ["gh", "api", f"repos/{repo}/git/refs", "--method", "POST", "--input", "-"],
                input=payload, capture_output=True, text=True,
            )
            if r.returncode == 0:
                log(f"  created branch {branch}")
            else:
                log(f"  WARNING: could not create branch {branch}: {r.stderr.strip()}")
        except Exception as exc:
            log(f"  WARNING: could not create branch {branch}: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed the commander sandbox repo with mock issues."
    )
    parser.add_argument("--wipe", action="store_true",
                        help="Close all open issues first, then create fresh")
    parser.add_argument("--append", action="store_true",
                        help="Create all fixture issues without idempotency check")
    parser.add_argument("--hard-delete", action="store_true",
                        help="Alias for --wipe (GitHub API cannot delete issues)")
    parser.add_argument("--include-branches", action="store_true",
                        help="Also create feature branches for each issue")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print planned operations without making any API calls")
    _default_repo = TEST_GITHUB_REPO or ""
    parser.add_argument(
        "--repo",
        default=_default_repo or None,
        required=not bool(_default_repo),
        help="Target repo (owner/repo). Defaults to GITHUB_ISSUE_TEST_REPO env var.",
    )
    args = parser.parse_args()

    repo = args.repo
    dry_run = args.dry_run
    wipe = args.wipe or args.hard_delete

    log(f"Target repo: {repo}")
    if dry_run:
        log("Mode: dry-run (no API calls will be made)")
    elif wipe:
        log("Mode: wipe (close all open, then seed fresh)")
    elif args.append:
        log("Mode: append (create all, skip idempotency check)")
    else:
        log("Mode: idempotent (create missing issues only)")

    # Step 1 — validate fixture file
    if not FIXTURE_FILE.exists():
        sys.stderr.write(str(f"ERROR: Fixture file not found: {FIXTURE_FILE}") + "\n")
        sys.exit(1)

    with FIXTURE_FILE.open() as f:
        raw = yaml.safe_load(f)

    try:
        issues = validate_fixture(raw)
    except ValueError as exc:
        sys.stderr.write(str(f"ERROR: Invalid fixture file: {exc}") + "\n")
        sys.exit(1)

    log(f"Fixture validated: {len(issues)} issues")

    # Step 2 — ensure labels exist
    ensure_labels(repo, dry_run)

    # Step 3 — optionally wipe
    if wipe:
        log(f"Closing all open issues on {repo} ...")
        close_all_open(repo, dry_run)

    # Step 4 — determine which issues to create
    if args.append or wipe:
        to_create = issues
    else:
        # Idempotent: skip issues whose titles already exist
        known = existing_titles(repo)
        to_create = [i for i in issues if i["title"] not in known]
        skipped = len(issues) - len(to_create)
        if skipped:
            log(f"Skipping {skipped} issue(s) that already exist (idempotent run)")
        if not to_create:
            log("Nothing to do — all fixture issues already present.")
            return

    # Step 5 — create issues
    log(f"Creating {len(to_create)} issue(s) ...")
    created: list[tuple[int | None, str]] = []
    for issue in to_create:
        num = create_issue(repo, issue, dry_run)
        created.append((num, issue["title"]))

    # Step 6 — optionally create branches
    if args.include_branches and not dry_run:
        log("Creating feature branches ...")
        for num, title in created:
            if num is not None:
                create_feature_branch(repo, num, title, dry_run)
    elif args.include_branches and dry_run:
        for _, title in created:
            log(f"  [dry-run] would create branch for: {title!r}")

    log(f"Done. {len(to_create)} issue(s) processed on {repo}.")


if __name__ == "__main__":
    main()
