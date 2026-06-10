"""Tests for issue #791: Add per-area AGENTS.md context files (hierarchical)

Each test is anchored to a specific acceptance criterion from the issue.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent.parent
CODER_AGENT = REPO_ROOT / ".claude" / "agents" / "coder.md"
DOCUMENTER_AGENT = REPO_ROOT / ".claude" / "agents" / "documenter.md"

AGENTS_MD_DIRS = [
    REPO_ROOT / "apps" / "dashboard",
    REPO_ROOT / "apps" / "dashboard" / "routers",
    REPO_ROOT / "services" / "sprint_manager",
    REPO_ROOT / "scripts",
    REPO_ROOT / "apps" / "dashboard" / "static",
]

REQUIRED_SECTIONS = [
    "## Purpose",
    "## Key Files",
    "## Conventions",
    "## Danger Zones",
    "## What NOT to Touch",
]


# ── AC1: AGENTS.md files exist and contain required sections ─────────────────

def _read_agents_md(directory: Path) -> str:
    agents_file = directory / "AGENTS.md"
    assert agents_file.exists(), f"AGENTS.md missing in {directory.relative_to(REPO_ROOT)}"
    return agents_file.read_text()


def test_agents_md_exists_in_apps_dashboard():
    # AC1: AGENTS.md exists in apps/dashboard
    assert (REPO_ROOT / "apps" / "dashboard" / "AGENTS.md").exists()


def test_agents_md_exists_in_apps_dashboard_routers():
    # AC1: AGENTS.md exists in apps/dashboard/routers
    assert (REPO_ROOT / "apps" / "dashboard" / "routers" / "AGENTS.md").exists()


def test_agents_md_exists_in_services_sprint_manager():
    # AC1: AGENTS.md exists in services/sprint_manager
    assert (REPO_ROOT / "services" / "sprint_manager" / "AGENTS.md").exists()


def test_agents_md_exists_in_scripts():
    # AC1: AGENTS.md exists in scripts
    assert (REPO_ROOT / "scripts" / "AGENTS.md").exists()


def test_agents_md_exists_in_static():
    # AC1: AGENTS.md exists in apps/dashboard/static (frontend)
    assert (REPO_ROOT / "apps" / "dashboard" / "static" / "AGENTS.md").exists()


def test_agents_md_apps_dashboard_has_required_sections():
    # AC1: apps/dashboard/AGENTS.md contains all required sections
    content = _read_agents_md(REPO_ROOT / "apps" / "dashboard")
    for section in REQUIRED_SECTIONS:
        assert section in content, (
            f"Section '{section}' missing from apps/dashboard/AGENTS.md"
        )


def test_agents_md_routers_has_required_sections():
    # AC1: apps/dashboard/routers/AGENTS.md contains all required sections
    content = _read_agents_md(REPO_ROOT / "apps" / "dashboard" / "routers")
    for section in REQUIRED_SECTIONS:
        assert section in content, (
            f"Section '{section}' missing from apps/dashboard/routers/AGENTS.md"
        )


def test_agents_md_sprint_manager_has_required_sections():
    # AC1: services/sprint_manager/AGENTS.md contains all required sections
    content = _read_agents_md(REPO_ROOT / "services" / "sprint_manager")
    for section in REQUIRED_SECTIONS:
        assert section in content, (
            f"Section '{section}' missing from services/sprint_manager/AGENTS.md"
        )


def test_agents_md_scripts_has_required_sections():
    # AC1: scripts/AGENTS.md contains all required sections
    content = _read_agents_md(REPO_ROOT / "scripts")
    for section in REQUIRED_SECTIONS:
        assert section in content, (
            f"Section '{section}' missing from scripts/AGENTS.md"
        )


def test_agents_md_static_has_required_sections():
    # AC1: apps/dashboard/static/AGENTS.md contains all required sections
    content = _read_agents_md(REPO_ROOT / "apps" / "dashboard" / "static")
    for section in REQUIRED_SECTIONS:
        assert section in content, (
            f"Section '{section}' missing from apps/dashboard/static/AGENTS.md"
        )


def test_agents_md_sections_are_non_empty():
    # AC1: sections must not be empty placeholders
    for directory in AGENTS_MD_DIRS:
        content = _read_agents_md(directory)
        lines = content.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("## "):
                # Find next section or end of file
                next_section = len(lines)
                for j in range(i + 1, len(lines)):
                    if lines[j].startswith("## "):
                        next_section = j
                        break
                section_body = "\n".join(lines[i + 1:next_section]).strip()
                assert section_body, (
                    f"Section '{line}' is empty in {directory.relative_to(REPO_ROOT)}/AGENTS.md"
                )


# ── AC2: Documenter agent has batch seed mode ─────────────────────────────────

def test_documenter_has_batch_seed_mode_instructions():
    # AC2: documenter.md contains instructions for seeding AGENTS.md in batch
    content = DOCUMENTER_AGENT.read_text()
    assert "AGENTS.md" in content, (
        "documenter.md must reference AGENTS.md seeding"
    )
    assert "seed" in content.lower() or "batch" in content.lower(), (
        "documenter.md must describe batch/seed mode for AGENTS.md"
    )


# ── AC3 + AC4: Coder workflow reads AGENTS.md ─────────────────────────────────

def test_coder_md_has_agents_md_reading_step():
    # AC3: coder.md workflow instructs coder to read AGENTS.md for implicated dirs
    content = CODER_AGENT.read_text()
    assert "AGENTS.md" in content, (
        "coder.md must instruct the coder to read AGENTS.md files"
    )


def test_coder_md_reads_agents_md_before_implementation():
    # AC3: the AGENTS.md reading step appears before the implementation step (Step 5)
    content = CODER_AGENT.read_text()
    agents_pos = content.find("AGENTS.md")
    implement_pos = content.find("### Step 5")
    assert agents_pos != -1, "AGENTS.md not found in coder.md"
    assert implement_pos != -1, "Step 5 not found in coder.md"
    assert agents_pos < implement_pos, (
        "AGENTS.md reading instruction must appear before Step 5 (Implement)"
    )


def test_coder_md_confirms_agents_md_loaded():
    # AC4: coder.md requires confirmation/log that AGENTS.md was loaded
    content = CODER_AGENT.read_text()
    # The step must say to log or confirm which AGENTS.md files were read
    has_log_instruction = (
        "log" in content.lower()
        or "confirm" in content.lower()
        or "print" in content.lower()
    ) and "AGENTS.md" in content
    assert has_log_instruction, (
        "coder.md must instruct the coder to log/confirm which AGENTS.md files were loaded"
    )


# ── AC5: Documenter flags needs-review ────────────────────────────────────────

def test_documenter_flags_agents_md_needs_review():
    # AC5: documenter.md instructs flagging AGENTS.md as needs-review when
    # the directory had changes this sprint (no auto-rewrite)
    content = DOCUMENTER_AGENT.read_text()
    assert "needs-review" in content, (
        "documenter.md must contain 'needs-review' flagging logic for AGENTS.md"
    )
    assert "AGENTS.md" in content, (
        "documenter.md must reference AGENTS.md in its post-sprint step"
    )


def test_documenter_does_not_auto_rewrite_agents_md():
    # AC5: documenter.md must NOT say it auto-rewrites AGENTS.md content
    content = DOCUMENTER_AGENT.read_text()
    # The flag-only constraint: check that rewrite is not permitted for AGENTS.md
    # by verifying the instruction says "flag only" or "do not rewrite"
    lower = content.lower()
    assert "auto-rewrite" not in lower or "no auto-rewrite" in lower or "flag only" in lower, (
        "documenter.md must not permit auto-rewriting of AGENTS.md (flag only)"
    )
