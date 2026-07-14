#!/usr/bin/env python3
"""Generate docs/architecture/code-state.md — deterministic code-state snapshot.

Called by the sprint finish pipeline (generate_code_state_snapshot in
services/sprint_manager/code_state.py) after the documenter step.

Sections produced:
  1. Module Map     — top-level + key nested dirs with one-line purpose
  2. Recent Deltas  — files/areas changed by this sprint (git diff --name-only)
  3. Hot Files      — most-churned files in the last 90 days
  4. Generated      — timestamp + sprint label

Exit 0 on success, non-zero on error.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# Known dirs and their one-line purposes (checked for existence before inclusion).
# Order matters: entries appear in this order in the output.
_MODULE_ENTRIES: list[tuple[str, str]] = [
    ("apps", "FastAPI web dashboard application and router modules"),
    ("apps/dashboard/routers", "Extracted FastAPI route modules (no new routes in server.py)"),
    ("apps/dashboard/static/src", "ES module source bundled via esbuild → static/dist/bundle.js"),
    ("services", "Sprint lifecycle management and agent orchestration"),
    ("services/sprint_manager", "Sprint orchestration, dispatch loop, and post-sprint pipeline"),
    ("scripts", "CLI helpers for ticket, branch, and sprint lifecycle operations"),
    ("tests", "Pytest test suite (unit and integration)"),
    ("hooks", "Event webhook handlers that POST to the dashboard"),
    ("docs", "Project documentation"),
    ("alembic", "Database migration scripts"),
]


def _run(*cmd: str, cwd: Path, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), capture_output=True, text=True, cwd=str(cwd), check=check)


def _module_map_section(repo_root: Path) -> str:
    lines = ["## Module Map", ""]
    for rel_path, purpose in _MODULE_ENTRIES:
        if (repo_root / rel_path).is_dir():
            lines.append(f"- **`{rel_path}/`** — {purpose}")
    if len(lines) == 2:
        lines.append("_(no known module directories detected)_")
    lines.append("")
    return "\n".join(lines)


def _recent_deltas_section(repo_root: Path, base_sha: str, head_sha: str, sprint_label: str) -> str:
    lines = [f"## Recent Deltas ({sprint_label})", ""]

    if base_sha == head_sha:
        lines.append("_(no diff available — base and head are the same commit)_")
        lines.append("")
        return "\n".join(lines)

    r = _run("git", "diff", "--name-only", f"{base_sha}..{head_sha}", cwd=repo_root)
    if r.returncode != 0 or not r.stdout.strip():
        lines.append("_(no files changed or diff not available)_")
        lines.append("")
        return "\n".join(lines)

    changed_files = [f.strip() for f in r.stdout.splitlines() if f.strip()]
    total = len(changed_files)
    lines.append(f"Files changed: **{total}**")
    lines.append("")

    # Group by top-level directory
    by_dir: Counter = Counter()
    for f in changed_files:
        top = f.split("/")[0] if "/" in f else "(root)"
        by_dir[top] += 1

    for dirname, count in by_dir.most_common():
        lines.append(f"- `{dirname}/` — {count} file(s)")

    lines.append("")
    return "\n".join(lines)


def _hot_files_section(repo_root: Path) -> str:
    lines = ["## Hot Files (last 90 days)", ""]

    r = _run(
        "git", "log",
        "--since=90 days ago",
        "--format=",
        "--name-only",
        cwd=repo_root,
    )
    if r.returncode != 0 or not r.stdout.strip():
        lines.append("_(no git history available)_")
        lines.append("")
        return "\n".join(lines)

    file_counts: Counter = Counter()
    for line in r.stdout.splitlines():
        line = line.strip()
        if line:
            file_counts[line] += 1

    if not file_counts:
        lines.append("_(no file changes found in recent history)_")
        lines.append("")
        return "\n".join(lines)

    top_files = file_counts.most_common(20)
    lines.append("| File | Commits |")
    lines.append("|------|---------|")
    for fname, count in top_files:
        lines.append(f"| `{fname}` | {count} |")

    lines.append("")
    return "\n".join(lines)


def _generated_section(sprint_label: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return "\n".join([
        "## Generated",
        "",
        f"Sprint: `{sprint_label}`  ",
        f"Timestamp: `{ts}`  ",
        "_Generated deterministically — no LLM required._",
        "",
    ])


def generate(
    repo_root: Path,
    sprint_label: str,
    base_sha: str,
    head_sha: str,
    output_path: Path,
) -> None:
    """Generate the snapshot and write it to output_path."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    sections = [
        f"# Code State — {sprint_label}",
        "",
        "_Deterministic snapshot generated at sprint finish. "
        "Do not hand-edit — regenerated each sprint._",
        "",
        _module_map_section(repo_root),
        _recent_deltas_section(repo_root, base_sha, head_sha, sprint_label),
        _hot_files_section(repo_root),
        _generated_section(sprint_label),
    ]

    output_path.write_text("\n".join(sections), encoding="utf-8")
    sys.stdout.write(f"  [code_state] Wrote {output_path}\n")


def main() -> None:
    p = argparse.ArgumentParser(
        description="Generate docs/architecture/code-state.md for a sprint."
    )
    p.add_argument("--repo-root", default=".", help="Path to the git repo root")
    p.add_argument("--sprint-label", required=True, help="Sprint label, e.g. sprint-116")
    p.add_argument("--base-sha", default="HEAD", help="Merge-base SHA for recent deltas")
    p.add_argument("--head-sha", default="HEAD", help="HEAD SHA for recent deltas")
    p.add_argument(
        "--output",
        default=None,
        help="Output path (default: <repo-root>/docs/architecture/code-state.md)",
    )
    args = p.parse_args()

    repo_root = Path(args.repo_root).resolve()
    output_path = Path(args.output) if args.output else repo_root / "docs" / "architecture" / "code-state.md"

    generate(
        repo_root=repo_root,
        sprint_label=args.sprint_label,
        base_sha=args.base_sha,
        head_sha=args.head_sha,
        output_path=output_path,
    )


if __name__ == "__main__":
    main()
