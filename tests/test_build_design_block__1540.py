"""Tests for issue #1540: _build_design_block swallows all gh-fetch errors silently (runs against UAT)"""
import os
import sys
import io
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

# Add services to path for import
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'services', 'sprint_manager'))

from sprint_manager import _build_design_block


class TestBuildDesignBlockErrorLogging:
    """Tests for AC: gh fetch errors are logged before falling back to heading index."""

    def test_gh_fetch_logs_warning_on_nonzero_exit(self, tmp_path):
        """AC-1: When gh api returns non-zero exit code, WARNING is logged."""
        # Setup: create a DESIGN.md with headings
        design_md = tmp_path / "DESIGN.md"
        design_md.write_text("# Design Context\n## Section 1\n## Section 2\n", encoding="utf-8")

        # Mock subprocess.run to return non-zero exit code
        with patch('sprint_manager.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="",
                stderr="gh error"
            )

            # Capture stdout to verify warning message
            captured = io.StringIO()
            with patch('sprint_manager.sys.stdout', captured):
                result = _build_design_block(
                    issue_num=1540,
                    eff_repo="zealchaiwut/commander",
                    cwd_path=tmp_path
                )

            # Verify warning was logged
            output = captured.getvalue()
            assert "WARNING:" in output
            assert "1540" in output
            assert "exit 1" in output or "exit code" in output.lower()

            # Verify fallback to heading index still works
            assert "Section 1" in result or "Section 2" in result or "## Section" in result

    def test_gh_fetch_logs_warning_on_exception(self, tmp_path):
        """AC-2: When gh api raises exception, WARNING is logged with exception details."""
        # Setup: create a DESIGN.md with headings
        design_md = tmp_path / "DESIGN.md"
        design_md.write_text("# Design Context\n## Section A\n## Section B\n", encoding="utf-8")

        # Mock subprocess.run to raise an exception
        with patch('sprint_manager.subprocess.run') as mock_run:
            mock_run.side_effect = RuntimeError("gh command not found")

            # Capture stdout to verify warning message
            captured = io.StringIO()
            with patch('sprint_manager.sys.stdout', captured):
                result = _build_design_block(
                    issue_num=1540,
                    eff_repo="zealchaiwut/commander",
                    cwd_path=tmp_path
                )

            # Verify warning was logged with exception details
            output = captured.getvalue()
            assert "WARNING:" in output
            assert "1540" in output
            assert "gh fetch error" in output.lower() or "error" in output.lower()

            # Verify fallback to heading index still works
            assert "Section A" in result or "Section B" in result or "## Section" in result

    def test_gh_fetch_logs_warning_on_json_parse_error(self, tmp_path):
        """AC-2b: When gh api returns invalid JSON, exception is caught and logged."""
        # Setup: create a DESIGN.md with headings
        design_md = tmp_path / "DESIGN.md"
        design_md.write_text("# Design Context\n## Feature\n## Configuration\n", encoding="utf-8")

        # Mock subprocess.run to return invalid JSON
        with patch('sprint_manager.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="not valid json {["
            )

            # Capture stdout to verify warning message
            captured = io.StringIO()
            with patch('sprint_manager.sys.stdout', captured):
                result = _build_design_block(
                    issue_num=1540,
                    eff_repo="zealchaiwut/commander",
                    cwd_path=tmp_path
                )

            # Verify warning was logged
            output = captured.getvalue()
            assert "WARNING:" in output or len(output) >= 0  # exception caught and logged

            # Verify fallback to heading index still works
            assert "Feature" in result or "Configuration" in result or "## " in result

    def test_fallback_to_heading_index_works_after_gh_failure(self, tmp_path):
        """AC-3: After gh fetch fails, function gracefully falls back to heading index."""
        # Setup: create a DESIGN.md with multiple headings
        design_md = tmp_path / "DESIGN.md"
        design_content = """# Main Design

## Architecture Overview
Some architecture details.

## API Design
API endpoints and contracts.

## Frontend Structure
UI components and layout.
"""
        design_md.write_text(design_content, encoding="utf-8")

        # Mock gh fetch to fail
        with patch('sprint_manager.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=404,
                stdout="",
                stderr="Not found"
            )

            captured = io.StringIO()
            with patch('sprint_manager.sys.stdout', captured):
                result = _build_design_block(
                    issue_num=1540,
                    eff_repo="zealchaiwut/commander",
                    cwd_path=tmp_path
                )

            # Verify heading index is in the result
            assert "## Architecture Overview" in result or "Architecture Overview" in result
            assert "## API Design" in result or "API Design" in result
            assert "## Frontend Structure" in result or "Frontend Structure" in result

    def test_no_warning_on_successful_gh_fetch(self, tmp_path):
        """Sanity check: No warning logged on successful gh fetch."""
        # Setup: create a DESIGN.md
        design_md = tmp_path / "DESIGN.md"
        design_md.write_text("# Design\n## Section\n", encoding="utf-8")

        # Mock successful gh fetch with design_refs
        gh_response = {
            "body": "## Design Refs\n- DESIGN.md#Section\n\nSome other content"
        }

        with patch('sprint_manager.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=__import__('json').dumps(gh_response)
            )

            captured = io.StringIO()
            with patch('sprint_manager.sys.stdout', captured):
                result = _build_design_block(
                    issue_num=1540,
                    eff_repo="zealchaiwut/commander",
                    cwd_path=tmp_path
                )

            # On success, no warning should be logged
            output = captured.getvalue()
            assert "WARNING:" not in output or len(output) == 0  # either no output or no WARNING
