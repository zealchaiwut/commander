#!/usr/bin/env python3
"""Generate a living STATUS.md snapshot at the project root.

Fetches open issues and recent merged PRs from GitHub, then writes
STATUS.md to the directory that contains .commander/.

Usage (standalone):
    python3 scripts/generate_status.py [--repo owner/repo] [--out PATH]

Usage (from sprint_manager after sprint close):
    python3 scripts/generate_status.py --repo owner/repo --sprint-label sprint-45
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import yaml
    _YAML_OK = True
except ImportError:
    _YAML_OK = False


# ── config discovery ──────────────────────────────────────────────────────────

def _discover_commander_dir() -> Path | None:
    """Walk up from cwd, returning the first .commander/ that contains sprint.yaml.
    Falls back to the first .commander/ found if none has sprint.yaml."""
    cwd = Path.cwd()
    first_found: Path | None = None
    for parent in [cwd, *cwd.parents]:
        candidate = parent / ".commander"
        if candidate.is_dir():
            if (candidate / "sprint.yaml").exists():
                return candidate
            if first_found is None:
                first_found = candidate
    return first_found


def _load_yaml_config(commander_dir: Path) -> dict:
    if not _YAML_OK:
        return {}
    yaml_path = commander_dir / "sprint.yaml"
    if not yaml_path.exists():
        return {}
    with open(yaml_path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


# ── GitHub helpers ────────────────────────────────────────────────────────────

def _gh(args: list[str]) -> str:
    r = subprocess.run(["gh"] + args, capture_output=True, text=True, check=True)
    return r.stdout.strip()


def _fetch_open_issues(repo: str) -> list[dict]:
    out = _gh([
        "issue", "list", "--repo", repo, "--state", "open",
        "--json", "number,title,labels,assignees", "--limit", "200",
    ])
    return json.loads(out) if out else []


def _fetch_closed_sprint_issues(repo: str, sprint_label: str) -> list[dict]:
    """Return closed issues carrying the given sprint label."""
    out = _gh([
        "issue", "list", "--repo", repo, "--state", "closed",
        "--label", sprint_label,
        "--json", "number,title,labels", "--limit", "200",
    ])
    return json.loads(out) if out else []


def _fetch_merged_prs(repo: str, since_days: int = 7) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    out = _gh([
        "pr", "list", "--repo", repo, "--state", "merged",
        "--json", "number,title,mergedAt", "--limit", "100",
    ])
    if not out:
        return []
    prs = json.loads(out)
    return [
        pr for pr in prs
        if pr.get("mergedAt") and datetime.fromisoformat(
            pr["mergedAt"].replace("Z", "+00:00")
        ) >= cutoff
    ]


# ── sprint state helpers ──────────────────────────────────────────────────────

def _find_active_sprint_label(sprints_dir: Path) -> str | None:
    """Return label of the most recently started running sprint, or None."""
    running: list[tuple[str, str]] = []
    for plan_file in sprints_dir.glob("*-plan.json"):
        try:
            data = json.loads(plan_file.read_text(encoding="utf-8"))
            if data.get("state") == "running":
                label = plan_file.stem[: -len("-plan")]
                started_at = data.get("started_at", "")
                running.append((started_at, label))
        except Exception:
            continue
    if running:
        running.sort(key=lambda x: x[0], reverse=True)
        return running[0][1]
    return None


def _read_sprint_goal(sprints_dir: Path, sprint_label: str) -> str:
    goal_file = sprints_dir / f"{sprint_label}-goal.txt"
    if goal_file.exists():
        return goal_file.read_text(encoding="utf-8").strip()
    return ""


# ── formatting ────────────────────────────────────────────────────────────────

def _label_names(issue: dict) -> list[str]:
    return [lbl["name"] for lbl in issue.get("labels", [])]


def _assignee_str(issue: dict) -> str:
    assignees = issue.get("assignees", [])
    return assignees[0].get("login", "") if assignees else ""


def _sprint_num(label: str) -> int:
    parts = label.split("-")
    try:
        return int(parts[-1])
    except (ValueError, IndexError):
        return 0


def _issue_line(issue: dict) -> str:
    assignee = _assignee_str(issue)
    suffix = f" (@{assignee})" if assignee else ""
    return f"- #{issue['number']} {issue['title']}{suffix}"


def _build_progress_lines(
    sprint_open: list[dict], sprint_closed: list[dict]
) -> list[str]:
    """Return markdown lines for the Progress section of a sprint."""
    total = len(sprint_open) + len(sprint_closed)
    in_prog = sum(1 for i in sprint_open if "in-progress" in _label_names(i))
    sit = sum(1 for i in sprint_open if "SIT" in _label_names(i))
    uat = sum(1 for i in sprint_open if "UAT" in _label_names(i))
    done = len(sprint_closed)
    to_do = total - in_prog - sit - uat - done

    def _fmt(n: int) -> str:
        pct = round(100 * n / total) if total else 0
        return f"{n}/{total} ({pct}%)"

    lines: list[str] = ["## Progress", ""]
    lines.append(f"- To Do: {_fmt(to_do)}")
    lines.append(f"- In Progress: {_fmt(in_prog)}")
    lines.append(f"- SIT: {_fmt(sit)}")
    lines.append(f"- UAT: {_fmt(uat)}")
    lines.append(f"- Done: {_fmt(done)}")
    lines.append("")
    return lines


# ── core generator ────────────────────────────────────────────────────────────

def generate(repo: str, sprints_dir: Path, out_path: Path) -> None:
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y-%m-%d %H:%M UTC")

    sprint_label = _find_active_sprint_label(sprints_dir)
    sprint_goal = _read_sprint_goal(sprints_dir, sprint_label) if sprint_label else ""

    issues = _fetch_open_issues(repo)
    sit_issues = [i for i in issues if "SIT" in _label_names(i)]
    uat_issues = [i for i in issues if "UAT" in _label_names(i)]

    # Sprint-scoped issues for Progress section
    sprint_open: list[dict] = []
    sprint_closed: list[dict] = []
    if sprint_label:
        sprint_open = [i for i in issues if sprint_label in _label_names(i)]
        sprint_closed = _fetch_closed_sprint_issues(repo, sprint_label)

    # Open issues grouped by sprint label, excluding SIT/UAT
    open_by_sprint: dict[str, list[dict]] = {}
    for issue in issues:
        labels = _label_names(issue)
        if "SIT" in labels or "UAT" in labels:
            continue
        sprint_labels = sorted(
            [lbl for lbl in labels if lbl.startswith("sprint-")],
            key=_sprint_num,
            reverse=True,
        )
        bucket = sprint_labels[0] if sprint_labels else "unassigned"
        open_by_sprint.setdefault(bucket, []).append(issue)

    recent_prs = _fetch_merged_prs(repo)

    lines: list[str] = []
    lines.append(f"_Last regenerated: {timestamp}_")
    lines.append("")

    lines.append("## Current Sprint Goal")
    if sprint_label:
        lines.append(f"**{sprint_label}**")
        lines.append("")
        lines.append(sprint_goal if sprint_goal else "_No goal recorded._")
    else:
        lines.append("No active sprint.")
    lines.append("")

    if sprint_label:
        lines.extend(_build_progress_lines(sprint_open, sprint_closed))
    else:
        lines.append("## Progress")
        lines.append("")
        lines.append("No active sprint.")
        lines.append("")

    lines.append("## SIT")
    if sit_issues:
        for issue in sit_issues:
            lines.append(_issue_line(issue))
    else:
        lines.append("_No issues in SIT._")
    lines.append("")

    lines.append("## UAT")
    if uat_issues:
        for issue in uat_issues:
            lines.append(_issue_line(issue))
    else:
        lines.append("_No issues in UAT._")
    lines.append("")

    lines.append("## Open Issues")
    if open_by_sprint:
        for bucket in sorted(open_by_sprint.keys(), key=_sprint_num, reverse=True):
            lines.append(f"### {bucket}")
            for issue in open_by_sprint[bucket]:
                lines.append(_issue_line(issue))
            lines.append("")
    else:
        lines.append("_No open issues._")
        lines.append("")

    lines.append("## Recent Merges")
    if recent_prs:
        sorted_prs = sorted(recent_prs, key=lambda p: p.get("mergedAt", ""), reverse=True)
        for pr in sorted_prs[:15]:
            merged = pr.get("mergedAt", "")[:10]
            lines.append(f"- #{pr['number']} {pr['title']} ({merged})")
        if len(sorted_prs) > 15:
            lines.append(f"- _…and {len(sorted_prs) - 15} more_")
    else:
        lines.append("_No PRs merged in the last 7 days._")
    lines.append("")

    content = "\n".join(lines)
    # Atomic write: write to a sibling temp file, then rename
    tmp_fd, tmp_name = tempfile.mkstemp(dir=out_path.parent, suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_name, out_path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    sys.stdout.write(str(f"STATUS.md written → {out_path}") + "\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Generate STATUS.md project snapshot")
    ap.add_argument("--repo", default=None, help="owner/repo (auto-detected from sprint.yaml)")
    ap.add_argument("--out", default=None, help="Output path (default: <project-root>/STATUS.md)")
    args = ap.parse_args()

    commander_dir = _discover_commander_dir()
    cfg: dict = _load_yaml_config(commander_dir) if commander_dir else {}

    repo = args.repo or cfg.get("repo_name") or ""
    if not repo:
        sys.exit("Error: --repo required or set repo_name in .commander/sprint.yaml")

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

    generate(repo=repo, sprints_dir=sprints_dir, out_path=out_path)


if __name__ == "__main__":
    main()
