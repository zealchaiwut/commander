"""Tests for issue #2053: Coder and tester agent definitions reference a `dashboard/` directory that does not exist (runs against UAT)"""
import os
import subprocess
import re
from pathlib import Path


def test_agent_definitions_use_correct_script_paths():
    """AC1: Every `dashboard/scripts/...` reference becomes repo-root `scripts/...`."""
    repo_root = subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=os.getcwd(),
        text=True
    ).strip()

    agent_dir = Path(repo_root) / ".claude" / "agents"
    agent_files = list(agent_dir.glob("*.md"))

    # Should have agent definition files
    assert len(agent_files) > 0, f"No agent markdown files found in {agent_dir}"

    violations = []
    for agent_file in agent_files:
        content = agent_file.read_text()
        # Look for the broken pattern: dashboard/scripts/
        if "dashboard/scripts" in content:
            # Count occurrences
            count = content.count("dashboard/scripts")
            violations.append(f"{agent_file.name}: {count} occurrence(s) of 'dashboard/scripts'")

    assert len(violations) == 0, f"Found 'dashboard/scripts' references (should be 'scripts/'): {violations}"


def test_tester_md_uses_apps_dashboard_for_cd_commands():
    """AC2: Every `$MAIN_REPO/dashboard` reference becomes `$MAIN_REPO/apps/dashboard` where cd is intended."""
    repo_root = subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=os.getcwd(),
        text=True
    ).strip()

    tester_md = Path(repo_root) / ".claude" / "agents" / "tester.md"
    assert tester_md.exists(), f"tester.md not found at {tester_md}"

    content = tester_md.read_text()

    # Find all cd "$MAIN_REPO/dashboard" patterns
    cd_pattern = r'cd\s+"\$MAIN_REPO/dashboard"'
    cd_violations = re.findall(cd_pattern, content)

    assert len(cd_violations) == 0, (
        f"Found cd commands with broken path: {cd_violations}. "
        "Should use 'cd \"$MAIN_REPO/apps/dashboard\"'"
    )


def test_tester_md_references_correct_test_file_location():
    """AC2: References to test file location use `$MAIN_REPO/apps/dashboard/tests/` for Commander layout."""
    repo_root = subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=os.getcwd(),
        text=True
    ).strip()

    tester_md = Path(repo_root) / ".claude" / "agents" / "tester.md"
    content = tester_md.read_text()

    # For Commander, tests are in $MAIN_REPO/tests/ (not apps/dashboard/tests)
    # But the agent definition should correctly describe where tests go
    # Find the "Where tests live" section
    lines = content.split('\n')
    tests_location_found = False
    for i, line in enumerate(lines):
        if "Where tests live" in line or "test files are written into" in line:
            # Check that it mentions the correct path for Commander
            # It should say $MAIN_REPO/tests/ for Commander
            section = '\n'.join(lines[i:min(i+5, len(lines))])
            if "$MAIN_REPO/tests/" in section or "$MAIN_REPO/apps/dashboard/tests/" in section:
                tests_location_found = True

    assert tests_location_found, "tester.md should mention where tests are written ($MAIN_REPO/tests/ for Commander)"


def test_all_script_references_resolve():
    """AC4: Verify each corrected command actually resolves."""
    repo_root = subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=os.getcwd(),
        text=True
    ).strip()

    scripts_to_verify = [
        "start_feature.py",
        "update_ticket.py",
        "comment_ticket.py",
        "post_test_report.py",
    ]

    for script in scripts_to_verify:
        script_path = Path(repo_root) / "scripts" / script
        assert script_path.exists(), f"Script not found at {script_path}"

        # Verify it's actually callable and has --help
        result = subprocess.run(
            ["python3", str(script_path), "--help"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5
        )
        assert result.returncode == 0, f"{script} --help failed: {result.stderr}"


def test_agent_files_in_apps_dashboard_also_correct():
    """AC3: Check all .claude/ directories (repo-root and apps/dashboard) for stale paths."""
    repo_root = subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=os.getcwd(),
        text=True
    ).strip()

    claude_dirs = [
        Path(repo_root) / ".claude" / "agents",
        Path(repo_root) / "apps" / "dashboard" / ".claude" / "agents",
    ]

    violations = []
    for claude_dir in claude_dirs:
        if not claude_dir.exists():
            continue

        for agent_file in claude_dir.glob("*.md"):
            content = agent_file.read_text()
            if "dashboard/scripts" in content:
                violations.append(f"{agent_file}: contains 'dashboard/scripts'")
            # Also check for any other stale layout patterns
            if re.search(r"MAIN_REPO/dashboard\b", content):
                # This is OK in some contexts (generic template docs), but check for cmd contexts
                for line in content.split('\n'):
                    if 'cd "$MAIN_REPO/dashboard"' in line:
                        violations.append(f"{agent_file}: cd uses $MAIN_REPO/dashboard (should be $MAIN_REPO/apps/dashboard)")

    assert len(violations) == 0, f"Found stale path references: {violations}"
