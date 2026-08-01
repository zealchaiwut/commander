"""Tests for issue #2056 — /estimate slash command backing file.

AC1: .claude/commands/estimate.md exists and has valid frontmatter with description.
AC2: Command accepts a GitHub issue URL, parses the issue number, and
     documents estimate_issue.py --issue <N> [--save-comment] [--save-label] [--force].
AC3: estimate.md appears in the .claude/commands/ directory listing.
AC4: CLAUDE.md contains no advertised slash commands without a backing file.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
COMMANDS_DIR = REPO_ROOT / ".claude" / "commands"
COMMAND_FILE = COMMANDS_DIR / "estimate.md"
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"


# ── AC1 ──────────────────────────────────────────────────────────────────────

def test_estimate_command_file_exists():
    """AC1: .claude/commands/estimate.md must exist."""
    assert COMMAND_FILE.exists(), (
        f"Missing: {COMMAND_FILE}. "
        "Create .claude/commands/estimate.md wrapping estimate_issue.py "
        "as documented in CLAUDE.md."
    )


def test_estimate_command_has_valid_frontmatter():
    """AC1: command file must have YAML frontmatter with a description field."""
    content = COMMAND_FILE.read_text()
    assert content.startswith("---"), "frontmatter must start with ---"
    parts = content.split("---", 2)
    assert len(parts) >= 3, "frontmatter must be closed by a second ---"
    frontmatter = parts[1]
    assert "description:" in frontmatter, "frontmatter must contain a 'description:' field"


# ── AC2 ──────────────────────────────────────────────────────────────────────

def test_estimate_command_references_script_and_issue_flag():
    """AC2: command file must reference estimate_issue.py and --issue flag."""
    content = COMMAND_FILE.read_text()
    assert "estimate_issue.py" in content, (
        "Command must reference services/sprint_manager/estimate_issue.py"
    )
    assert "--issue" in content, "Command must document --issue flag"


def test_estimate_command_documents_url_parsing():
    """AC2: command file must explain how to extract the issue number from a URL."""
    content = COMMAND_FILE.read_text()
    has_url_guidance = (
        "github.com" in content.lower()
        or "issue-url" in content.lower()
        or "url" in content.lower()
    )
    assert has_url_guidance, (
        "Command must describe how to parse a GitHub issue URL "
        "and extract the issue number"
    )


def test_estimate_command_documents_optional_flags():
    """AC2: command must document all optional flags from the CLI contract."""
    content = COMMAND_FILE.read_text()
    for flag in ("--save-comment", "--save-label", "--force"):
        assert flag in content, f"Command must document optional flag: {flag}"


# ── AC3 ──────────────────────────────────────────────────────────────────────

def test_estimate_command_discovered_in_directory():
    """AC3: estimate.md must appear in the .claude/commands/ listing."""
    available = {p.name for p in COMMANDS_DIR.iterdir() if p.suffix == ".md"}
    assert "estimate.md" in available, (
        f".claude/commands/ listing: {sorted(available)!r} — estimate.md not found"
    )


# ── AC4 ──────────────────────────────────────────────────────────────────────

def _extract_slash_commands_from_claude_md() -> list[str]:
    """Return command names advertised as slash commands in CLAUDE.md.

    Matches backtick-enclosed /name tokens that are Claude Code slash commands
    (not API paths like /api/...).
    """
    content = CLAUDE_MD.read_text()
    names = []
    for m in re.finditer(r'`/([a-z][a-z0-9_-]*)(?:\s[^`]*)?`', content):
        name = m.group(1)
        if name.startswith("api"):
            continue
        names.append(name)
    return list(dict.fromkeys(names))  # deduplicate preserving order


def test_claude_md_all_slash_commands_have_backing_files():
    """AC4: every slash command advertised in CLAUDE.md must have a .claude/commands/<name>.md."""
    advertised = _extract_slash_commands_from_claude_md()
    missing = [
        name for name in advertised
        if not (COMMANDS_DIR / f"{name}.md").exists()
    ]
    assert missing == [], (
        f"CLAUDE.md advertises slash commands with no backing file: {missing!r}. "
        "Either create the command files or remove the claims from CLAUDE.md."
    )
