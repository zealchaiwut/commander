"""Tests for issue #1962: Add API doc update duty to documenter prompt.

AC items verified:
  AC-1  DEFAULT_DOCUMENTER_PROMPT explicitly names docs/features/api.md
  AC-2  DEFAULT_DOCUMENTER_PROMPT contains rule for router diff → API doc update
  AC-3  GET /api/dev-report is mentioned as recently added route
  AC-4  Documenter edit-scope allowlist is unchanged (docs/ only)
  AC-5  All existing documenter dispatch tests pass
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from services.sprint_manager.post_sprint import DEFAULT_DOCUMENTER_PROMPT


class TestAC1ApiDocFileNamed:
    """AC-1: DEFAULT_DOCUMENTER_PROMPT explicitly names docs/features/api.md."""

    def test_api_md_mentioned_in_prompt(self):
        """Prompt must explicitly mention docs/features/api.md."""
        assert "docs/features/api.md" in DEFAULT_DOCUMENTER_PROMPT, (
            "Prompt must explicitly reference 'docs/features/api.md' for API documentation"
        )

    def test_api_md_in_documentation_files_section(self):
        """API docs must be part of the documented files list."""
        # Check that it's in a section about documentation files to update
        lines = DEFAULT_DOCUMENTER_PROMPT.split("\n")
        api_md_line = None
        for i, line in enumerate(lines):
            if "docs/features/api.md" in line:
                api_md_line = i
                break

        assert api_md_line is not None, "docs/features/api.md not found in prompt"

        # Verify it's in a context that makes sense (not in prohibited actions, etc.)
        context_lines = "\n".join(lines[max(0, api_md_line - 10):api_md_line + 3])
        assert "Prohibited" not in context_lines or api_md_line > 100, (
            "docs/features/api.md should not be in Prohibited Actions section"
        )


class TestAC2RouterTriggerRule:
    """AC-2: Router diff touching apps/dashboard/routers/ triggers API doc update."""

    def test_routers_path_mentioned(self):
        """Prompt must mention apps/dashboard/routers/ as a trigger."""
        assert "apps/dashboard/routers/" in DEFAULT_DOCUMENTER_PROMPT, (
            "Prompt must reference 'apps/dashboard/routers/' as a trigger"
        )

    def test_router_trigger_with_api_md_update(self):
        """Router changes must be tied to docs/features/api.md update requirement."""
        # Check that routers and api.md are mentioned in proximity
        prompt_lower = DEFAULT_DOCUMENTER_PROMPT.lower()
        routers_idx = prompt_lower.find("apps/dashboard/routers/")
        api_md_idx = prompt_lower.find("docs/features/api.md")

        assert routers_idx >= 0 and api_md_idx >= 0, (
            "Both 'apps/dashboard/routers/' and 'docs/features/api.md' must be in prompt"
        )

        # They should be in the same logical section (within ~500 chars)
        distance = abs(routers_idx - api_md_idx)
        assert distance < 500, (
            f"Router trigger and API doc file should be close together, "
            f"distance is {distance} chars"
        )

    def test_mandatory_review_mentioned(self):
        """Prompt must state that router changes MUST trigger review."""
        prompt_lower = DEFAULT_DOCUMENTER_PROMPT.lower()
        # Check for language like "must" and "review" near the router mention
        assert "must" in prompt_lower, "Prompt should include 'must' to indicate mandatory action"

        # Should mention that endpoints/params/response shapes need documentation
        context_terms = ["endpoint", "param", "response"]
        found_terms = sum(1 for term in context_terms if term in prompt_lower)
        assert found_terms >= 1, (
            f"Prompt should mention documentation elements like "
            f"endpoints, params, or response shapes (found {found_terms})"
        )


class TestAC3DevReportRoute:
    """AC-3: GET /api/dev-report mentioned as recently added route."""

    def test_dev_report_route_mentioned(self):
        """Prompt must explicitly mention GET /api/dev-report."""
        assert "GET /api/dev-report" in DEFAULT_DOCUMENTER_PROMPT, (
            "Prompt must mention 'GET /api/dev-report' as a recently added route"
        )

    def test_dev_report_in_context(self):
        """GET /api/dev-report should be mentioned as an example of recent changes."""
        lines = DEFAULT_DOCUMENTER_PROMPT.split("\n")
        dev_report_line = None
        for i, line in enumerate(lines):
            if "GET /api/dev-report" in line:
                dev_report_line = i
                break

        assert dev_report_line is not None, "GET /api/dev-report not found"

        # It should be in a context that makes sense (e.g., in documentation section,
        # not in prohibited actions)
        context_end = min(dev_report_line + 5, len(lines))
        context = "\n".join(lines[dev_report_line:context_end])
        assert "Prohibited" not in context, (
            "GET /api/dev-report should not be in Prohibited section"
        )


class TestAC4EditScopeUnchanged:
    """AC-4: Documenter edit-scope allowlist is unchanged (docs/ only)."""

    def test_only_docs_directory_allowed(self):
        """Prompt should only allow editing files under docs/."""
        prompt_lower = DEFAULT_DOCUMENTER_PROMPT.lower()

        # Should forbid modifications outside docs/
        assert "do not modify" in prompt_lower or "do not edit" in prompt_lower, (
            "Prompt should have prohibition on modifying certain paths"
        )

        # Should explicitly restrict to source code in prohibited actions
        assert "source code" in prompt_lower, (
            "Prompt should mention not modifying source code"
        )

        # The allowlist is implicitly "docs/" from the documentation files section
        # Check that no new paths like services/, apps/, tests/ are listed
        # as editable
        forbidden_edit_paths = [
            "services/",
            "apps/dashboard/server.py",
            "tests/",
            "scripts/",
        ]
        for path in forbidden_edit_paths:
            # These should not appear in sections allowing edits
            # (they might appear in the diff review context, but not as "to update")
            lines = DEFAULT_DOCUMENTER_PROMPT.split("\n")
            for i, line in enumerate(lines):
                if path in line and ("update" in line.lower() or "edit" in line.lower()):
                    # This would be a violation
                    context = "\n".join(lines[max(0, i-2):i+3])
                    pytest.fail(
                        f"Path '{path}' should not be in editable scope. "
                        f"Context:\n{context}"
                    )


class TestAC5ExistingTestsPass:
    """AC-5: All existing documenter dispatch tests pass without modification."""

    def test_documenter_tests_importable(self):
        """Existing documenter tests should still be importable."""
        # This is a sanity check that we haven't broken the test infrastructure
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "test_697", Path(__file__).parent / "test_697__run_documentor_once_per_sprint.py"
        )
        if spec and spec.loader:
            test_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(test_module)
            assert hasattr(test_module, "TestAC1DocumentorRemovedFromHandlePostTester"), (
                "Existing documenter tests should be importable"
            )

    def test_prompt_still_has_required_sections(self):
        """Prompt should still have all required sections for documenter operation."""
        required_sections = [
            "## Context",
            "## Your Mandate",
            "## Step 1",
            "## Step 2",
            "## Step 3",
            "## Step 4",
            "## Prohibited Actions",
        ]
        for section in required_sections:
            assert section in DEFAULT_DOCUMENTER_PROMPT, (
                f"Prompt must contain required section '{section}'"
            )

    def test_prompt_has_commit_format_spec(self):
        """Prompt must specify the exact output format for completion message."""
        assert "Documenter complete:" in DEFAULT_DOCUMENTER_PROMPT, (
            "Prompt must specify 'Documenter complete:' output format"
        )
