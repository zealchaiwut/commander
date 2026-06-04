#!/usr/bin/env python3
"""Check whether documentation is stale relative to recent code changes.

Usage:
    python3 scripts/check_docs_freshness.py [options]

Reads a .docs-freshness.yml config from the repo root (falls back to built-in
defaults if absent) and compares the set of files changed in a commit range
against the code→doc mapping.  Prints a JSON report and optionally POSTs
results to the Commander dashboard.

Exit codes:
    0 — no stale docs
    1 — one or more stale docs detected
"""

import argparse
import fnmatch
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

# ── Default mapping (used when .docs-freshness.yml is absent) ─────────────────

DEFAULT_MAPPINGS = [
    {"code": ["src/**", "api/**", "apps/**", "services/**"], "docs": ["README.md", "CLAUDE.md"]},
    {"code": ["db/migrations/**", "migrations/**"], "docs": ["SCHEMA.md", "SCHEMA"]},
]


def _load_config(repo_path: Path) -> dict:
    config_path = repo_path / ".docs-freshness.yml"
    if not config_path.exists():
        return {"mappings": DEFAULT_MAPPINGS}
    if not _YAML_AVAILABLE:
        print("[warn] PyYAML not installed — falling back to defaults", file=sys.stderr)
        return {"mappings": DEFAULT_MAPPINGS}
    with open(config_path) as f:
        data = yaml.safe_load(f) or {}
    if "mappings" not in data:
        data["mappings"] = DEFAULT_MAPPINGS
    return data


def _git_changed_files(repo_path: Path, base: str, head: str) -> list[str]:
    """Return list of files changed between base and head (relative to repo root)."""
    result = subprocess.run(
        ["git", "-C", str(repo_path), "diff", "--name-only", f"{base}...{head}"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        # Fallback: diff against single commit
        result = subprocess.run(
            ["git", "-C", str(repo_path), "diff-tree", "--no-commit-id", "-r", "--name-only", head],
            capture_output=True, text=True,
        )
    return [f.strip() for f in result.stdout.splitlines() if f.strip()]


def _matches_any(path: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if fnmatch.fnmatch(path, pat):
            return True
        # Also match if path starts with the prefix (e.g. "src/" matches "src/foo/bar.py")
        if pat.endswith("/**") and path.startswith(pat[:-3]):
            return True
    return False


def _doc_exists(repo_path: Path, doc_path: str) -> bool:
    return (repo_path / doc_path).exists()


def check_freshness(
    repo_path: Path,
    base: str,
    head: str,
    config: dict,
) -> dict:
    """Return dict with stale_docs and cleared_docs lists."""
    changed = set(_git_changed_files(repo_path, base, head))
    mappings = config.get("mappings", DEFAULT_MAPPINGS)
    exclude_paths = set(config.get("exclude", []))

    # Filter out explicitly excluded paths
    changed = {f for f in changed if not _matches_any(f, list(exclude_paths))}

    stale_docs: set[str] = set()
    cleared_docs: set[str] = set()

    for mapping in mappings:
        code_patterns = mapping.get("code", [])
        doc_paths = mapping.get("docs", [])

        code_changed = [f for f in changed if _matches_any(f, code_patterns)]
        if not code_changed:
            continue

        docs_changed = [d for d in doc_paths if d in changed]
        docs_not_changed = [d for d in doc_paths if d not in changed]

        for doc in docs_not_changed:
            if _doc_exists(repo_path, doc):
                stale_docs.add(doc)

        for doc in docs_changed:
            # Doc was updated in this changeset — clear any prior warning
            cleared_docs.add(doc)
            stale_docs.discard(doc)

    return {
        "stale_docs": sorted(stale_docs),
        "cleared_docs": sorted(cleared_docs - stale_docs),
    }


def _post_to_dashboard(dashboard_url: str, payload: dict) -> bool:
    url = dashboard_url.rstrip("/") + "/api/docs-freshness/check"
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status < 300
    except Exception as exc:
        print(f"[warn] failed to post to dashboard: {exc}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="Check documentation freshness")
    parser.add_argument("--repo-path", default=".", help="Path to git repo root")
    parser.add_argument("--base", default="HEAD~1", help="Base commit/ref for diff")
    parser.add_argument("--head", default="HEAD", help="Head commit/ref for diff")
    parser.add_argument("--trigger-ref", default="", help="Commit SHA or PR ref for dashboard record")
    parser.add_argument("--trigger-type", default="push", choices=["push", "pull_request"])
    parser.add_argument("--trigger-url", default="", help="URL of the triggering commit or PR")
    parser.add_argument("--repo", default="", help="owner/repo identifier for dashboard")
    parser.add_argument("--dashboard-url", default="", help="Commander dashboard base URL")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of human text")
    args = parser.parse_args()

    repo_path = Path(args.repo_path).resolve()
    config = _load_config(repo_path)

    result = check_freshness(repo_path, args.base, args.head, config)

    repo = args.repo
    if not repo:
        # Auto-detect from git remote
        r = subprocess.run(
            ["git", "-C", str(repo_path), "remote", "get-url", "origin"],
            capture_output=True, text=True,
        )
        remote = r.stdout.strip()
        if "github.com" in remote:
            # ssh: git@github.com:owner/repo.git  or https://github.com/owner/repo.git
            repo = remote.split("github.com")[-1].lstrip("/:").removesuffix(".git")

    trigger_ref = args.trigger_ref
    if not trigger_ref:
        r = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True,
        )
        trigger_ref = r.stdout.strip() or "unknown"

    payload = {
        "repo": repo,
        "trigger_ref": trigger_ref,
        "trigger_type": args.trigger_type,
        "trigger_url": args.trigger_url or None,
        "stale_docs": result["stale_docs"],
        "cleared_docs": result["cleared_docs"],
    }

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        if result["stale_docs"]:
            print(f"[stale-docs] {len(result['stale_docs'])} stale doc(s) detected:")
            for doc in result["stale_docs"]:
                print(f"  - {doc}")
        else:
            print("[stale-docs] All docs are up to date.")
        if result["cleared_docs"]:
            print(f"[stale-docs] {len(result['cleared_docs'])} doc(s) cleared:")
            for doc in result["cleared_docs"]:
                print(f"  + {doc}")

    if args.dashboard_url:
        ok = _post_to_dashboard(args.dashboard_url, payload)
        if not args.json:
            status = "posted" if ok else "failed to post"
            print(f"[stale-docs] dashboard: {status}")

    sys.exit(1 if result["stale_docs"] else 0)


if __name__ == "__main__":
    main()
