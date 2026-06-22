#!/usr/bin/env python3
"""Create a GitHub issue and assign it to a sprint.

Usage:
  python3 scripts/create_ticket.py \
    --title "Fix login bug" \
    --body  "Steps to reproduce..." \
    --sprint 1 \
    --labels "bug,backend" \
    --attachment /path/to/file.png \
    --attachment /path/to/spec.txt

Prints:  #<number> <url>
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

_DASHBOARD_DIR = Path(__file__).parent.parent / "apps" / "dashboard"
sys.path.insert(0, str(_DASHBOARD_DIR))
import github_client  # noqa: E402

# Definition-of-Ready template — all four canonical sections scaffolded.
# Manual tickets should start from this body so they are DoR-compliant on
# creation. BA-generated tickets populate Design Refs from the project's
# DESIGN.md; manual authors should fill each section before filing.
TEMPLATE_BODY = """\
## What & Why

<!-- What are we building and why does it matter? -->

## Acceptance Criteria

- [ ] <criterion 1 — independently testable, phrased as "System does X">
- [ ] <criterion 2>

## Design Refs

- Architecture Overview
- Sprint Lifecycle — Composite Key Invariant

## UAT Test Steps

1. <action step>
   **Expected:** <observable outcome>

2. <action step>
   **Expected:** <observable outcome>

## Out of Scope

- <explicit exclusion to prevent scope creep>
"""


def _load_env():
    env = _DASHBOARD_DIR / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
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


def main():
    _load_env()

    parser = argparse.ArgumentParser(description="Create a GitHub issue")
    parser.add_argument("--title",  required=True)
    parser.add_argument("--body",   default="")
    parser.add_argument("--sprint", type=int, default=None)
    parser.add_argument("--labels", default="", help="Comma-separated extra labels")
    parser.add_argument(
        "--attachment", dest="attachments", action="append", metavar="PATH",
        help="File to attach (repeatable); copied to references/issue-<N>/",
    )
    args = parser.parse_args()

    # Validate all attachment paths before creating the issue
    attachment_paths = []
    for p in (args.attachments or []):
        path = Path(p)
        if not path.exists():
            sys.exit(f"attachment not found: {p}")
        attachment_paths.append(path)

    try:
        repo = github_client.repo()
    except ValueError as e:
        sys.exit(str(e))

    labels = []
    if args.sprint is not None:
        labels.append(f"sprint-{args.sprint}")
    if args.labels:
        labels += [lbl.strip() for lbl in args.labels.split(",") if lbl.strip()]

    result = subprocess.run(
        ["gh", "issue", "create",
         "--repo",  repo,
         "--title", args.title,
         "--body",  args.body,
         *(["--label", ",".join(labels)] if labels else [])],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        sys.exit(f"Error: {result.stderr.strip()}")

    url    = result.stdout.strip()
    number = url.rstrip("/").split("/")[-1]

    if attachment_paths:
        root = _repo_root()
        ref_dir = root / "references" / f"issue-{number}"
        ref_dir.mkdir(parents=True, exist_ok=True)

        gitkeep = root / "references" / ".gitkeep"
        if not gitkeep.exists():
            gitkeep.touch()

        for src in attachment_paths:
            shutil.copy2(src, ref_dir / src.name)

        subprocess.run(
            ["git", "add", "references/.gitkeep", f"references/issue-{number}"],
            cwd=str(root), check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", f"chore: add attachments for issue #{number}"],
            cwd=str(root), check=True,
        )

        links = "\n".join(
            f"[{src.name}](references/issue-{number}/{src.name})"
            for src in attachment_paths
        )
        updated_body = args.body + f"\n\n## Attachments\n\n{links}"

        subprocess.run(
            ["gh", "issue", "edit", number,
             "--repo", repo,
             "--body", updated_body],
            capture_output=True, text=True, check=True,
        )

    sys.stdout.write(str(f"#{number} {url}") + "\n")


if __name__ == "__main__":
    main()
