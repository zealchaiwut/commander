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
) -> str:
    return (
        f"ISSUE #{issue_num}: {issue_title}\n\n"
        f"ISSUE BODY:\n{issue_body}\n\n"
        f"FEATURE DIFF:\n{diff or '(no diff available)'}\n\n"
        f"CURRENT README (first 200 lines):\n{readme_head or '(not found)'}\n\n"
        f"CURRENT CLAUDE.MD (first 100 lines):\n{claude_md_head or '(not found)'}\n\n"
        f"MODE: {mode}\n\n"
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
            print(f"  [documentor] claude exited {r.returncode}: {r.stderr.strip()[:500]}",
                  file=sys.stderr)
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


def _apply_change(file_path: Path, change: dict) -> bool:
    """Apply a single change dict to a file. Returns True if the file was modified."""
    change_type = change.get("type", "add_bullet")
    section     = change.get("section", "")
    content     = change.get("content", "").strip()

    if not content:
        return False

    if not file_path.exists():
        print(f"  [documentor] {file_path.name} not found — skipping change")
        return False

    text = file_path.read_text(encoding="utf-8")

    if change_type == "add_section":
        # Append a new section at end of file
        if section and section.strip("#").strip() in text:
            print(f"  [documentor] section '{section}' already exists in {file_path.name} — skipping")
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
        print(f"  [documentor] section '{target}' not found in {file_path.name}; appending to end")
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
            print(f"  [documentor] applied change to {label}: {change.get('content', '')[:80]}")
            modified = True
    return modified


def _commit_doc_changes(issue_num: int, cwd: Path) -> None:
    """Stage and commit README.md and CLAUDE.md changes to the feature branch."""
    files = ["README.md", "CLAUDE.md"]
    staged = []
    for f in files:
        path = cwd / f
        if path.exists():
            ok, _ = _try_run("git", "diff", "--quiet", f, cwd=cwd)
            if not ok:  # has unstaged changes
                staged.append(f)

    if not staged:
        print("  [documentor] no doc changes to commit")
        return

    _run("git", "add", *staged, cwd=cwd)
    _run("git", "commit", "-m", f"docs: update for issue #{issue_num}", cwd=cwd)
    print(f"  [documentor] committed: docs: update for issue #{issue_num}")


def _post_uat_comment(issue_num: int, repo: str, markdown: str) -> None:
    """Post the UAT comment to the GitHub issue."""
    if not markdown.strip():
        print("  [documentor] uat_comment_markdown is empty — skipping comment")
        return

    # Ensure the comment starts with the required prefix
    if not markdown.strip().startswith("## 📋 How to UAT this"):
        markdown = "## 📋 How to UAT this\n\n" + markdown.strip()

    try:
        github_client.add_comment(issue_num, markdown, repo_name=repo)
        print(f"  [documentor] UAT comment posted to issue #{issue_num}")
    except Exception as e:
        print(f"  [documentor] failed to post UAT comment: {e}", file=sys.stderr)


def run_documentor(
    issue_num: int,
    repo: str,
    mode: str = "both",
    skip_readme: bool = False,
    skip_uat_comment: bool = False,
    git_root: Optional[Path] = None,
    base_branch: str = "develop",
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

    print(f"  [documentor] issue #{issue_num}, repo={repo}, mode={mode}, root={git_root}")

    # Fetch issue
    print(f"  [documentor] fetching issue #{issue_num} from {repo} ...")
    issue   = _get_issue(issue_num, repo)
    title   = issue.get("title", "")
    body    = issue.get("body", "") or ""

    # Find feature branch
    feature_branch = _find_feature_branch(issue_num, cwd=git_root)
    if not feature_branch:
        print(f"  [documentor] no feature branch found for issue #{issue_num} — using HEAD diff")
        feature_branch = "HEAD"

    # Get diff
    diff = _get_diff(feature_branch, base_branch, cwd=git_root)

    # Read doc files for context
    readme_head    = _read_head(git_root / "README.md", 200)
    claude_md_head = _read_head(git_root / "CLAUDE.md", 100)

    # Adjust mode based on skip flags
    if skip_readme and skip_uat_comment:
        print("  [documentor] both --skip-readme and --skip-uat-comment set — nothing to do")
        return {"readme_changes": [], "claude_md_changes": [], "uat_comment_markdown": ""}
    if skip_readme:
        mode = "uat"
    if skip_uat_comment:
        mode = "readme"

    # Build and run prompt
    prompt = _build_prompt(issue_num, title, body, diff, readme_head, claude_md_head, mode)
    print(f"  [documentor] invoking agent (model: claude-haiku-4-5) ...")
    raw_output = _invoke_agent(prompt)

    # Parse output
    try:
        result = _parse_json_output(raw_output)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"  [documentor] failed to parse agent JSON: {e}", file=sys.stderr)
        print(f"  [documentor] raw output (first 500 chars):\n{raw_output[:500]}", file=sys.stderr)
        result = {"readme_changes": [], "claude_md_changes": [], "uat_comment_markdown": ""}

    # Cache output
    commander_root = _discover_commander_root(git_root) or git_root
    cache_path = _docs_cache_path(issue_num, commander_root)
    cache_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  [documentor] output cached to {cache_path}")

    # Apply README / CLAUDE.md changes
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
        if readme_modified or claude_modified:
            _commit_doc_changes(issue_num, cwd=git_root)

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
        issue_num      = args.issue,
        repo           = repo,
        mode           = args.mode,
        skip_readme    = args.skip_readme,
        skip_uat_comment = args.skip_uat_comment,
        git_root       = git_root,
        base_branch    = args.base_branch,
    )

    readme_count  = len(result.get("readme_changes", []))
    claude_count  = len(result.get("claude_md_changes", []))
    has_uat       = bool(result.get("uat_comment_markdown", "").strip())
    print(
        f"\n[documentor] done — "
        f"readme_changes={readme_count}, "
        f"claude_md_changes={claude_count}, "
        f"uat_comment={'yes' if has_uat else 'no'}"
    )


if __name__ == "__main__":
    main()
