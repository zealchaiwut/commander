"""Tests for issue #1541: _build_design_block skips gh API call when issue_body is provided.

AC items verified:
  AC-1  When issue_body is passed (not None), subprocess.run is NOT called.
  AC-2  When issue_body is passed, the returned block reflects the body's
        ## Design Refs section (refs resolved correctly from the passed body).
  AC-3  When issue_body is None (default), the function falls back to the
        existing gh api subprocess call (backward compat unchanged).
  AC-4  Heading-index path: when issue_body is passed but has no ## Design Refs,
        the heading index from DESIGN.md is returned without calling subprocess.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

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


def _issue_body_without_refs() -> str:
    return """\
## What & Why

Test issue without design refs.

## Acceptance Criteria

- [ ] Something
"""


def _make_gh_response(body: str) -> MagicMock:
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = json.dumps({"body": body, "number": 99, "title": "Test"})
    return mock


# ---------------------------------------------------------------------------
# AC-1: subprocess.run is NOT called when issue_body is provided
# ---------------------------------------------------------------------------

class TestAC1NoSubprocessWhenBodyProvided:
    def test_subprocess_not_called_when_body_passed(self, tmp_path):
        """subprocess.run must not be called when issue_body is provided."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")
        body = _issue_body_without_refs()

        with patch("subprocess.run") as mock_run:
            _build_design_block(99, "owner/repo", tmp_path, issue_body=body)

        mock_run.assert_not_called()

    def test_subprocess_not_called_with_refs_body(self, tmp_path):
        """subprocess.run must not be called even when the body contains ## Design Refs."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")
        body = _issue_body_with_refs("DESIGN.md#sprint-board")

        with patch("subprocess.run") as mock_run:
            _build_design_block(99, "owner/repo", tmp_path, issue_body=body)

        mock_run.assert_not_called()

    def test_subprocess_not_called_with_empty_string_body(self, tmp_path):
        """Passing issue_body='' (empty string, not None) must skip subprocess."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")

        with patch("subprocess.run") as mock_run:
            _build_design_block(99, "owner/repo", tmp_path, issue_body="")

        mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# AC-2: passed body is used for design-refs resolution
# ---------------------------------------------------------------------------

class TestAC2PassedBodyUsedForRefs:
    def test_design_ref_resolved_from_passed_body(self, tmp_path):
        """When issue_body has ## Design Refs, the ref is resolved from DESIGN.md."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")
        body = _issue_body_with_refs("DESIGN.md#sprint-board")

        block = _build_design_block(99, "owner/repo", tmp_path, issue_body=body)

        assert "Sprint Board" in block or "sprint board" in block.lower(), (
            "Design ref from the passed body must resolve to section text"
        )
        assert "Each sprint has tickets in columns" in block, (
            "Resolved section body must appear in the returned block"
        )

    def test_multiple_refs_resolved_from_passed_body(self, tmp_path):
        """All refs in the passed body's ## Design Refs section are resolved."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")
        body = _issue_body_with_refs("DESIGN.md#sprint-board", "DESIGN.md#ticket-lifecycle")

        block = _build_design_block(99, "owner/repo", tmp_path, issue_body=body)

        assert "sprint board" in block.lower() or "Sprint Board" in block
        assert "ticket" in block.lower()

    def test_no_refs_in_passed_body_returns_heading_index(self, tmp_path):
        """Body with no ## Design Refs returns the DESIGN.md heading index."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")
        body = _issue_body_without_refs()

        block = _build_design_block(99, "owner/repo", tmp_path, issue_body=body)

        assert block, "Must return non-empty block when body has no refs"
        assert "Sprint Board" in block or "Ticket Lifecycle" in block, (
            "Heading index must be returned when body has no ## Design Refs"
        )


# ---------------------------------------------------------------------------
# AC-3: fallback to gh api when issue_body is None (backward compat)
# ---------------------------------------------------------------------------

class TestAC3FallbackToGhWhenBodyIsNone:
    def test_subprocess_called_when_body_is_none(self, tmp_path):
        """subprocess.run IS called when issue_body=None (default)."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")
        body = _issue_body_without_refs()

        with patch("subprocess.run", return_value=_make_gh_response(body)) as mock_run:
            _build_design_block(99, "owner/repo", tmp_path)  # no issue_body arg

        mock_run.assert_called_once()

    def test_explicit_none_triggers_subprocess(self, tmp_path):
        """Explicitly passing issue_body=None also triggers the subprocess call."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")
        body = _issue_body_without_refs()

        with patch("subprocess.run", return_value=_make_gh_response(body)) as mock_run:
            _build_design_block(99, "owner/repo", tmp_path, issue_body=None)

        mock_run.assert_called_once()

    def test_fallback_result_matches_passed_body_result(self, tmp_path):
        """Both paths (passed body vs gh fetch) return equivalent blocks for the same content."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")
        body = _issue_body_without_refs()

        # Path 1: pass body directly
        block_direct = _build_design_block(99, "owner/repo", tmp_path, issue_body=body)

        # Path 2: body returned via gh subprocess mock
        with patch("subprocess.run", return_value=_make_gh_response(body)):
            block_via_gh = _build_design_block(99, "owner/repo", tmp_path)

        assert block_direct == block_via_gh, (
            "Both paths must produce identical output for the same issue body"
        )


# ---------------------------------------------------------------------------
# AC-4: heading-index path with passed body (no subprocess)
# ---------------------------------------------------------------------------

class TestAC4HeadingIndexWithPassedBody:
    def test_heading_index_no_subprocess_when_body_passed(self, tmp_path):
        """Heading-index path (no design refs) runs without subprocess when body is passed."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")

        with patch("subprocess.run") as mock_run:
            block = _build_design_block(
                99, "owner/repo", tmp_path, issue_body=_issue_body_without_refs()
            )

        mock_run.assert_not_called()
        assert "Sprint Board" in block or "Ticket Lifecycle" in block

    def test_no_repo_with_passed_body_skips_subprocess(self, tmp_path):
        """Even with eff_repo=None, passing issue_body must not trigger subprocess."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")

        with patch("subprocess.run") as mock_run:
            _build_design_block(99, None, tmp_path, issue_body=_issue_body_without_refs())

        mock_run.assert_not_called()
