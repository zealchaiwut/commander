#!/usr/bin/env python3
"""Migrate existing references/issue-N/* files from main history to the `attachments` branch.

This script is IDEMPOTENT: running it twice will not duplicate files.
It is NOT run automatically — invoke manually after upgrading to the new
attachment-branch flow (issue #188).

Usage:
  python3 scripts/migrate_attachments_to_branch.py [--repo owner/repo] [--dry-run]

What it does:
  1. Scans the working clone's `references/` directory for issue folders.
  2. Reads each file from the filesystem (current working tree).
  3. Commits all found files to the `attachments` branch via the same bare-cache
     mechanism the dashboard uses.
  4. Does NOT delete the `references/` folder from the main history (non-destructive).

Assumptions:
  - The `attachments` branch already exists (created by the dashboard on first upload).
    If not, run `gh api repos/<owner>/<repo>/branches/attachments` to check and
    create it manually if needed.
  - You are running from the repo root or a subdirectory of it.
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

_DASHBOARD_DIR = Path(__file__).parent.parent / "apps" / "dashboard"
sys.path.insert(0, str(_DASHBOARD_DIR))

import github_client

_ATTACHMENTS_BRANCH = "attachments"
_ATTACHMENTS_CACHE_DIR = _DASHBOARD_DIR / "runtime" / "attachments-cache"


def _load_env():
    env = _DASHBOARD_DIR / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


def _repo_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        sys.exit("Could not determine git repo root")
    return Path(result.stdout.strip())


def _get_cache_dir(repo: str) -> Path:
    safe = repo.replace("/", "-")
    return _ATTACHMENTS_CACHE_DIR / safe


def _ensure_attachments_branch(repo: str) -> None:
    result = subprocess.run(
        ["gh", "api", f"repos/{repo}/branches/{_ATTACHMENTS_BRANCH}"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return
    sys.stdout.write(str(f"  Creating orphan '{_ATTACHMENTS_BRANCH}' branch...") + "\n")
    url_result = subprocess.run(
        ["gh", "repo", "view", repo, "--json", "url", "-q", ".url"],
        capture_output=True, text=True,
    )
    remote_url = url_result.stdout.strip() or f"https://github.com/{repo}.git"
    with tempfile.TemporaryDirectory() as tmpdir:
        subprocess.run(["git", "clone", "--depth=1", remote_url, tmpdir],
                       capture_output=True)
        subprocess.run(["git", "checkout", "--orphan", _ATTACHMENTS_BRANCH],
                       capture_output=True, cwd=tmpdir)
        subprocess.run(["git", "rm", "-rf", "."], capture_output=True, cwd=tmpdir)
        subprocess.run(["git", "commit", "--allow-empty", "-m", "init attachments branch"],
                       capture_output=True, cwd=tmpdir)
        subprocess.run(["git", "push", "origin", _ATTACHMENTS_BRANCH],
                       cwd=tmpdir, check=True)


def _init_cache(repo: str) -> Path:
    cache_dir = _get_cache_dir(repo)
    if cache_dir.exists():
        subprocess.run(
            ["git", "fetch", "origin", _ATTACHMENTS_BRANCH],
            capture_output=True, cwd=str(cache_dir),
        )
        return cache_dir
    cache_dir.parent.mkdir(parents=True, exist_ok=True)
    url_result = subprocess.run(
        ["gh", "repo", "view", repo, "--json", "url", "-q", ".url"],
        capture_output=True, text=True,
    )
    remote_url = url_result.stdout.strip() or f"https://github.com/{repo}.git"
    subprocess.run(
        ["git", "clone", "--bare", "--branch", _ATTACHMENTS_BRANCH,
         remote_url, str(cache_dir)],
        check=True,
    )
    return cache_dir


def _list_existing(cache_dir: Path, issue_number: int) -> set[str]:
    prefix = f"references/issue-{issue_number}/"
    result = subprocess.run(
        ["git", "ls-tree", "--name-only", f"refs/heads/{_ATTACHMENTS_BRANCH}", prefix],
        capture_output=True, text=True, cwd=str(cache_dir),
    )
    if result.returncode != 0:
        return set()
    files = set()
    for line in result.stdout.splitlines():
        name = line.strip()
        if "/" in name:
            name = name.rsplit("/", 1)[-1]
        if name:
            files.add(name)
    return files


def _commit_files(cache_dir: Path, issue_number: int,
                  file_data: list[tuple[str, bytes]]) -> None:
    """Commit file_data to the attachments branch bare cache and push."""
    parent_result = subprocess.run(
        ["git", "rev-parse", f"refs/heads/{_ATTACHMENTS_BRANCH}"],
        capture_output=True, text=True, cwd=str(cache_dir),
    )
    parent_sha = parent_result.stdout.strip() if parent_result.returncode == 0 else None

    if parent_sha:
        tree_result = subprocess.run(
            ["git", "cat-file", "-p", parent_sha],
            capture_output=True, text=True, cwd=str(cache_dir),
        )
        parent_tree_sha = None
        for line in tree_result.stdout.splitlines():
            if line.startswith("tree "):
                parent_tree_sha = line.split()[1]
                break
    else:
        parent_tree_sha = None

    with tempfile.NamedTemporaryFile(delete=False, suffix=".idx") as tf:
        idx_path = tf.name

    try:
        env = {"GIT_INDEX_FILE": idx_path, "HOME": str(Path.home())}
        if parent_tree_sha:
            subprocess.run(
                ["git", "read-tree", parent_tree_sha],
                capture_output=True, cwd=str(cache_dir),
                env={**env, "GIT_DIR": str(cache_dir)},
            )
        for fname, content in file_data:
            dest_path = f"references/issue-{issue_number}/{fname}"
            hash_proc = subprocess.run(
                ["git", "hash-object", "-w", "--stdin"],
                input=content, capture_output=True, cwd=str(cache_dir),
            )
            blob_sha = hash_proc.stdout.strip().decode()
            subprocess.run(
                ["git", "update-index", "--add", "--cacheinfo",
                 f"100644,{blob_sha},{dest_path}"],
                capture_output=True, cwd=str(cache_dir),
                env={**env, "GIT_DIR": str(cache_dir)}, check=True,
            )
        write_result = subprocess.run(
            ["git", "write-tree"],
            capture_output=True, text=True, cwd=str(cache_dir),
            env={**env, "GIT_DIR": str(cache_dir)},
        )
        new_tree_sha = write_result.stdout.strip()
        commit_cmd = [
            "git", "commit-tree", new_tree_sha,
            "-m", f"chore(attachments): migrate files for issue #{issue_number}",
        ]
        if parent_sha:
            commit_cmd += ["-p", parent_sha]
        commit_result = subprocess.run(
            commit_cmd, capture_output=True, text=True, cwd=str(cache_dir),
        )
        new_sha = commit_result.stdout.strip()
        subprocess.run(
            ["git", "update-ref", f"refs/heads/{_ATTACHMENTS_BRANCH}", new_sha],
            capture_output=True, cwd=str(cache_dir), check=True,
        )
    finally:
        try:
            Path(idx_path).unlink()
        except Exception:
            pass

    # Push
    push = subprocess.run(
        ["git", "push", "origin",
         f"refs/heads/{_ATTACHMENTS_BRANCH}:refs/heads/{_ATTACHMENTS_BRANCH}"],
        capture_output=True, text=True, cwd=str(cache_dir),
    )
    if push.returncode != 0:
        sys.stderr.write(str(f"  WARNING: push failed: {push.stderr.strip()}") + "\n")


def main():
    _load_env()
    parser = argparse.ArgumentParser(description="Migrate references/ to attachments branch")
    parser.add_argument("--repo", default=None, help="owner/repo (auto-detected if not set)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be done, do not commit")
    args = parser.parse_args()

    try:
        repo = args.repo or github_client.repo()
    except ValueError as e:
        sys.exit(str(e))

    root = _repo_root()
    refs_dir = root / "references"
    if not refs_dir.exists():
        sys.stdout.write(str("No references/ directory found. Nothing to migrate.") + "\n")
        return

    issue_dirs = sorted(
        [d for d in refs_dir.iterdir() if d.is_dir() and re.match(r"issue-\d+$", d.name)],
        key=lambda d: int(d.name.split("-")[1]),
    )
    if not issue_dirs:
        sys.stdout.write(str("No issue directories found under references/. Nothing to migrate.") + "\n")
        return

    if not args.dry_run:
        sys.stdout.write(str(f"Ensuring '{_ATTACHMENTS_BRANCH}' branch exists on {repo}...") + "\n")
        _ensure_attachments_branch(repo)
        sys.stdout.write(str(f"Initializing bare-clone cache for {repo}...") + "\n")
        cache_dir = _init_cache(repo)
    else:
        cache_dir = None

    total = 0
    for issue_dir in issue_dirs:
        m = re.match(r"issue-(\d+)$", issue_dir.name)
        issue_number = int(m.group(1))
        files_here = [f for f in issue_dir.iterdir() if f.is_file()]
        if not files_here:
            continue

        if cache_dir:
            existing = _list_existing(cache_dir, issue_number)
        else:
            existing = set()

        to_commit: list[tuple[str, bytes]] = []
        for f in sorted(files_here):
            if f.name in existing:
                sys.stdout.write(str(f"  SKIP (already on branch): {issue_dir.name}/{f.name}") + "\n")
                continue
            if args.dry_run:
                sys.stdout.write(str(f"  WOULD COMMIT: {issue_dir.name}/{f.name} ({f.stat().st_size} bytes)") + "\n")
            else:
                sys.stdout.write(str(f"  Staging: {issue_dir.name}/{f.name}") + "\n")
                to_commit.append((f.name, f.read_bytes()))
                total += 1

        if to_commit and not args.dry_run:
            sys.stdout.write(str(f"  Committing {len(to_commit)} file(s) for issue #{issue_number}...") + "\n")
            _commit_files(cache_dir, issue_number, to_commit)

    if args.dry_run:
        sys.stdout.write(str("Dry run complete. No changes made.") + "\n")
    else:
        sys.stdout.write(str(f"Migration complete. {total} file(s) committed to '{_ATTACHMENTS_BRANCH}' branch.") + "\n")
        sys.stdout.write(str("The references/ folder in the main history has NOT been modified (non-destructive).") + "\n")


if __name__ == "__main__":
    main()
