"""Tests for issue #2056: estimate slash command (documentation/command file task)"""
import os
import re
import sys
from pathlib import Path


# Utility to find repo root
def find_repo_root():
    current = Path(__file__).parent.parent
    while (current / ".git").exists():
        return current
    # Fallback to a known structure
    return Path(__file__).parent.parent


REPO_ROOT = find_repo_root()


def test_estimate_command_file_exists__2056():
    """AC: Create `.claude/commands/estimate.md` wrapping estimate_issue.py"""
    estimate_cmd = REPO_ROOT / ".claude" / "commands" / "estimate.md"
    assert estimate_cmd.exists(), f"estimate.md not found at {estimate_cmd}"
    content = estimate_cmd.read_text()
    assert len(content) > 0, "estimate.md is empty"


def test_estimate_command_has_valid_frontmatter__2056():
    """AC: Command file follows the structure of existing commands"""
    estimate_cmd = REPO_ROOT / ".claude" / "commands" / "estimate.md"
    content = estimate_cmd.read_text()

    # Check for YAML frontmatter
    assert content.startswith("---"), "Command file must start with --- (YAML frontmatter)"
    lines = content.split("\n")

    # Find closing ---
    closing_line = None
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            closing_line = i
            break

    assert closing_line is not None, "No closing --- found in frontmatter"
    assert "description:" in content, "Missing 'description:' field in frontmatter"


def test_estimate_command_documents_issue_url_parsing__2056():
    """AC: Command accepts issue URL and parses number correctly"""
    estimate_cmd = REPO_ROOT / ".claude" / "commands" / "estimate.md"
    content = estimate_cmd.read_text()

    # Check for documentation of URL/reference parsing
    assert "ISSUE_REF" in content or "ISSUE_URL" in content, "Missing ISSUE_REF/ISSUE_URL parameter documentation"
    assert "github.com" in content or "issues/" in content, "Missing URL pattern documentation"
    assert "extract" in content.lower(), "Missing explanation of issue number extraction"


def test_estimate_command_wraps_estimate_issue_py__2056():
    """AC: Command calls estimate_issue.py with proper arguments"""
    estimate_cmd = REPO_ROOT / ".claude" / "commands" / "estimate.md"
    content = estimate_cmd.read_text()

    # Check for reference to the Python script
    assert "estimate_issue.py" in content, "Missing reference to estimate_issue.py"
    assert "--issue" in content, "Missing --issue flag documentation"
    assert "--repo" in content, "Missing --repo flag documentation"
    assert "--save-comment" in content, "Missing --save-comment flag documentation"
    assert "--save-label" in content, "Missing --save-label flag documentation"
    assert "--force" in content, "Missing --force flag documentation"


def test_estimate_command_discoverable__2056():
    """AC: Verify command file is discovered — it appears in commands directory"""
    commands_dir = REPO_ROOT / ".claude" / "commands"
    assert commands_dir.exists(), f"Commands directory not found at {commands_dir}"

    # List all command files
    command_files = sorted([f.name for f in commands_dir.glob("*.md")])
    assert "estimate.md" in command_files, f"estimate.md not in commands directory. Found: {command_files}"

    # Ensure it's discoverable alongside other commands
    assert len(command_files) > 1, "Commands directory should have multiple command files"
    # Known commands should include estimate, ba, est, decide, etc.
    assert any("ba" in f for f in command_files), "ba.md should be present as reference"


def test_claude_md_advertises_slash_command__2056():
    """AC: CLAUDE.md mentions `/estimate <issue-url>` — ensure claim is not removed"""
    claude_md = REPO_ROOT / "CLAUDE.md"
    assert claude_md.exists(), "CLAUDE.md not found"

    content = claude_md.read_text()
    # The claim should still be there (we created the command, didn't remove the claim)
    assert "/estimate" in content, "Claim about /estimate command was removed from CLAUDE.md"
    assert "slash command" in content, "Missing slash command documentation"


def test_no_other_advertised_commands_missing__2056():
    """AC: Sweep CLAUDE.md for other advertised commands with no backing file"""
    commands_dir = REPO_ROOT / ".claude" / "commands"
    available_commands = {f.stem for f in commands_dir.glob("*.md")}

    claude_md = REPO_ROOT / "CLAUDE.md"
    content = claude_md.read_text()

    # Search for patterns that look like advertised commands
    # Look for "As a slash command:" or "Usage:" patterns that reference /command
    slash_patterns = re.findall(r"`/([a-z-]+)`", content)

    missing = []
    for cmd in slash_patterns:
        # Filter out false positives (paths like /api/*, /dev/*, /etc/*, etc.)
        if cmd in ["api", "dev", "etc", "tmp", "bin", "usr", "var", "home", "root", "opt", "srv"]:
            continue
        # API endpoints and paths are not slash commands
        if any(x in cmd for x in ["dashboard", "sprint", "project", "event", "agent", "repo"]):
            continue
        # Check if we have a command file for it
        if cmd not in available_commands:
            missing.append(cmd)

    assert len(missing) == 0, f"Advertised commands with no backing file: {missing}"
