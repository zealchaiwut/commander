"""Sprint brief generator (issue #860).

After a sprint finishes, this module creates docs/sprint-briefs/sprint-<label>.md
in the target project's working tree. The brief contains four human-readable
sections derived from the sprint state:

  - Shipped — one plain-language line per ticket that merged.
  - Now Visible / Changed — what's observable in the product after this sprint.
  - Awaiting UAT — each pending item with a one-line exercise instruction.
  - Did Not Ship — planned-but-incomplete items with a stated reason.

The file is written to disk and committed using the existing documentor commit
flow (via _commit_brief).  After committing, the sprint summary GitHub issue
body is updated to include a link to the brief.

Usage (called from sprint_manager.py after write_sprint_summary):

    from services.sprint_manager.brief_generator import write_sprint_brief

    write_sprint_brief(
        state           = state,
        repo_name       = eff_repo,
        git_root        = Path(cfg.worktree_tester),
        summary_issue_num = summary_issue_num,
    )
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Section generators
# ---------------------------------------------------------------------------

def _shipped_section(done_issues: list) -> str:
    """Return the Shipped section markdown."""
    lines = ["## Shipped", ""]
    if not done_issues:
        lines.append("Nothing shipped this sprint.")
    else:
        for issue in done_issues:
            title = issue.title if hasattr(issue, "title") else str(issue.get("title", ""))
            number = issue.number if hasattr(issue, "number") else int(issue.get("number", 0))
            lines.append(f"- #{number} {title}")
    lines.append("")
    return "\n".join(lines)


def _now_visible_section(done_issues: list) -> str:
    """Return the Now Visible / Changed section markdown."""
    lines = ["## Now Visible / Changed", ""]
    if not done_issues:
        lines.append("No user-visible changes this sprint.")
    else:
        for issue in done_issues:
            title = issue.title if hasattr(issue, "title") else str(issue.get("title", ""))
            number = issue.number if hasattr(issue, "number") else int(issue.get("number", 0))
            # Provide a generic placeholder — the documentor agent fills in real detail;
            # this module provides the structural scaffold so the file always exists.
            lines.append(f"- #{number} {title} — change is now live in the dashboard.")
    lines.append("")
    return "\n".join(lines)


def _awaiting_uat_section(uat_issues: list) -> str:
    """Return the Awaiting UAT section markdown."""
    lines = ["## Awaiting UAT", ""]
    if not uat_issues:
        lines.append("No items pending UAT.")
    else:
        for issue in uat_issues:
            title = issue.title if hasattr(issue, "title") else str(issue.get("title", ""))
            number = issue.number if hasattr(issue, "number") else int(issue.get("number", 0))
            lines.append(f"- #{number} {title} — open the dashboard and verify the feature works as described.")
    lines.append("")
    return "\n".join(lines)


def _did_not_ship_section(skipped_issues: list) -> str:
    """Return the Did Not Ship section markdown."""
    lines = ["## Did Not Ship", ""]
    if not skipped_issues:
        lines.append("All planned items shipped.")
    else:
        for issue in skipped_issues:
            title = issue.title if hasattr(issue, "title") else str(issue.get("title", ""))
            number = issue.number if hasattr(issue, "number") else int(issue.get("number", 0))
            reason = ""
            if hasattr(issue, "skip_reason"):
                reason = issue.skip_reason or ""
            elif isinstance(issue, dict):
                reason = issue.get("skip_reason") or ""
            if not reason:
                reason = "rolled to next sprint"
            lines.append(f"- #{number} {title} — {reason}")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Brief content builder
# ---------------------------------------------------------------------------

def build_brief_content(
    sprint_label: str,
    done_issues: list,
    uat_issues: list,
    skipped_issues: list,
) -> str:
    """Return the full brief markdown string."""
    header = f"# Sprint {sprint_label} Brief\n\n"
    shipped     = _shipped_section(done_issues)
    now_visible = _now_visible_section(done_issues)
    awaiting    = _awaiting_uat_section(uat_issues)
    did_not     = _did_not_ship_section(skipped_issues)
    return header + shipped + now_visible + awaiting + did_not


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def _brief_path(sprint_label: str, git_root: Path) -> Path:
    """Return the Path for the brief file, creating parent dir if needed."""
    briefs_dir = git_root / "docs" / "sprint-briefs"
    briefs_dir.mkdir(parents=True, exist_ok=True)
    return briefs_dir / f"sprint-{sprint_label}.md"


def _try_run(*cmd, cwd: Optional[Path] = None) -> tuple[bool, str]:
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return r.returncode == 0, r.stdout.strip()


def _commit_brief(brief_path: Path, sprint_label: str, git_root: Path) -> Optional[str]:
    """Stage and commit the brief file. Returns the new commit SHA or None."""
    rel = str(brief_path.relative_to(git_root))

    # Only commit if the file differs from what's in the index
    ok, _ = _try_run("git", "diff", "--quiet", rel, cwd=git_root)
    has_unstaged = not ok
    ok2, _ = _try_run("git", "diff", "--cached", "--quiet", rel, cwd=git_root)
    has_staged = not ok2
    # Also check if file is untracked
    _, untracked_out = _try_run("git", "ls-files", "--others", "--exclude-standard", rel, cwd=git_root)
    is_untracked = bool(untracked_out.strip())

    if not (has_unstaged or has_staged or is_untracked):
        sys.stdout.write(f"  [brief_generator] brief unchanged — skipping commit\n")
        return None

    _try_run("git", "add", rel, cwd=git_root)
    ok3, _ = _try_run(
        "git", "commit", "-m",
        f"docs: sprint-{sprint_label} brief (issue #860)",
        cwd=git_root,
    )
    if not ok3:
        sys.stdout.write(f"  [brief_generator] WARNING: git commit failed for {rel}\n")
        return None

    _, sha = _try_run("git", "rev-parse", "HEAD", cwd=git_root)
    sys.stdout.write(f"  [brief_generator] committed brief at {sha[:12]}\n")
    return sha or None


# ---------------------------------------------------------------------------
# Summary issue update
# ---------------------------------------------------------------------------

def _brief_github_url(sprint_label: str, repo_name: str) -> str:
    """Return the GitHub blob URL for the brief file."""
    return (
        f"https://github.com/{repo_name}/blob/develop/"
        f"docs/sprint-briefs/sprint-{sprint_label}.md"
    )


def _update_summary_issue_with_brief(
    summary_issue_num: int,
    sprint_label: str,
    repo_name: str,
) -> None:
    """Append a link to the brief in the sprint summary GitHub issue body.

    Idempotent: if the link is already present, does nothing.
    """
    import sys
    import os
    _REPO_ROOT = Path(__file__).parent.parent.parent
    _DASHBOARD = _REPO_ROOT / "apps" / "dashboard"
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    if str(_DASHBOARD) not in sys.path:
        sys.path.insert(0, str(_DASHBOARD))

    try:
        import github_client  # type: ignore[import]
    except ImportError:
        sys.stdout.write("  [brief_generator] WARNING: github_client not importable — skipping summary issue update\n")
        return

    brief_url = _brief_github_url(sprint_label, repo_name)
    link_line = f"\n\n---\n**Sprint Brief:** [{sprint_label} brief]({brief_url})"

    try:
        issue = github_client.get_issue(summary_issue_num, repo_name=repo_name)
        body  = issue.get("body") or ""
    except Exception as e:
        sys.stdout.write(f"  [brief_generator] WARNING: could not fetch summary issue #{summary_issue_num}: {e}\n")
        return

    if brief_url in body:
        sys.stdout.write(f"  [brief_generator] brief link already in summary issue #{summary_issue_num} — skipping\n")
        return

    new_body = body.rstrip() + link_line
    try:
        github_client.update_issue_body(summary_issue_num, new_body, repo_name=repo_name)
        sys.stdout.write(f"  [brief_generator] updated summary issue #{summary_issue_num} with brief link\n")
    except Exception as e:
        sys.stdout.write(f"  [brief_generator] WARNING: failed to update summary issue: {e}\n")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def write_sprint_brief(
    state: object,
    repo_name: Optional[str],
    git_root: Path,
    summary_issue_num: Optional[int] = None,
) -> Optional[Path]:
    """Write docs/sprint-briefs/sprint-<label>.md and commit it.

    Parameters
    ----------
    state:
        SprintState-like object with .sprint_label, .issues attributes.
        Each issue in .issues must have .title, .number, .status,
        and optionally .skip_reason.
    repo_name:
        GitHub owner/repo string (e.g. "zealchaiwut/commander").
    git_root:
        Absolute Path to the working tree where the brief should be written.
    summary_issue_num:
        If provided, the sprint summary GitHub issue is updated with a link
        to the brief file.

    Returns
    -------
    Path of the written brief, or None on failure.
    """
    sprint_label: str = getattr(state, "sprint_label", "unknown")
    issues: list = getattr(state, "issues", [])

    done_issues    = [i for i in issues if getattr(i, "status", "") == "done"]
    uat_issues     = [i for i in issues if getattr(i, "status", "") == "done"]  # UAT = merged issues
    skipped_issues = [i for i in issues if getattr(i, "status", "") == "skipped"]

    content = build_brief_content(
        sprint_label  = sprint_label,
        done_issues   = done_issues,
        uat_issues    = uat_issues,
        skipped_issues = skipped_issues,
    )

    brief_path = _brief_path(sprint_label, git_root)
    try:
        brief_path.write_text(content, encoding="utf-8")
        sys.stdout.write(f"  [brief_generator] wrote {brief_path}\n")
    except OSError as e:
        sys.stdout.write(f"  [brief_generator] ERROR: could not write brief: {e}\n")
        return None

    _commit_brief(brief_path, sprint_label, git_root)

    if summary_issue_num and repo_name:
        _update_summary_issue_with_brief(summary_issue_num, sprint_label, repo_name)

    return brief_path
