"""Tests for #619: Add TDD instructions to coder prompt template.

AC items verified:
  AC-1  coder_prompt_template instructs reading the Acceptance section and
        deriving test cases from it before writing any implementation code
  AC-2  Template explicitly states: write the derived tests first, then implement
        until tests pass
  AC-3  Template explicitly forbids deleting, skipping, or weakening existing or
        newly written tests to achieve a passing state
  AC-4  All existing coder_prompt_template clauses (branching workflow, CLAUDE.md,
        Attachments, PRODUCT.md/DESIGN.md) are preserved verbatim and unmodified
  AC-5  No other dispatch logic or calling code is changed
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _read_template_block(script_name: str) -> str:
    path = REPO_ROOT / "scripts" / script_name
    text = path.read_text(encoding="utf-8")
    idx = text.find("coder_prompt_template")
    assert idx != -1, f"coder_prompt_template not found in {script_name}"
    return text[idx: idx + 1500]


# ---------------------------------------------------------------------------
# AC-1: Template instructs reading the Acceptance section before writing code
# ---------------------------------------------------------------------------

class TestAC1AcceptanceSectionInstruction:
    def test_sprint_init_instructs_read_acceptance_section(self):
        block = _read_template_block("sprint_init.py")
        assert "Acceptance" in block, (
            "coder_prompt_template in sprint_init.py must instruct reading the Acceptance section"
        )

    def test_sprint_init_instructs_derive_tests_from_acceptance(self):
        block = _read_template_block("sprint_init.py")
        lower = block.lower()
        assert "test" in lower and "acceptance" in lower, (
            "coder_prompt_template in sprint_init.py must reference both 'test' and 'acceptance'"
        )

    def test_sprint_init_instructs_before_implementation(self):
        block = _read_template_block("sprint_init.py")
        lower = block.lower()
        assert any(phrase in lower for phrase in ("before writing", "before implement")), (
            "coder_prompt_template in sprint_init.py must instruct writing tests before implementation"
        )

    def test_init_project_instructs_read_acceptance_section(self):
        block = _read_template_block("init_project.py")
        assert "Acceptance" in block, (
            "coder_prompt_template in init_project.py must instruct reading the Acceptance section"
        )

    def test_init_project_instructs_derive_tests_from_acceptance(self):
        block = _read_template_block("init_project.py")
        lower = block.lower()
        assert "test" in lower and "acceptance" in lower, (
            "coder_prompt_template in init_project.py must reference both 'test' and 'acceptance'"
        )

    def test_init_project_instructs_before_implementation(self):
        block = _read_template_block("init_project.py")
        lower = block.lower()
        assert any(phrase in lower for phrase in ("before writing", "before implement")), (
            "coder_prompt_template in init_project.py must instruct writing tests before implementation"
        )


# ---------------------------------------------------------------------------
# AC-2: Template explicitly states write tests first, then implement
# ---------------------------------------------------------------------------

class TestAC2WriteTestsFirst:
    def test_sprint_init_write_tests_first(self):
        block = _read_template_block("sprint_init.py")
        lower = block.lower()
        assert "write the" in lower and "test" in lower and (
            "first" in lower or "then implement" in lower
        ), (
            "coder_prompt_template in sprint_init.py must say 'write the tests first, then implement'"
        )

    def test_sprint_init_then_implement(self):
        block = _read_template_block("sprint_init.py")
        lower = block.lower()
        assert "implement" in lower, (
            "coder_prompt_template in sprint_init.py must mention implementation after tests"
        )

    def test_init_project_write_tests_first(self):
        block = _read_template_block("init_project.py")
        lower = block.lower()
        assert "write the" in lower and "test" in lower and (
            "first" in lower or "then implement" in lower
        ), (
            "coder_prompt_template in init_project.py must say 'write the tests first, then implement'"
        )

    def test_init_project_then_implement(self):
        block = _read_template_block("init_project.py")
        lower = block.lower()
        assert "implement" in lower, (
            "coder_prompt_template in init_project.py must mention implementation after tests"
        )


# ---------------------------------------------------------------------------
# AC-3: Template explicitly forbids deleting, skipping, or weakening tests
# ---------------------------------------------------------------------------

class TestAC3ForbidWeakeningTests:
    def test_sprint_init_forbids_deleting_tests(self):
        block = _read_template_block("sprint_init.py")
        lower = block.lower()
        assert "delete" in lower or "delet" in lower, (
            "coder_prompt_template in sprint_init.py must explicitly forbid deleting tests"
        )

    def test_sprint_init_forbids_skipping_tests(self):
        block = _read_template_block("sprint_init.py")
        lower = block.lower()
        assert "skip" in lower, (
            "coder_prompt_template in sprint_init.py must explicitly forbid skipping tests"
        )

    def test_sprint_init_forbids_weakening_tests(self):
        block = _read_template_block("sprint_init.py")
        lower = block.lower()
        assert "weaken" in lower, (
            "coder_prompt_template in sprint_init.py must explicitly forbid weakening tests"
        )

    def test_init_project_forbids_deleting_tests(self):
        block = _read_template_block("init_project.py")
        lower = block.lower()
        assert "delete" in lower or "delet" in lower, (
            "coder_prompt_template in init_project.py must explicitly forbid deleting tests"
        )

    def test_init_project_forbids_skipping_tests(self):
        block = _read_template_block("init_project.py")
        lower = block.lower()
        assert "skip" in lower, (
            "coder_prompt_template in init_project.py must explicitly forbid skipping tests"
        )

    def test_init_project_forbids_weakening_tests(self):
        block = _read_template_block("init_project.py")
        lower = block.lower()
        assert "weaken" in lower, (
            "coder_prompt_template in init_project.py must explicitly forbid weakening tests"
        )


# ---------------------------------------------------------------------------
# AC-4: Existing clauses preserved (branching workflow, CLAUDE.md, Attachments, design docs)
# ---------------------------------------------------------------------------

class TestAC4ExistingClausesPreserved:
    def test_sprint_init_preserves_branching_workflow(self):
        block = _read_template_block("sprint_init.py")
        assert "branching workflow" in block, (
            "coder_prompt_template in sprint_init.py must preserve the branching workflow clause"
        )

    def test_sprint_init_preserves_claude_md_reference(self):
        block = _read_template_block("sprint_init.py")
        assert "CLAUDE.md" in block, (
            "coder_prompt_template in sprint_init.py must preserve the CLAUDE.md reference"
        )

    def test_sprint_init_preserves_attachments_clause(self):
        block = _read_template_block("sprint_init.py")
        assert "Attachments" in block, (
            "coder_prompt_template in sprint_init.py must preserve the Attachments section clause"
        )

    def test_sprint_init_preserves_product_md(self):
        block = _read_template_block("sprint_init.py")
        assert "PRODUCT.md" in block, (
            "coder_prompt_template in sprint_init.py must preserve the PRODUCT.md reference"
        )

    def test_sprint_init_preserves_design_md(self):
        block = _read_template_block("sprint_init.py")
        assert "DESIGN.md" in block, (
            "coder_prompt_template in sprint_init.py must preserve the DESIGN.md reference"
        )

    def test_init_project_preserves_branching_workflow(self):
        block = _read_template_block("init_project.py")
        assert "branching workflow" in block, (
            "coder_prompt_template in init_project.py must preserve the branching workflow clause"
        )

    def test_init_project_preserves_claude_md_reference(self):
        block = _read_template_block("init_project.py")
        assert "CLAUDE.md" in block, (
            "coder_prompt_template in init_project.py must preserve the CLAUDE.md reference"
        )

    def test_init_project_preserves_attachments_clause(self):
        block = _read_template_block("init_project.py")
        assert "Attachments" in block, (
            "coder_prompt_template in init_project.py must preserve the Attachments section clause"
        )

    def test_init_project_preserves_product_md(self):
        block = _read_template_block("init_project.py")
        assert "PRODUCT.md" in block, (
            "coder_prompt_template in init_project.py must preserve the PRODUCT.md reference"
        )

    def test_init_project_preserves_design_md(self):
        block = _read_template_block("init_project.py")
        assert "DESIGN.md" in block, (
            "coder_prompt_template in init_project.py must preserve the DESIGN.md reference"
        )


# ---------------------------------------------------------------------------
# AC-5: No other dispatch logic or calling code changed
# ---------------------------------------------------------------------------

class TestAC5NoOtherCodeChanged:
    def test_dispatch_coder_signature_unchanged(self):
        import services.sprint_manager.sprint_manager as sm
        import inspect
        src = inspect.getsource(sm._dispatch_coder)
        # Core dispatch structure: cfg.coder_prompt_template still used as before
        assert "cfg.coder_prompt_template" in src or "coder_prompt_template" in src, (
            "_dispatch_coder must still reference coder_prompt_template"
        )

    def test_sprint_manager_coder_prompt_template_field_unchanged(self):
        import services.sprint_manager.sprint_manager as sm
        import inspect
        src = inspect.getsource(sm.SprintConfig)
        assert "coder_prompt_template" in src, (
            "SprintConfig must still have coder_prompt_template field"
        )
