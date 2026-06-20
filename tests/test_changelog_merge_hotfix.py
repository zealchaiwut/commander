"""Tests for sprint-finish CHANGELOG auto-merge (startup.py hotfix)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

from startup import _changelog_sprint_sections  # noqa: E402


def test_changelog_sprint_sections_parses_multiple_blocks():
    text = (
        "# Changelog\n\n"
        "## Sprint 91\n\nSummary 91.\n\n"
        "## Sprint 90\n\nSummary 90.\n"
    )
    sections = _changelog_sprint_sections(text)
    assert "91" in sections
    assert "90" in sections
    assert "Summary 91." in sections["91"]
    assert sections["91"].startswith("## Sprint 91")


def test_changelog_merge_prepends_only_new_sprint_sections():
    """Sprint-only sections are prepended; develop sections are preserved."""
    sprint_text = (
        "# Changelog\n\n"
        "## Sprint 91\n\nNew sprint content.\n\n"
        "## Sprint 90\n\nOld shared.\n"
    )
    develop_text = (
        "# Changelog\n\n"
        "## Sprint 85.5\n\nDecomposition.\n\n"
        "## Sprint 90\n\nOld shared.\n"
    )
    sprint_sections = _changelog_sprint_sections(sprint_text)
    develop_sections = _changelog_sprint_sections(develop_text)
    new_labels = [lbl for lbl in sprint_sections if lbl not in develop_sections]
    prepend = "\n".join(sprint_sections[lbl].rstrip() for lbl in new_labels)
    rest = develop_text.split("\n", 1)[1].lstrip("\n")
    merged = f"# Changelog\n\n{prepend}\n\n{rest}"

    assert "## Sprint 91" in merged
    assert "New sprint content." in merged
    assert "## Sprint 85.5" in merged
    assert "Decomposition." in merged
    assert merged.index("## Sprint 91") < merged.index("## Sprint 85.5")
    assert "91" not in develop_sections
