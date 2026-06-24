"""Tests for issue #1543: Design ref slug not normalized before lookup.

AC items verified:
  AC-1  The ref fragment is normalized via _slugify_heading before comparison,
        not via strip().lower() only.
  AC-2  A ref written as DESIGN.md#Sprint Board (spaces) resolves to the same
        heading as DESIGN.md#sprint-board (already-slugified).
  AC-3  A ref with mixed case and spaces (e.g. DESIGN.md#Sprint  Board) matches
        the slugified heading sprint-board.
  AC-4  The existing happy path — refs already in slug form (#sprint-board) —
        continues to match correctly.
  AC-5  No other lookup behavior at the comparison site is altered beyond
        normalization of the ref fragment.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from services.sprint_manager.sprint_manager import (  # noqa: E402
    _build_design_block,
    _slugify_heading,
)

DESIGN_CONTENT = """\
# Project Design

## Sprint Board

The sprint board shows active and past sprints.
Each sprint has tickets in columns: backlog, in-progress, sit, uat.

## Ticket Lifecycle

Tickets flow: backlog → in-progress (coder) → sit (tester) → uat (human sign-off).

## Architecture Overview

FastAPI backend, SQLite DB, optional Neon/Postgres, esbuild frontend bundle.
"""


def _make_gh_response(body: str) -> MagicMock:
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = json.dumps({"body": body, "number": 99, "title": "Test"})
    return mock


def _issue_body_with_refs(*refs: str) -> str:
    ref_lines = "\n".join(f"- {r}" for r in refs)
    return f"""\
## What & Why

Test issue.

## Acceptance Criteria

- [ ] Something

## Design Refs

{ref_lines}
"""


# ---------------------------------------------------------------------------
# AC-1: ref fragment goes through _slugify_heading, not just strip().lower()
# ---------------------------------------------------------------------------

class TestAC1SlugifyHeadingUsed:
    def test_slugify_heading_converts_spaces_to_dashes(self):
        """_slugify_heading must convert spaces to dashes (prerequisite for AC-2/AC-3)."""
        assert _slugify_heading("Sprint Board") == "sprint-board"

    def test_slugify_heading_handles_multiple_spaces(self):
        """_slugify_heading must collapse multiple spaces into single dash."""
        assert _slugify_heading("Sprint  Board") == "sprint-board"

    def test_slugify_heading_handles_mixed_case(self):
        """_slugify_heading must lowercase."""
        assert _slugify_heading("SPRINT BOARD") == "sprint-board"


# ---------------------------------------------------------------------------
# AC-2: DESIGN.md#Sprint Board (spaces) resolves same as DESIGN.md#sprint-board
# ---------------------------------------------------------------------------

class TestAC2SpacedRefResolvesLikeSlugged:
    def test_spaced_ref_resolves_to_section(self, tmp_path):
        """DESIGN.md#Sprint Board (with space) must resolve to the Sprint Board section."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")
        body = _issue_body_with_refs("DESIGN.md#Sprint Board")

        with patch("subprocess.run", return_value=_make_gh_response(body)):
            block = _build_design_block(99, "owner/repo", tmp_path, issue_body=body)

        assert block, "Block must be non-empty when spaced ref matches a heading"
        assert "sprint board" in block.lower() or "Sprint Board" in block, (
            "Section content for Sprint Board must appear in the block"
        )

    def test_spaced_ref_same_result_as_slugged_ref(self, tmp_path):
        """DESIGN.md#Sprint Board must return same content as DESIGN.md#sprint-board."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")

        body_spaced = _issue_body_with_refs("DESIGN.md#Sprint Board")
        body_slugged = _issue_body_with_refs("DESIGN.md#sprint-board")

        with patch("subprocess.run", return_value=_make_gh_response(body_spaced)):
            block_spaced = _build_design_block(99, "owner/repo", tmp_path, issue_body=body_spaced)

        with patch("subprocess.run", return_value=_make_gh_response(body_slugged)):
            block_slugged = _build_design_block(99, "owner/repo", tmp_path, issue_body=body_slugged)

        assert block_spaced == block_slugged, (
            "DESIGN.md#Sprint Board must resolve identically to DESIGN.md#sprint-board"
        )


# ---------------------------------------------------------------------------
# AC-3: Mixed case + multiple spaces also resolve correctly
# ---------------------------------------------------------------------------

class TestAC3MixedCaseAndDoubleSpaceRef:
    def test_double_space_ref_resolves(self, tmp_path):
        """DESIGN.md#Sprint  Board (double space) must resolve to Sprint Board section."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")
        body = _issue_body_with_refs("DESIGN.md#Sprint  Board")

        with patch("subprocess.run", return_value=_make_gh_response(body)):
            block = _build_design_block(99, "owner/repo", tmp_path, issue_body=body)

        assert block, "Block must be non-empty when double-spaced ref matches a heading"
        assert "sprint board" in block.lower() or "Sprint Board" in block

    def test_all_caps_with_space_ref_resolves(self, tmp_path):
        """DESIGN.md#SPRINT BOARD (all caps, space) must resolve to Sprint Board section."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")
        body = _issue_body_with_refs("DESIGN.md#SPRINT BOARD")

        with patch("subprocess.run", return_value=_make_gh_response(body)):
            block = _build_design_block(99, "owner/repo", tmp_path, issue_body=body)

        assert block, "Block must be non-empty when all-caps spaced ref matches a heading"
        assert "sprint board" in block.lower() or "Sprint Board" in block


# ---------------------------------------------------------------------------
# AC-4: Existing happy path — already-slugged refs still work
# ---------------------------------------------------------------------------

class TestAC4HappyPathRegression:
    def test_slugged_ref_still_resolves(self, tmp_path):
        """DESIGN.md#sprint-board must still resolve correctly after the fix."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")
        body = _issue_body_with_refs("DESIGN.md#sprint-board")

        with patch("subprocess.run", return_value=_make_gh_response(body)):
            block = _build_design_block(99, "owner/repo", tmp_path, issue_body=body)

        assert block, "Pre-slugged ref must still resolve"
        assert "sprint board" in block.lower() or "Sprint Board" in block

    def test_another_slugged_ref_resolves(self, tmp_path):
        """DESIGN.md#ticket-lifecycle must still resolve correctly."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")
        body = _issue_body_with_refs("DESIGN.md#ticket-lifecycle")

        with patch("subprocess.run", return_value=_make_gh_response(body)):
            block = _build_design_block(99, "owner/repo", tmp_path, issue_body=body)

        assert block, "Pre-slugged ticket-lifecycle ref must still resolve"
        assert "ticket" in block.lower()


# ---------------------------------------------------------------------------
# AC-5: Nonexistent heading still fails — normalization has no false positives
# ---------------------------------------------------------------------------

class TestAC5NoFalsePositives:
    def test_nonexistent_ref_returns_empty(self, tmp_path):
        """A ref to a heading that does not exist must return empty block (not a false match)."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")
        body = _issue_body_with_refs("DESIGN.md#nonexistent-section")

        with patch("subprocess.run", return_value=_make_gh_response(body)):
            block = _build_design_block(99, "owner/repo", tmp_path, issue_body=body)

        assert block == "", (
            "A ref to a nonexistent heading must not produce a block — normalization must not create false positives"
        )

    def test_spaced_nonexistent_ref_returns_empty(self, tmp_path):
        """A spaced ref to a heading that does not exist must also fail correctly."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")
        body = _issue_body_with_refs("DESIGN.md#No Such Section")

        with patch("subprocess.run", return_value=_make_gh_response(body)):
            block = _build_design_block(99, "owner/repo", tmp_path, issue_body=body)

        assert block == "", (
            "A spaced ref to a nonexistent heading must not produce a block"
        )
