#!/usr/bin/env python3
"""Generate a context digest markdown file for starting a new Claude Code session.

Reads CLAUDE.md, session memory files, and GitHub in-progress issues to build a
self-contained snapshot that can be pasted at the start of a new session.

Usage:
    python3 scripts/generate_context_digest.py [--repo owner/repo] [--out PATH] [--decisions N]

Output is written to .claude/context-digest.md by default; the path is printed to stdout.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


# ── git helpers ───────────────────────────────────────────────────────────────

def _git(args: list[str]) -> str:
    try:
        r = subprocess.run(["git"] + args, capture_output=True, text=True, check=True)
        return r.stdout.strip()
    except subprocess.CalledProcessError:
        return ""


def _active_branch() -> str:
    return _git(["rev-parse", "--abbrev-ref", "HEAD"]) or "unknown"


# ── discovery ─────────────────────────────────────────────────────────────────

def _find_claude_md(start: Path) -> Path | None:
    for parent in [start, *start.parents]:
        candidate = parent / "CLAUDE.md"
        if candidate.exists():
            return candidate
    return None


def _memory_dir(cwd: Path) -> Path:
    encoded = str(cwd).replace("/", "-")
    return Path.home() / ".claude" / "projects" / encoded / "memory"


def _discover_repo(cwd: Path) -> str | None:
    for parent in [cwd, *cwd.parents]:
        sprint_yaml = parent / ".commander" / "sprint.yaml"
        if sprint_yaml.exists():
            try:
                import yaml
                cfg = yaml.safe_load(sprint_yaml.read_text(encoding="utf-8")) or {}
                repo = cfg.get("repo_name")
                if repo:
                    return repo
            except Exception:
                pass
    try:
        r = subprocess.run(
            ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
            capture_output=True, text=True, check=True, timeout=5,
        )
        return r.stdout.strip() or None
    except Exception:
        return None


# ── CLAUDE.md parsing ─────────────────────────────────────────────────────────

_GOAL_HEADINGS = {"project overview", "overview", "about", "what is this", "purpose"}


def _extract_goals(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    goals: list[str] = []
    in_target = False

    for line in lines:
        if line.startswith("## "):
            heading = line[3:].strip().lower()
            in_target = heading in _GOAL_HEADINGS
            continue
        if line.startswith("#"):
            if in_target:
                break
            continue
        if in_target and line.strip().startswith("-"):
            clean = line.strip().lstrip("-").strip()
            if clean:
                goals.append(clean[:120])
            if len(goals) >= 6:
                break

    if not goals:
        seen = False
        for line in lines:
            if line.startswith("## "):
                seen = True
                continue
            if seen and line.strip().startswith("-") and not line.startswith("#"):
                clean = line.strip().lstrip("-").strip()
                if clean:
                    goals.append(clean[:120])
                if len(goals) >= 6:
                    break

    return goals[:6]


# ── memory files ──────────────────────────────────────────────────────────────

def _parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("---", 3)
    if end == -1:
        return {}, text
    fm_text = text[3:end].strip()
    body = text[end + 3:].strip()
    metadata: dict = {}
    for line in fm_text.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            k = key.strip()
            v = val.strip()
            if k and k not in ("metadata",):
                metadata[k] = v
    return metadata, body


def _load_memory_files(memory_dir: Path) -> list[dict]:
    if not memory_dir.exists():
        return []
    entries: list[dict] = []
    for f in sorted(memory_dir.glob("*.md")):
        if f.name.upper() == "MEMORY.MD":
            continue
        try:
            text = f.read_text(encoding="utf-8")
            fm, body = _parse_frontmatter(text)
            entries.append({
                "name": fm.get("name", f.stem),
                "description": fm.get("description", ""),
                "type": fm.get("type", ""),
                "body": body,
            })
        except Exception:
            continue
    return entries


# ── GitHub ────────────────────────────────────────────────────────────────────

def _fetch_inprogress(repo: str) -> list[dict]:
    try:
        r = subprocess.run(
            [
                "gh", "issue", "list", "--repo", repo,
                "--label", "in-progress", "--state", "open",
                "--json", "number,title", "--limit", "50",
            ],
            capture_output=True, text=True, check=True, timeout=8,
        )
        return json.loads(r.stdout) if r.stdout.strip() else []
    except Exception:
        return []


# ── core generator ────────────────────────────────────────────────────────────

def generate(cwd: Path, repo: str | None, out_path: Path, max_decisions: int = 10) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    branch = _active_branch()
    claude_md = _find_claude_md(cwd)
    memory_entries = _load_memory_files(_memory_dir(cwd))
    inprogress = _fetch_inprogress(repo) if repo else []

    lines: list[str] = []

    lines.append("> **How to Use:** Paste this digest at the start of a new Claude Code session to restore project context immediately.")
    lines.append("")
    lines.append(f"_Generated: {timestamp}_")
    lines.append("")

    # Project State
    lines.append("## Project State")
    lines.append("")
    lines.append(f"- **Working directory:** `{cwd}`")
    lines.append(f"- **Active branch:** `{branch}`")
    if claude_md:
        goals = _extract_goals(claude_md)
        if goals:
            lines.append("- **Goals:**")
            for g in goals:
                lines.append(f"  - {g}")
        else:
            lines.append("- **Goals:** _(see CLAUDE.md)_")
    else:
        lines.append("- **Goals:** _No CLAUDE.md found._")
    lines.append("")

    # Active Work
    lines.append("## Active Work")
    lines.append("")
    if inprogress:
        for issue in inprogress:
            lines.append(f"- #{issue['number']} {issue['title']}")
    elif not repo:
        lines.append("_No repository detected; skipped in-progress issue lookup._")
    else:
        lines.append("_No in-progress tasks detected._")
    lines.append("")

    # Recent Decisions
    lines.append("## Recent Decisions")
    lines.append("")
    decision_types = {"feedback", "project"}
    decisions = [e for e in memory_entries if e["type"] in decision_types]
    if not decisions:
        decisions = memory_entries
    decisions = decisions[:max_decisions]

    if decisions:
        for entry in decisions:
            lines.append(f"### {entry['name']}")
            if entry["description"]:
                lines.append(f"_{entry['description']}_")
                lines.append("")
            if entry["body"]:
                body_lines = [l for l in entry["body"].splitlines() if l.strip()][:6]
                for bl in body_lines:
                    lines.append(bl)
            lines.append("")
    else:
        lines.append("_No decisions recorded in session memory._")
        lines.append("")

    content = "\n".join(lines)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    sys.stdout.write(str(str(out_path)) + "\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Generate context digest for new Claude Code sessions")
    ap.add_argument("--repo", default=None, help="owner/repo (auto-detected from sprint.yaml or gh)")
    ap.add_argument("--out", default=None, help="Output path (default: <cwd>/.claude/context-digest.md)")
    ap.add_argument("--decisions", type=int, default=10, help="Max memory entries to include (default: 10)")
    args = ap.parse_args()

    cwd = Path.cwd().resolve()
    repo = args.repo or _discover_repo(cwd)
    out_path = Path(args.out).resolve() if args.out else cwd / ".claude" / "context-digest.md"

    generate(cwd=cwd, repo=repo, out_path=out_path, max_decisions=args.decisions)


if __name__ == "__main__":
    main()
