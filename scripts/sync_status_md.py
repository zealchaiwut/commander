#!/usr/bin/env python3
"""Regenerate STATUS.md and commit if content changed.

Compares new content against the existing file (excluding the timestamp
line, which always differs). Only writes and commits when sprint progress
or goal text actually changed.

Exit codes:
  0 — STATUS.md updated and committed
  1 — no change detected, nothing committed
  2 — error

Usage:
    python3 scripts/sync_status_md.py [--repo owner/repo] [--out PATH] [--note TEXT]

--note TEXT: optional context appended to commit message, e.g. "#123 moved to UAT"
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(_SCRIPTS_DIR))
from generate_status import (  # noqa: E402
    _discover_commander_dir,
    _load_yaml_config,
    generate,
)


def _body(content: str) -> str:
    """Return content with the first two lines (timestamp + blank) stripped."""
    lines = content.splitlines()
    return "\n".join(lines[2:]) if len(lines) > 2 else "\n".join(lines)


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True, cwd=cwd)


def main() -> None:
    ap = argparse.ArgumentParser(description="Sync STATUS.md and commit if changed")
    ap.add_argument("--repo", default=None, help="owner/repo (auto-detected from sprint.yaml)")
    ap.add_argument("--out", default=None, help="Output path (default: <project-root>/STATUS.md)")
    ap.add_argument("--note", default=None, help="Context appended to commit message")
    args = ap.parse_args()

    commander_dir = _discover_commander_dir()
    cfg: dict = _load_yaml_config(commander_dir) if commander_dir else {}

    repo = args.repo or cfg.get("repo_name") or ""
    if not repo:
        print("Error: --repo required or set repo_name in .commander/sprint.yaml", file=sys.stderr)
        sys.exit(2)

    paths = cfg.get("paths") or {}
    sprints_dir_str = paths.get("sprints_dir", "")
    if sprints_dir_str:
        sprints_dir = Path(sprints_dir_str)
    elif commander_dir:
        sprints_dir = commander_dir / "sprints"
    else:
        sprints_dir = Path.cwd() / ".commander" / "sprints"

    project_root = commander_dir.parent if commander_dir else Path.cwd()
    out_path = Path(args.out).expanduser().resolve() if args.out else project_root / "STATUS.md"

    existing_body = _body(out_path.read_text(encoding="utf-8")) if out_path.exists() else ""

    # Generate new content to a temp file (generate() writes atomically itself)
    with tempfile.NamedTemporaryFile(
        mode="r", suffix=".md", dir=out_path.parent, delete=False
    ) as tmp:
        tmp_path = Path(tmp.name)

    try:
        generate(repo=repo, sprints_dir=sprints_dir, out_path=tmp_path)
        new_content = tmp_path.read_text(encoding="utf-8")
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass

    if _body(new_content) == existing_body:
        print("STATUS.md unchanged.")
        sys.exit(1)

    # Atomic write to the real path
    import os
    import tempfile as _tf
    fd, tmp_write = tempfile.mkstemp(dir=out_path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(new_content)
        os.replace(tmp_write, out_path)
    except Exception:
        try:
            os.unlink(tmp_write)
        except OSError:
            pass
        raise

    # Git commit
    r = _git("rev-parse", "--git-dir", cwd=project_root)
    if r.returncode != 0:
        print(f"STATUS.md written to {out_path} (not a git repo, skipping commit).")
        sys.exit(0)

    _git("add", str(out_path), cwd=project_root)

    r = _git("diff", "--cached", "--quiet", cwd=project_root)
    if r.returncode == 0:
        print("STATUS.md written but no staged diff, skipping commit.")
        sys.exit(1)

    msg = "chore: auto-sync STATUS.md — sprint progress updated"
    if args.note:
        msg += f" [{args.note}]"

    r = _git("commit", "-m", msg, cwd=project_root)
    if r.returncode != 0:
        print(f"git commit failed: {r.stderr.strip()}", file=sys.stderr)
        sys.exit(2)

    print(f"STATUS.md committed: {msg}")
    sys.exit(0)


if __name__ == "__main__":
    main()
