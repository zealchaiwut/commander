"""
Tests for issue #860: Documentor writes per-sprint brief after each sprint.

Acceptance criteria covered:
- AC1: creates docs/sprint-briefs/sprint-<label>.md (folder created if missing)
- AC2: brief includes Shipped section with one line per ticket
- AC3: brief includes Now Visible / Changed section
- AC4: brief includes Awaiting UAT section with exercise instructions
- AC5: brief includes Did Not Ship section with stated reasons
- AC6: file is committed (commit flow invoked)
- AC7: sprint summary issue body updated with link to brief
- AC8: re-running same sprint label overwrites brief without breaking the link
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "apps" / "dashboard"))

from services.sprint_manager.brief_generator import (
    build_brief_content,
    write_sprint_brief,
    _brief_path,
    _shipped_section,
    _now_visible_section,
    _awaiting_uat_section,
    _did_not_ship_section,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@dataclass
class FakeIssue:
    number: int
    title: str
    status: str = "done"
    skip_reason: Optional[str] = None


@dataclass
class FakeSprintState:
    sprint_label: str
    sprint_number: Optional[int] = None
    issues: list = field(default_factory=list)


def _make_state(label: str = "sprint-60") -> FakeSprintState:
    return FakeSprintState(
        sprint_label=label,
        sprint_number=60,
        issues=[
            FakeIssue(number=100, title="Add dark mode toggle",   status="done"),
            FakeIssue(number=101, title="Fix login race condition", status="done"),
            FakeIssue(number=102, title="Add export to CSV",       status="done"),
            FakeIssue(number=103, title="Refactor DB layer",       status="skipped",
                      skip_reason="blocked by upstream schema change"),
        ],
    )


# ---------------------------------------------------------------------------
# AC1: docs/sprint-briefs/sprint-<label>.md is created (folder created)
# ---------------------------------------------------------------------------

def test_brief_path_is_under_sprint_briefs_dir(tmp_path):
    """_brief_path returns a path inside docs/sprint-briefs/."""
    p = _brief_path("sprint-60", tmp_path)
    assert p.parent == tmp_path / "docs" / "sprint-briefs"
    assert p.name == "sprint-sprint-60.md"


def test_brief_folder_is_created_if_missing(tmp_path):
    """write_sprint_brief creates docs/sprint-briefs/ when it does not exist."""
    briefs_dir = tmp_path / "docs" / "sprint-briefs"
    assert not briefs_dir.exists()

    with patch("services.sprint_manager.brief_generator._commit_brief", return_value="abc123"), \
         patch("services.sprint_manager.brief_generator._update_summary_issue_with_brief"):
        write_sprint_brief(state=_make_state(), repo_name="owner/repo", git_root=tmp_path)

    assert briefs_dir.exists()


# ---------------------------------------------------------------------------
# AC2: Shipped section
# ---------------------------------------------------------------------------

def test_shipped_section_contains_done_tickets():
    """Shipped section lists all done tickets."""
    issues = [
        FakeIssue(100, "Add dark mode toggle", "done"),
        FakeIssue(101, "Fix login race condition", "done"),
    ]
    section = _shipped_section(issues)
    assert "## Shipped" in section
    assert "#100" in section
    assert "Add dark mode toggle" in section
    assert "#101" in section
    assert "Fix login race condition" in section


def test_shipped_section_empty_sprint():
    """Shipped section handles no done tickets gracefully."""
    section = _shipped_section([])
    assert "## Shipped" in section
    assert "Nothing shipped" in section


# ---------------------------------------------------------------------------
# AC3: Now Visible / Changed section
# ---------------------------------------------------------------------------

def test_now_visible_section_present():
    """Now Visible / Changed section is present in the brief."""
    issues = [FakeIssue(100, "Add dark mode toggle", "done")]
    section = _now_visible_section(issues)
    assert "## Now Visible / Changed" in section
    assert "dark mode" in section.lower() or "#100" in section


# ---------------------------------------------------------------------------
# AC4: Awaiting UAT section
# ---------------------------------------------------------------------------

def test_awaiting_uat_section_has_exercise_instructions():
    """Awaiting UAT section includes at least one exercise instruction."""
    issues = [FakeIssue(100, "Add dark mode toggle", "done")]
    section = _awaiting_uat_section(issues)
    assert "## Awaiting UAT" in section
    assert "#100" in section
    # Each item should have a descriptive instruction (at least 10 chars after the title)
    assert len(section) > len("## Awaiting UAT\n\n- #100 Add dark mode toggle")


def test_awaiting_uat_section_empty():
    """Awaiting UAT section handles no pending items gracefully."""
    section = _awaiting_uat_section([])
    assert "## Awaiting UAT" in section
    assert "No items pending UAT" in section


# ---------------------------------------------------------------------------
# AC5: Did Not Ship section
# ---------------------------------------------------------------------------

def test_did_not_ship_section_has_reason():
    """Did Not Ship section includes stated reason for each skipped ticket."""
    issues = [
        FakeIssue(103, "Refactor DB layer", "skipped",
                  skip_reason="blocked by upstream schema change"),
    ]
    section = _did_not_ship_section(issues)
    assert "## Did Not Ship" in section
    assert "#103" in section
    assert "blocked by upstream schema change" in section


def test_did_not_ship_section_default_reason():
    """Skipped tickets with no reason get a fallback reason."""
    issues = [FakeIssue(103, "Refactor DB layer", "skipped", skip_reason=None)]
    section = _did_not_ship_section(issues)
    assert "## Did Not Ship" in section
    assert "#103" in section
    # Should have SOME reason, not just the title
    assert "rolled to next sprint" in section or len(section.split("—")) >= 2


def test_did_not_ship_section_empty():
    """Did Not Ship section handles all tickets shipping gracefully."""
    section = _did_not_ship_section([])
    assert "## Did Not Ship" in section
    assert "All planned items shipped" in section


# ---------------------------------------------------------------------------
# AC6: committed using existing commit flow
# ---------------------------------------------------------------------------

def test_write_sprint_brief_calls_commit(tmp_path):
    """write_sprint_brief calls _commit_brief to commit the file."""
    with patch("services.sprint_manager.brief_generator._commit_brief", return_value="sha123") as mock_commit, \
         patch("services.sprint_manager.brief_generator._update_summary_issue_with_brief"):
        write_sprint_brief(state=_make_state(), repo_name="owner/repo", git_root=tmp_path)
        mock_commit.assert_called_once()
        # First positional arg is the brief path
        brief_path_arg = mock_commit.call_args[0][0]
        assert "sprint-briefs" in str(brief_path_arg)


# ---------------------------------------------------------------------------
# AC7: sprint summary issue updated with link to brief
# ---------------------------------------------------------------------------

def test_write_sprint_brief_updates_summary_issue(tmp_path):
    """write_sprint_brief updates the summary issue with a link when issue num given."""
    with patch("services.sprint_manager.brief_generator._commit_brief", return_value="sha123"), \
         patch("services.sprint_manager.brief_generator._update_summary_issue_with_brief") as mock_update:
        write_sprint_brief(
            state=_make_state(),
            repo_name="owner/repo",
            git_root=tmp_path,
            summary_issue_num=999,
        )
        mock_update.assert_called_once_with(999, "sprint-60", "owner/repo")


def test_write_sprint_brief_no_update_without_issue_num(tmp_path):
    """write_sprint_brief skips issue update when summary_issue_num is None."""
    with patch("services.sprint_manager.brief_generator._commit_brief", return_value="sha123"), \
         patch("services.sprint_manager.brief_generator._update_summary_issue_with_brief") as mock_update:
        write_sprint_brief(
            state=_make_state(),
            repo_name="owner/repo",
            git_root=tmp_path,
            summary_issue_num=None,
        )
        mock_update.assert_not_called()


# ---------------------------------------------------------------------------
# AC8: re-run overwrites brief without breaking the link
# ---------------------------------------------------------------------------

def test_write_sprint_brief_overwrites_on_rerun(tmp_path):
    """Re-running write_sprint_brief overwrites the existing file."""
    with patch("services.sprint_manager.brief_generator._commit_brief", return_value="sha1"), \
         patch("services.sprint_manager.brief_generator._update_summary_issue_with_brief"):
        p1 = write_sprint_brief(state=_make_state(), repo_name="owner/repo", git_root=tmp_path)

    # Modify the file to simulate different content
    assert p1 is not None
    p1.write_text("old content", encoding="utf-8")

    with patch("services.sprint_manager.brief_generator._commit_brief", return_value="sha2"), \
         patch("services.sprint_manager.brief_generator._update_summary_issue_with_brief"):
        p2 = write_sprint_brief(state=_make_state(), repo_name="owner/repo", git_root=tmp_path)

    assert p2 is not None
    assert p1 == p2  # Same path
    content = p2.read_text()
    assert "old content" not in content  # Was overwritten
    assert "## Shipped" in content


# ---------------------------------------------------------------------------
# Integration: build_brief_content produces all four sections
# ---------------------------------------------------------------------------

def test_build_brief_content_has_all_sections():
    """build_brief_content output contains all four required sections."""
    state = _make_state("sprint-99")
    done    = [i for i in state.issues if i.status == "done"]
    skipped = [i for i in state.issues if i.status == "skipped"]

    content = build_brief_content(
        sprint_label   = "sprint-99",
        done_issues    = done,
        uat_issues     = done,
        skipped_issues = skipped,
    )

    assert "# Sprint sprint-99 Brief" in content
    assert "## Shipped" in content
    assert "## Now Visible / Changed" in content
    assert "## Awaiting UAT" in content
    assert "## Did Not Ship" in content
    # Verify specific tickets appear in shipped
    assert "#100" in content
    assert "#101" in content
    # Verify skipped ticket appears in Did Not Ship
    assert "#103" in content
    assert "blocked by upstream schema change" in content
