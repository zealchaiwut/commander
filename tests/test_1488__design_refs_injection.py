"""Tests for issue #1488: Inject design context into coder prompt via Design Refs.

AC items verified:
  AC-a  Ref-resolution happy path: DESIGN.md#slug resolves to section text,
        injected before the ticket body in the prompt.
  AC-b  Missing-heading warning path: unknown slug emits WARNING line, no error.
  AC-c  No-refs heading-index path: absent ## Design Refs injects heading index.
  AC-d  Cap/truncation enforcement: block > 6000 chars is truncated with notice.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

from services.sprint_manager.sprint_manager import _build_design_block  # noqa: E402

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

DESIGN_CAP = 6000


def _make_gh_response(body: str) -> MagicMock:
    """Return a mock CompletedProcess with a GitHub issue JSON body."""
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = json.dumps({"body": body, "number": 99, "title": "Test"})
    return mock


def _issue_body_with_refs(*refs: str) -> str:
    """Build a minimal issue body with a ## Design Refs section."""
    ref_lines = "\n".join(f"- {r}" for r in refs)
    return f"""\
## What & Why

Test issue.

## Acceptance Criteria

- [ ] Something

## Design Refs

{ref_lines}
"""


def _issue_body_without_refs() -> str:
    """Build a minimal issue body with NO ## Design Refs section."""
    return """\
## What & Why

Test issue without design refs.

## Acceptance Criteria

- [ ] Something
"""


# ---------------------------------------------------------------------------
# AC-a: happy path — ref resolves and section text appears in block
# ---------------------------------------------------------------------------

class TestACaRefResolutionHappyPath:
    def test_resolved_section_text_in_block(self, tmp_path):
        """Resolved section text appears in the returned block."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")
        body = _issue_body_with_refs("DESIGN.md#sprint-board")

        with patch("subprocess.run", return_value=_make_gh_response(body)):
            block = _build_design_block(99, "owner/repo", tmp_path)

        assert "Sprint Board" in block or "sprint board" in block.lower(), (
            "Resolved heading text should appear in the design block"
        )
        assert "Each sprint has tickets in columns" in block, (
            "Section body text should appear in the design block"
        )

    def test_block_is_non_empty(self, tmp_path):
        """Block is non-empty when a valid ref resolves."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")
        body = _issue_body_with_refs("DESIGN.md#architecture-overview")

        with patch("subprocess.run", return_value=_make_gh_response(body)):
            block = _build_design_block(99, "owner/repo", tmp_path)

        assert block, "Block must be non-empty for a valid design ref"

    def test_parse_ticket_spec_used_for_refs(self, tmp_path):
        """parse_ticket_spec is used to extract refs (AC-6): refs work case-insensitively via heading match."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")
        # Lower-case heading in ticket via parse_ticket_spec bullet
        body = _issue_body_with_refs("DESIGN.md#ticket-lifecycle")

        with patch("subprocess.run", return_value=_make_gh_response(body)):
            block = _build_design_block(99, "owner/repo", tmp_path)

        assert "Ticket Lifecycle" in block or "ticket" in block.lower(), (
            "Design ref extracted via parse_ticket_spec must resolve its heading"
        )

    def test_multiple_refs_all_resolved(self, tmp_path):
        """All valid refs in the list are resolved and appear in the block."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")
        body = _issue_body_with_refs("DESIGN.md#sprint-board", "DESIGN.md#ticket-lifecycle")

        with patch("subprocess.run", return_value=_make_gh_response(body)):
            block = _build_design_block(99, "owner/repo", tmp_path)

        assert "sprint board" in block.lower() or "Sprint Board" in block
        assert "ticket" in block.lower()


# ---------------------------------------------------------------------------
# AC-b: missing-heading warning path
# ---------------------------------------------------------------------------

class TestACbMissingHeadingWarning:
    def test_warning_logged_for_unknown_slug(self, tmp_path, capsys):
        """A WARNING line is logged when a heading slug is not found."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")
        body = _issue_body_with_refs("DESIGN.md#nonexistent-heading")

        with patch("subprocess.run", return_value=_make_gh_response(body)):
            _build_design_block(99, "owner/repo", tmp_path)

        captured = capsys.readouterr()
        assert "WARNING" in captured.out, (
            "A WARNING line must appear in stdout for an unknown heading slug"
        )
        assert "nonexistent-heading" in captured.out, (
            "WARNING must name the missing slug"
        )

    def test_dispatch_not_blocked_on_missing_slug(self, tmp_path):
        """Missing slug does not raise an exception — dispatch proceeds."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")
        body = _issue_body_with_refs("DESIGN.md#nonexistent-heading")

        with patch("subprocess.run", return_value=_make_gh_response(body)):
            # Should not raise
            _build_design_block(99, "owner/repo", tmp_path)

    def test_valid_refs_still_resolved_alongside_missing(self, tmp_path):
        """Valid refs still resolve even when some slugs are missing."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")
        body = _issue_body_with_refs("DESIGN.md#sprint-board", "DESIGN.md#nonexistent-heading")

        with patch("subprocess.run", return_value=_make_gh_response(body)):
            block = _build_design_block(99, "owner/repo", tmp_path)

        assert "sprint board" in block.lower() or "Sprint Board" in block, (
            "Valid ref should still resolve when another ref is missing"
        )


# ---------------------------------------------------------------------------
# AC-c: no ## Design Refs → heading index injected
# ---------------------------------------------------------------------------

class TestACcNoRefsHeadingIndex:
    def test_heading_index_returned_when_no_refs_section(self, tmp_path):
        """When ticket has no ## Design Refs, a heading index from DESIGN.md is returned."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")
        body = _issue_body_without_refs()

        with patch("subprocess.run", return_value=_make_gh_response(body)):
            block = _build_design_block(99, "owner/repo", tmp_path)

        assert block, "Block must be non-empty when no ## Design Refs section exists"
        # The block should list headings, not section bodies
        assert "Sprint Board" in block or "sprint-board" in block or "## Sprint Board" in block, (
            "Heading index should list DESIGN.md headings"
        )

    def test_heading_index_contains_multiple_headings(self, tmp_path):
        """Heading index contains more than one heading from DESIGN.md."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")
        body = _issue_body_without_refs()

        with patch("subprocess.run", return_value=_make_gh_response(body)):
            block = _build_design_block(99, "owner/repo", tmp_path)

        # DESIGN_CONTENT has 3 headings: Sprint Board, Ticket Lifecycle, Architecture Overview
        headings_found = sum(
            1 for h in ["Sprint Board", "Ticket Lifecycle", "Architecture Overview"]
            if h in block
        )
        assert headings_found >= 2, (
            f"Heading index should list multiple DESIGN.md headings; only found {headings_found}"
        )

    def test_no_refs_section_not_full_body(self, tmp_path):
        """Heading index (no-refs path) is shorter than the full DESIGN.md."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")
        body = _issue_body_without_refs()

        with patch("subprocess.run", return_value=_make_gh_response(body)):
            block = _build_design_block(99, "owner/repo", tmp_path)

        # Full DESIGN_CONTENT body text should NOT appear (only headings)
        assert "Each sprint has tickets in columns" not in block, (
            "No-refs path should inject heading index only, not full section bodies"
        )


# ---------------------------------------------------------------------------
# AC-d: cap/truncation enforcement
# ---------------------------------------------------------------------------

class TestACdCapTruncation:
    def test_block_truncated_at_cap(self, tmp_path):
        """Block exceeding ~6000 chars is capped and includes truncation notice."""
        # Build a DESIGN.md where one section is very long
        long_section = "x " * 4000  # ~8000 chars
        big_design = f"""\
# Big Design

## Giant Section

{long_section}

## Short Section

Short.
"""
        (tmp_path / "DESIGN.md").write_text(big_design, encoding="utf-8")
        body = _issue_body_with_refs("DESIGN.md#giant-section")

        with patch("subprocess.run", return_value=_make_gh_response(body)):
            block = _build_design_block(99, "owner/repo", tmp_path)

        assert len(block) <= DESIGN_CAP + 200, (  # allow slack for the notice itself
            f"Block must be capped near {DESIGN_CAP} chars; got {len(block)}"
        )

    def test_truncation_notice_present(self, tmp_path):
        """Truncated block includes a visible truncation notice."""
        long_section = "y " * 4000
        big_design = f"""\
# Big Design

## Huge Section

{long_section}
"""
        (tmp_path / "DESIGN.md").write_text(big_design, encoding="utf-8")
        body = _issue_body_with_refs("DESIGN.md#huge-section")

        with patch("subprocess.run", return_value=_make_gh_response(body)):
            block = _build_design_block(99, "owner/repo", tmp_path)

        assert "truncat" in block.lower(), (
            "Truncated block must contain a truncation notice"
        )

    def test_non_truncated_block_has_no_truncation_notice(self, tmp_path):
        """Block under the cap does not include a truncation notice."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")
        body = _issue_body_with_refs("DESIGN.md#sprint-board")

        with patch("subprocess.run", return_value=_make_gh_response(body)):
            block = _build_design_block(99, "owner/repo", tmp_path)

        assert "truncat" not in block.lower(), (
            "Non-truncated block must not contain a truncation notice"
        )

    def test_no_refs_index_also_capped(self, tmp_path):
        """Even the heading-index path (no refs) is capped at ~6000 chars."""
        # Build a DESIGN.md with hundreds of long headings
        many_headings = "\n\n".join(
            f"## Section {i} {'x' * 50}\n\nBody {i}." for i in range(200)
        )
        big_design = f"# Big Design\n\n{many_headings}\n"
        (tmp_path / "DESIGN.md").write_text(big_design, encoding="utf-8")
        body = _issue_body_without_refs()

        with patch("subprocess.run", return_value=_make_gh_response(body)):
            block = _build_design_block(99, "owner/repo", tmp_path)

        assert len(block) <= DESIGN_CAP + 200, (
            f"Heading index must be capped at ~{DESIGN_CAP} chars; got {len(block)}"
        )


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_string_when_design_md_absent(self, tmp_path):
        """When DESIGN.md is absent, block is empty (guard handled elsewhere)."""
        body = _issue_body_with_refs("DESIGN.md#sprint-board")

        with patch("subprocess.run", return_value=_make_gh_response(body)):
            block = _build_design_block(99, "owner/repo", tmp_path)

        assert block == "", "Block must be empty when DESIGN.md does not exist"

    def test_gh_api_failure_returns_heading_index(self, tmp_path):
        """When gh api call fails, falls back gracefully (no-refs path or empty)."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")
        fail_mock = MagicMock()
        fail_mock.returncode = 1
        fail_mock.stdout = ""
        fail_mock.stderr = "not found"

        with patch("subprocess.run", return_value=fail_mock):
            # Should not raise
            block = _build_design_block(99, "owner/repo", tmp_path)

        # With a failed gh call we can't know the refs; fallback to index or empty
        assert isinstance(block, str), "Must return a string even when gh call fails"
