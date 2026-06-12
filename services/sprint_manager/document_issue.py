#!/usr/bin/env python3
"""Documentor — reads a shipped issue's diff and invokes the documentor agent to:
  1. Commit minimal README / CLAUDE.md updates to the feature branch.
  2. Post a plain-language "How to UAT this" comment on the GitHub issue.

Output is cached at <project>/.commander/docs/issue-<N>.json.

Usage:
    python3 document_issue.py --issue <N> [--repo <owner/repo>]
                              [--skip-readme] [--skip-uat-comment]
                              [--mode readme|uat|both]

Safety: this script only modifies files in the repo pointed to by --repo (or
the discovered git root).  It NEVER modifies the commander repo's own files
when run against a different project.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

# ── path setup ────────────────────────────────────────────────────────────────

_THIS_DIR     = Path(__file__).parent
_REPO_ROOT    = _THIS_DIR.parent.parent
_DASHBOARD    = _REPO_ROOT / "apps" / "dashboard"
_AGENT_FILE   = _DASHBOARD / ".claude" / "agents" / "documentor.md"

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_DASHBOARD))
try:
    from dotenv import load_dotenv
    load_dotenv(_DASHBOARD / ".env")
except ImportError:
    pass
import github_client  # type: ignore[import]

from services.run_id import mint_run_id
from services.logging import log as structured_log


# ── helpers ───────────────────────────────────────────────────────────────────

def _run(*cmd, cwd: Optional[Path] = None, check: bool = True) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, check=check, cwd=cwd)
    return r.stdout.strip()


def _try_run(*cmd, cwd: Optional[Path] = None) -> tuple[bool, str]:
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return r.returncode == 0, r.stdout.strip()


def _find_feature_branch(issue_num: int, cwd: Optional[Path] = None) -> Optional[str]:
    """Return the feature/<N>-* branch name, checking local then remote."""
    ok, out = _try_run("git", "branch", "--list", f"feature/{issue_num}-*", cwd=cwd)
    if ok and out.strip():
        return out.strip().splitlines()[0].strip().lstrip("* ")
    ok, out = _try_run("git", "branch", "-r", "--list", f"origin/feature/{issue_num}-*", cwd=cwd)
    if ok and out.strip():
        return out.strip().splitlines()[0].strip().removeprefix("origin/")
    return None


def _discover_commander_root(start: Path) -> Optional[Path]:
    """Walk up from start looking for .commander/."""
    current = start.resolve()
    while True:
        candidate = current / ".commander"
        if candidate.is_dir():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def _docs_cache_path(issue_num: int, commander_root: Path) -> Path:
    docs_dir = commander_root / ".commander" / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    return docs_dir / f"issue-{issue_num}.json"


def _get_issue(issue_num: int, repo: str) -> dict:
    """Fetch issue body from GitHub via gh CLI."""
    r = subprocess.run(
        ["gh", "issue", "view", str(issue_num), "--repo", repo,
         "--json", "number,title,body,labels"],
        capture_output=True, text=True, check=True,
    )
    return json.loads(r.stdout)


def _get_diff(feature_branch: str, base_branch: str, cwd: Path) -> str:
    """Return git diff between base_branch and feature_branch."""
    ok, diff = _try_run(
        "git", "diff", f"{base_branch}...{feature_branch}", "--", ".", cwd=cwd
    )
    if ok and diff:
        return diff[:8000]  # cap to keep prompt concise
    # Fallback: diff HEAD vs base
    ok, diff = _try_run("git", "diff", base_branch, "HEAD", cwd=cwd)
    return diff[:8000] if ok else ""


def _read_head(path: Path, lines: int) -> str:
    """Read first N lines of a file, or empty string if missing."""
    if not path.exists():
        return ""
    content = path.read_text(encoding="utf-8", errors="replace")
    return "\n".join(content.splitlines()[:lines])


def _build_prompt(
    issue_num: int,
    issue_title: str,
    issue_body: str,
    diff: str,
    readme_head: str,
    claude_md_head: str,
    mode: str,
    guide_contexts: Optional[dict[str, str]] = None,
) -> str:
    guides_section = ""
    if guide_contexts:
        parts = []
        for rel_path, content in guide_contexts.items():
            parts.append(f"GUIDE FILE ({rel_path}):\n{content or '(empty)'}")
        guides_section = "\n\n" + "\n\n".join(parts)
    return (
        f"ISSUE #{issue_num}: {issue_title}\n\n"
        f"ISSUE BODY:\n{issue_body}\n\n"
        f"FEATURE DIFF:\n{diff or '(no diff available)'}\n\n"
        f"CURRENT README (first 200 lines):\n{readme_head or '(not found)'}\n\n"
        f"CURRENT CLAUDE.MD (first 100 lines):\n{claude_md_head or '(not found)'}\n\n"
        f"MODE: {mode}"
        f"{guides_section}\n\n"
        "Output ONLY the JSON object as described in your instructions."
    )


def _invoke_agent(prompt: str) -> str:
    """Run the documentor agent via claude -p and return stdout."""
    cmd = [
        "claude",
        "--model", "claude-haiku-4-5-20251001",
        "--dangerously-skip-permissions",
        "-p",
        prompt,
    ]
    # Remove ANTHROPIC_API_KEY so subscription auth is used
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=180)
        if r.returncode != 0:
            sys.stderr.write(str(f"  [documentor] claude exited {r.returncode}: {r.stderr.strip()[:500]}") + "\n")
        return r.stdout.strip()
    except FileNotFoundError:
        sys.exit("claude CLI not found. Install Claude Code to use the documentor.")
    except subprocess.TimeoutExpired:
        sys.exit("documentor timed out after 180s")


def _parse_json_output(raw: str) -> dict:
    """Extract and parse the JSON object from agent output."""
    # Strip markdown fences if present
    stripped = re.sub(r"^```[a-z]*\n?", "", raw, flags=re.MULTILINE)
    stripped = re.sub(r"\n?```$", "", stripped, flags=re.MULTILINE)
    stripped = stripped.strip()

    # Find the first {...} block
    start = stripped.find("{")
    end   = stripped.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object found in agent output:\n{raw[:500]}")
    return json.loads(stripped[start:end + 1])


# ── guide helpers ─────────────────────────────────────────────────────────────

_KNOWN_GUIDE_NAMES = [
    "docs/quickstart.md",
    "docs/QUICKSTART.md",
    "docs/tutorial.md",
    "docs/TUTORIAL.md",
]

_USER_VISIBLE_NEGATIVE = [
    "no user-visible change",
    "no user-facing change",
    "pure refactor",
    "internal refactor",
]


def _discover_guide_files(git_root: Path, guide_glob: Optional[str] = None) -> list[Path]:
    """Return existing guide files from known paths + optional glob."""
    found: list[Path] = []
    seen: set[Path] = set()
    for rel in _KNOWN_GUIDE_NAMES:
        p = git_root / rel
        if p.exists() and p not in seen:
            found.append(p)
            seen.add(p)
    if guide_glob:
        for p in sorted(git_root.glob(guide_glob)):
            if p.is_file() and p not in seen:
                found.append(p)
                seen.add(p)
    return found


def _is_user_visible(issue: dict) -> bool:
    """Return True if the ticket introduces a user-visible change."""
    combined = f"{issue.get('title', '')} {issue.get('body', '')}".lower()
    return not any(marker in combined for marker in _USER_VISIBLE_NEGATIVE)


def _apply_guide_changes(
    guide_changes: dict[str, list[dict]],
    git_root: Path,
) -> list[Path]:
    """Apply per-file guide changes. Returns list of modified guide Paths."""
    modified: list[Path] = []
    for rel_path, changes in guide_changes.items():
        guide_path = git_root / rel_path
        if not guide_path.exists():
            sys.stdout.write(str(f"  [documentor] WARNING: guide not found, skipping: {rel_path}") + "\n")
            continue
        changed = _apply_doc_changes(changes, guide_path, rel_path)
        if changed:
            modified.append(guide_path)
    return modified


def _append_changelog_entry(
    issue_num: int,
    issue_title: str,
    repo: str,
    git_root: Path,
    ship_date: Optional[str] = None,
) -> bool:
    """Prepend a new entry to CHANGELOG.md (newest-first). Returns True if modified."""
    from datetime import date as _date

    changelog = git_root / "CHANGELOG.md"
    date_str = ship_date or _date.today().isoformat()
    issue_url = f"https://github.com/{repo}/issues/{issue_num}"
    entry_line = f"- [#{issue_num}]({issue_url}) {issue_title} — {date_str}\n"

    if not changelog.exists():
        changelog.write_text(f"# Changelog\n\n{entry_line}", encoding="utf-8")
        sys.stdout.write(str(f"  [documentor] created CHANGELOG.md with first entry for #{issue_num}") + "\n")
        return True

    text = changelog.read_text(encoding="utf-8")

    if f"[#{issue_num}](" in text:
        sys.stdout.write(str(f"  [documentor] CHANGELOG.md already has entry for #{issue_num} — skipping") + "\n")
        return False

    lines = text.splitlines(keepends=True)

    # Find line right after the top-level heading
    insert_at = 0
    for i, line in enumerate(lines):
        if re.match(r"^# ", line):
            insert_at = i + 1
            break

    # Skip blank lines to find first content after heading
    content_start = insert_at
    while content_start < len(lines) and lines[content_start].strip() == "":
        content_start += 1

    # Insert newest entry first
    lines.insert(content_start, entry_line)

    # Ensure a blank line separates from non-entry content that follows
    next_idx = content_start + 1
    if next_idx < len(lines) and lines[next_idx].strip() and not lines[next_idx].startswith("- [#"):
        lines.insert(content_start + 1, "\n")

    changelog.write_text("".join(lines), encoding="utf-8")
    sys.stdout.write(str(f"  [documentor] prepended CHANGELOG.md entry for #{issue_num}") + "\n")
    return True


def _section_body(text: str, section: str) -> Optional[str]:
    """Return the body of `section` (lines between its heading and the next
    heading of any level), or None if the section heading is not present.

    Used to scope the idempotency check to a single section so identical
    content in a *different* section does not block a valid insertion.
    """
    if not section:
        return None
    target = section.strip()
    lines = text.splitlines(keepends=True)
    sec_idx = None
    for i, line in enumerate(lines):
        if line.strip() == target:
            sec_idx = i
            break
    if sec_idx is None:
        return None
    end = len(lines)
    for i in range(sec_idx + 1, len(lines)):
        if re.match(r"^#{1,6} ", lines[i]):
            end = i
            break
    return "".join(lines[sec_idx + 1:end])


def _content_already_present(text: str, section: str, content: str) -> bool:
    """Precise idempotency check for a documentor change.

    Scopes the duplicate check to the target section (when one is given) and
    requires an exact stripped-line match of every content line, rather than a
    naive whole-file substring match. This avoids skipping a valid insertion
    when the same content appears in a different section or only as a substring
    of an unrelated line (issue #706).
    """
    content = content.strip()
    if not content:
        return False
    if section:
        scope = _section_body(text, section)
        if scope is None:
            # Target section does not exist yet → content cannot be in it.
            return False
    else:
        scope = text
    scope_lines = {ln.strip() for ln in scope.splitlines() if ln.strip()}
    content_lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    if not content_lines:
        return False
    return all(cl in scope_lines for cl in content_lines)


def _apply_change(file_path: Path, change: dict) -> bool:
    """Apply a single change dict to a file. Returns True if the file was modified."""
    change_type = change.get("type", "add_bullet")
    section     = change.get("section", "")
    content     = change.get("content", "").strip()

    if not content:
        return False

    if not file_path.exists():
        sys.stdout.write(str(f"  [documentor] {file_path.name} not found — skipping change") + "\n")
        return False

    text = file_path.read_text(encoding="utf-8")

    # Idempotency: skip only if this exact content already lives in the target
    # section. A naive whole-file substring check (`content in text`) wrongly
    # skipped valid insertions when the same text appeared in a different
    # section or merely as a substring of an unrelated line (issue #706).
    if _content_already_present(text, section, content):
        return False

    if change_type == "add_section":
        # Append a new section at end of file
        if section and section.strip("#").strip() in text:
            sys.stdout.write(str(f"  [documentor] section '{section}' already exists in {file_path.name} — skipping") + "\n")
            return False
        new_text = text.rstrip("\n") + f"\n\n{content}\n"
        file_path.write_text(new_text, encoding="utf-8")
        return True

    # add_bullet: find section heading and append after the last bullet in it
    if not section:
        # Append bullet at end of file
        new_text = text.rstrip("\n") + f"\n{content}\n"
        file_path.write_text(new_text, encoding="utf-8")
        return True

    lines  = text.splitlines(keepends=True)
    # Find the section heading
    target = section.strip()
    sec_idx = None
    for i, line in enumerate(lines):
        if line.strip() == target:
            sec_idx = i
            break

    if sec_idx is None:
        # Section not found — append to end
        sys.stdout.write(str(f"  [documentor] section '{target}' not found in {file_path.name}; appending to end") + "\n")
        new_text = text.rstrip("\n") + f"\n\n{target}\n\n{content}\n"
        file_path.write_text(new_text, encoding="utf-8")
        return True

    # Find insertion point: after last non-empty line in this section
    next_sec = len(lines)
    for i in range(sec_idx + 1, len(lines)):
        if re.match(r"^#{1,6} ", lines[i]):
            next_sec = i
            break

    # Insert before the next section (or at end)
    insert_at = next_sec
    # Walk back to skip trailing blank lines before the next section
    while insert_at > sec_idx + 1 and lines[insert_at - 1].strip() == "":
        insert_at -= 1

    lines.insert(insert_at, content + "\n")
    file_path.write_text("".join(lines), encoding="utf-8")
    return True


def _apply_doc_changes(
    changes: list[dict],
    file_path: Path,
    label: str,
) -> bool:
    """Apply all changes to a doc file. Returns True if any change was made."""
    if not changes:
        return False
    modified = False
    for change in changes:
        if _apply_change(file_path, change):
            sys.stdout.write(str(f"  [documentor] applied change to {label}: {change.get('content', '')[:80]}") + "\n")
            modified = True
    return modified


def _commit_doc_changes(issue_num: int, cwd: Path, extra_files: Optional[list[Path]] = None) -> None:
    """Stage and commit doc changes to the feature branch.

    Commits README.md, CLAUDE.md, CHANGELOG.md, and any modified guide files.
    """
    files = ["README.md", "CLAUDE.md", "CHANGELOG.md"]
    # Add relative paths for any extra guide files
    for p in (extra_files or []):
        try:
            rel = str(p.relative_to(cwd))
            files.append(rel)
        except ValueError:
            files.append(str(p))

    staged = []
    for f in files:
        path = cwd / f if not Path(f).is_absolute() else Path(f)
        rel = str(path.relative_to(cwd)) if path.is_absolute() else f
        if path.exists():
            ok, _ = _try_run("git", "diff", "--quiet", rel, cwd=cwd)
            if not ok:  # has unstaged changes
                staged.append(rel)

    if not staged:
        sys.stdout.write(str("  [documentor] no doc changes to commit") + "\n")
        return

    _run("git", "add", *staged, cwd=cwd)
    _run("git", "commit", "-m", f"docs: update for issue #{issue_num}", cwd=cwd)
    sys.stdout.write(str(f"  [documentor] committed: docs: update for issue #{issue_num}") + "\n")


def _post_uat_comment(issue_num: int, repo: str, markdown: str) -> None:
    """Post the UAT comment to the GitHub issue."""
    if not markdown.strip():
        sys.stdout.write(str("  [documentor] uat_comment_markdown is empty — skipping comment") + "\n")
        return

    # Ensure the comment starts with the required prefix
    if not markdown.strip().startswith("## 📋 How to UAT this"):
        markdown = "## 📋 How to UAT this\n\n" + markdown.strip()

    try:
        github_client.add_comment(issue_num, markdown, repo_name=repo)
        sys.stdout.write(str(f"  [documentor] UAT comment posted to issue #{issue_num}") + "\n")
    except Exception as e:
        sys.stderr.write(str(f"  [documentor] failed to post UAT comment: {e}") + "\n")


def run_documentor(
    issue_num: int,
    repo: str,
    mode: str = "both",
    skip_readme: bool = False,
    skip_uat_comment: bool = False,
    skip_changelog: bool = False,
    git_root: Optional[Path] = None,
    base_branch: str = "develop",
    guide_glob: Optional[str] = None,
) -> dict:
    """Main documentor logic. Returns the parsed agent output dict.

    Safety: all file modifications are confined to git_root (the target project).
    If git_root is the commander repo itself and repo points elsewhere, no
    commander files are modified — the diff and file reads target git_root.
    """
    if git_root is None:
        try:
            git_root = Path(_run("git", "rev-parse", "--show-toplevel"))
        except subprocess.CalledProcessError:
            sys.exit("Not in a git repository and --git-root not specified.")

    sys.stdout.write(str(f"  [documentor] issue #{issue_num}, repo={repo}, mode={mode}, root={git_root}") + "\n")

    # Fetch issue
    sys.stdout.write(str(f"  [documentor] fetching issue #{issue_num} from {repo} ...") + "\n")
    issue   = _get_issue(issue_num, repo)
    title   = issue.get("title", "")
    body    = issue.get("body", "") or ""

    # Find feature branch
    feature_branch = _find_feature_branch(issue_num, cwd=git_root)
    if not feature_branch:
        sys.stdout.write(str(f"  [documentor] no feature branch found for issue #{issue_num} — using HEAD diff") + "\n")
        feature_branch = "HEAD"

    # Get diff
    diff = _get_diff(feature_branch, base_branch, cwd=git_root)

    # Read doc files for context
    readme_head    = _read_head(git_root / "README.md", 200)
    claude_md_head = _read_head(git_root / "CLAUDE.md", 100)

    # Discover guide files and read their content for context
    guide_files = _discover_guide_files(git_root, guide_glob)
    guide_contexts: dict[str, str] = {}
    if guide_files:
        sys.stdout.write(str(f"  [documentor] discovered {len(guide_files)} guide file(s): "
              f"{[str(g.relative_to(git_root)) for g in guide_files]}") + "\n")
        for gf in guide_files:
            rel = str(gf.relative_to(git_root))
            guide_contexts[rel] = _read_head(gf, 100)

    # Adjust mode based on skip flags
    if skip_readme and skip_uat_comment:
        sys.stdout.write(str("  [documentor] both --skip-readme and --skip-uat-comment set — nothing to do") + "\n")
        return {"readme_changes": [], "claude_md_changes": [], "uat_comment_markdown": ""}
    if skip_readme:
        mode = "uat"
    if skip_uat_comment:
        mode = "readme"

    # Build and run prompt (include guide contexts when user-visible)
    prompt_guides = guide_contexts if _is_user_visible(issue) else None
    prompt = _build_prompt(
        issue_num, title, body, diff, readme_head, claude_md_head, mode,
        guide_contexts=prompt_guides,
    )
    sys.stdout.write(str(f"  [documentor] invoking agent (model: claude-haiku-4-5) ...") + "\n")
    raw_output = _invoke_agent(prompt)

    # Parse output
    try:
        result = _parse_json_output(raw_output)
    except (ValueError, json.JSONDecodeError) as e:
        sys.stderr.write(str(f"  [documentor] failed to parse agent JSON: {e}") + "\n")
        sys.stderr.write(str(f"  [documentor] raw output (first 500 chars):\n{raw_output[:500]}") + "\n")
        result = {"readme_changes": [], "claude_md_changes": [], "uat_comment_markdown": ""}

    # Cache output
    commander_root = _discover_commander_root(git_root) or git_root
    cache_path = _docs_cache_path(issue_num, commander_root)
    cache_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    sys.stdout.write(str(f"  [documentor] output cached to {cache_path}") + "\n")

    # Apply README / CLAUDE.md changes
    readme_modified = False
    claude_modified = False
    if mode in ("readme", "both") and not skip_readme:
        readme_modified = _apply_doc_changes(
            result.get("readme_changes", []),
            git_root / "README.md",
            "README.md",
        )
        claude_modified = _apply_doc_changes(
            result.get("claude_md_changes", []),
            git_root / "CLAUDE.md",
            "CLAUDE.md",
        )

    # Append CHANGELOG entry
    changelog_modified = False
    if not skip_changelog:
        changelog_modified = _append_changelog_entry(issue_num, title, repo, git_root)

    # Apply guide changes (quickstart, tutorial, and any glob-matched guides)
    guide_files_modified: list[Path] = []
    if mode in ("readme", "both") and not skip_readme and _is_user_visible(issue):
        guide_files_modified = _apply_guide_changes(
            result.get("guide_changes", {}),
            git_root,
        )

    # Commit all doc changes together
    if readme_modified or claude_modified or changelog_modified or guide_files_modified:
        _commit_doc_changes(issue_num, cwd=git_root, extra_files=guide_files_modified)

    # Post UAT comment
    if mode in ("uat", "both") and not skip_uat_comment:
        _post_uat_comment(issue_num, repo, result.get("uat_comment_markdown", ""))

    return result


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(
        description="Documentor — auto-update README/CLAUDE.md and post UAT instructions."
    )
    p.add_argument("--issue", type=int, required=True, help="GitHub issue number")
    p.add_argument("--repo", default=None,
                   help="owner/repo (default: derived from git remote)")
    p.add_argument("--skip-readme", action="store_true",
                   help="Skip README/CLAUDE.md updates")
    p.add_argument("--skip-uat-comment", action="store_true",
                   help="Skip posting UAT comment to issue")
    p.add_argument("--skip-changelog", action="store_true",
                   help="Skip appending entry to CHANGELOG.md")
    p.add_argument("--guide-glob", default=None,
                   help="Glob pattern for additional guide files (e.g. 'docs/guides/*.md')")
    p.add_argument("--mode", default="both", choices=["readme", "uat", "both"],
                   help="What to produce (default: both)")
    p.add_argument("--base-branch", default="develop",
                   help="Base branch to diff against (default: develop)")
    args = p.parse_args()

    # Mint or adopt run_id for this invocation
    _run_id = mint_run_id("manual")
    os.environ["COMMANDER_RUN_ID"] = _run_id
    structured_log.set_context(run_id=_run_id, source="manual")

    # Resolve repo
    if args.repo:
        repo = args.repo
    else:
        try:
            repo = github_client.repo()
        except Exception as e:
            sys.exit(f"Could not determine repo: {e}. Pass --repo owner/repo.")

    # Git root
    try:
        git_root = Path(_run("git", "rev-parse", "--show-toplevel"))
    except subprocess.CalledProcessError:
        sys.exit("Must be run from inside a git repository.")

    result = run_documentor(
        issue_num        = args.issue,
        repo             = repo,
        mode             = args.mode,
        skip_readme      = args.skip_readme,
        skip_uat_comment = args.skip_uat_comment,
        skip_changelog   = args.skip_changelog,
        git_root         = git_root,
        base_branch      = args.base_branch,
        guide_glob       = args.guide_glob,
    )

    readme_count  = len(result.get("readme_changes", []))
    claude_count  = len(result.get("claude_md_changes", []))
    has_uat       = bool(result.get("uat_comment_markdown", "").strip())
    sys.stdout.write(str(f"\n[documentor] done — "
        f"readme_changes={readme_count}, "
        f"claude_md_changes={claude_count}, "
        f"uat_comment={'yes' if has_uat else 'no'}") + "\n")


if __name__ == "__main__":
    main()
