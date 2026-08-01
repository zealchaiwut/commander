"""Tests for issue #2053: coder/tester agent definitions must not reference stale dashboard/ paths"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
CODER_MD = REPO_ROOT / ".claude" / "agents" / "coder.md"
TESTER_MD = REPO_ROOT / ".claude" / "agents" / "tester.md"
CLAUDE_TREE = REPO_ROOT / ".claude"

# Scripts that should resolve at repo root (not dashboard/scripts/)
EXPECTED_SCRIPTS = [
    "scripts/start_feature.py",
    "scripts/update_ticket.py",
    "scripts/comment_ticket.py",
    "scripts/post_test_report.py",
]


def _stale_lines(path: Path, pattern: re.Pattern) -> list:
    return [(i + 1, line) for i, line in enumerate(path.read_text().splitlines()) if pattern.search(line)]


# AC1: Every dashboard/scripts/... reference must be gone

def test_coder_md_no_dashboard_scripts_path():
    # AC1: coder.md must not reference dashboard/scripts/ — scripts live at repo root
    matches = _stale_lines(CODER_MD, re.compile(r"dashboard/scripts/"))
    assert not matches, f"coder.md still has 'dashboard/scripts/' at lines: {[ln for ln, _ in matches]}"


def test_tester_md_no_dashboard_scripts_path():
    # AC1: tester.md must not reference dashboard/scripts/ — scripts live at repo root
    matches = _stale_lines(TESTER_MD, re.compile(r"dashboard/scripts/"))
    assert not matches, f"tester.md still has 'dashboard/scripts/' at lines: {[ln for ln, _ in matches]}"


# AC2: $MAIN_REPO/dashboard (without apps/) must be gone from tester.md

def test_tester_md_no_bare_main_repo_dashboard():
    # AC2: $MAIN_REPO/dashboard must be $MAIN_REPO/apps/dashboard (dashboard-package cwd)
    # Exception: $UAT_REPO/dashboard is a legitimate fallback for generic template repos
    lines = TESTER_MD.read_text().splitlines()
    bad = [
        (i + 1, line)
        for i, line in enumerate(lines)
        if re.search(r"\$MAIN_REPO/dashboard", line)
    ]
    assert not bad, f"tester.md still has bare $MAIN_REPO/dashboard at lines: {[ln for ln, _ in bad]}"


def test_coder_md_no_show_toplevel_dashboard_scripts():
    # AC2: coder.md must not have show-toplevel)/dashboard/scripts pattern
    matches = _stale_lines(CODER_MD, re.compile(r"show-toplevel\)/dashboard"))
    assert not matches, f"coder.md still has show-toplevel)/dashboard at lines: {[ln for ln, _ in matches]}"


def test_coder_md_step1_warning_mentions_apps_dashboard():
    # AC2: the "do not run from X subdirectory" note in coder.md Step 1 must reference apps/dashboard/
    content = CODER_MD.read_text()
    assert "apps/dashboard" in content, "coder.md Step 1 warning does not mention apps/dashboard"
    # The old bare 'dashboard/' subdirectory reference must be gone from the warning line
    for i, line in enumerate(content.splitlines(), 1):
        if "do not run git commands" in line.lower() or "subdirectory" in line.lower():
            assert "apps/dashboard" in line or "dashboard/" not in line, (
                f"coder.md line {i} still mentions bare dashboard/ in the subdirectory warning: {line!r}"
            )


# AC3: Full .claude/ tree must have no stale dashboard/scripts/ or )/dashboard patterns

def test_no_stale_dashboard_scripts_in_claude_tree():
    # AC3: scan entire .claude/ tree for dashboard/scripts/ (script paths that should be at repo root)
    bad_files = []
    for md_path in CLAUDE_TREE.rglob("*.md"):
        if re.search(r"dashboard/scripts/", md_path.read_text()):
            bad_files.append(str(md_path.relative_to(REPO_ROOT)))
    assert not bad_files, f"Stale 'dashboard/scripts/' found in .claude/ tree: {bad_files}"


def test_no_stale_show_toplevel_dashboard_in_claude_tree():
    # AC3: scan entire .claude/ tree for show-toplevel)/dashboard (wrong script path form)
    bad_files = []
    for md_path in CLAUDE_TREE.rglob("*.md"):
        if re.search(r"show-toplevel\)/dashboard", md_path.read_text()):
            bad_files.append(str(md_path.relative_to(REPO_ROOT)))
    assert not bad_files, f"Stale 'show-toplevel)/dashboard' found in .claude/ tree: {bad_files}"


# AC4: Each corrected script path must resolve on disk

@pytest.mark.parametrize("script_path", EXPECTED_SCRIPTS)
def test_referenced_script_exists(script_path):
    # AC4: every script referenced in the agent definitions must exist at the repo root scripts/
    full_path = REPO_ROOT / script_path
    assert full_path.exists(), f"Referenced script not found: {full_path}"


def test_apps_dashboard_package_exists():
    # AC4: apps/dashboard/ must exist (tester cd target for venv activation + github_client)
    assert (REPO_ROOT / "apps" / "dashboard").is_dir(), "apps/dashboard/ not found"


def test_github_client_in_apps_dashboard():
    # AC4: github_client.py must be in apps/dashboard/ (tester Step 1 import site)
    gc = REPO_ROOT / "apps" / "dashboard" / "github_client.py"
    assert gc.exists(), f"github_client.py not found at {gc}"
