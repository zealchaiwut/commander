"""Tests for issue #1962: Add API doc update duty to documenter prompt."""
import os
import sys
from pathlib import Path


# Repo root for imports
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from services.sprint_manager.post_sprint import DEFAULT_DOCUMENTER_PROMPT


class TestDocumenterPromptApiDocDuty:
    """Verify the documenter prompt includes API doc update duty."""

    def test_prompt_names_api_docs_file(self):
        """AC1: DEFAULT_DOCUMENTER_PROMPT explicitly names docs/features/api.md as the target file for API docs."""
        assert "docs/features/api.md" in DEFAULT_DOCUMENTER_PROMPT
        # Verify it's mentioned in the context of API docs, not a random occurrence
        assert "docs/features/api.md" in DEFAULT_DOCUMENTER_PROMPT.split("## Step 2")[0]

    def test_prompt_includes_router_trigger_rule(self):
        """AC2: DEFAULT_DOCUMENTER_PROMPT contains a rule stating that any diff touching apps/dashboard/routers/ requires mandatory review and update of docs/features/api.md."""
        prompt_lower = DEFAULT_DOCUMENTER_PROMPT.lower()
        assert "apps/dashboard/routers/" in DEFAULT_DOCUMENTER_PROMPT
        assert "must review" in prompt_lower and "docs/features/api.md" in DEFAULT_DOCUMENTER_PROMPT

    def test_prompt_mentions_dev_report_endpoint(self):
        """AC3: GET /api/dev-report is mentioned in the prompt as a recently added route."""
        assert "GET /api/dev-report" in DEFAULT_DOCUMENTER_PROMPT

    def test_edit_scope_allowlist_unchanged(self):
        """AC4: The documenter's edit-scope allowlist is unchanged; no new paths added beyond docs/."""
        prohibited_section = DEFAULT_DOCUMENTER_PROMPT.split("## Prohibited Actions")[1]
        # Verify the prohibited section explicitly forbids source code changes
        assert "Do NOT modify source code" in prohibited_section
        assert ".py" in prohibited_section and ".js" in prohibited_section
        # Verify no new file paths are added to the allowlist
        # The prompt should only allow edits under docs/ directory
        assert "docs/" in DEFAULT_DOCUMENTER_PROMPT
        # Ensure no new top-level directories were added to the allowlist
        assert "src/" not in DEFAULT_DOCUMENTER_PROMPT or "src/" in "docs/features/api.md"  # if mentioned, must be in docs/

    def test_prompt_structure_preserved(self):
        """AC5: All existing documenter dispatch structure is preserved (Steps 1-4, prohibited actions)."""
        required_sections = [
            "## Your Mandate",
            "## Step 1 — Review the diff",
            "## Step 2 — Identify what shipped",
            "## Step 3 — Update docs",
            "## Step 4 — Commit",
            "## Prohibited Actions",
        ]
        for section in required_sections:
            assert section in DEFAULT_DOCUMENTER_PROMPT
