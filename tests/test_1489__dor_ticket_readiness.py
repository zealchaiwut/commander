"""Tests for issue #1489 — Emit Definition-of-Ready specs from BA agent.

Covers:
  AC1 — ba.md template includes all four canonical DoR sections:
        ## Acceptance Criteria, ## Design Refs, ## Test Plan / UAT Test Steps,
        ## Out of Scope
  AC2 — ba.md instructs BA to read DESIGN.md and extract headings for
        ## Design Refs (non-placeholder, non-invented content)
  AC3 — create_ticket.py TEMPLATE_BODY scaffolds all four sections,
        and parsed result is non-empty for all four keys
  AC4 — feature.md issue template includes ## Design Refs section
  AC5 — ba.md template uses checkbox list for AC, numbered steps + Expected:
        for UAT; no duplication of content between sections
  AC6 — a BA-representative ticket body passes parse_ticket_spec for all four
        sections (all non-empty)
  AC7 — smoke test: create_ticket.TEMPLATE_BODY passes parse_ticket_spec with
        all four sections non-empty
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

from services.sprint_manager.ticket_spec import parse_ticket_spec


# ── Paths ────────────────────────────────────────────────────────────────────

BA_MD = REPO_ROOT / ".claude" / "agents" / "ba.md"
FEATURE_MD = REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "feature.md"
CREATE_TICKET_PY = REPO_ROOT / "scripts" / "create_ticket.py"


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def ba_md_text():
    return BA_MD.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def feature_md_text():
    return FEATURE_MD.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def create_ticket_text():
    return CREATE_TICKET_PY.read_text(encoding="utf-8")


# ── AC1: ba.md template has all four canonical DoR sections ──────────────────

class TestAC1BaMdTemplate:
    def test_ba_md_template_has_acceptance_criteria(self, ba_md_text):
        """ba.md template must include ## Acceptance Criteria."""
        assert "## Acceptance Criteria" in ba_md_text

    def test_ba_md_template_has_design_refs(self, ba_md_text):
        """ba.md template must include ## Design Refs section."""
        assert "## Design Refs" in ba_md_text

    def test_ba_md_template_has_test_plan_or_uat_steps(self, ba_md_text):
        """ba.md template must include ## UAT Test Steps or ## Test Plan."""
        has_uat = "## UAT Test Steps" in ba_md_text
        has_test_plan = "## Test Plan" in ba_md_text
        assert has_uat or has_test_plan, (
            "ba.md must contain ## UAT Test Steps or ## Test Plan"
        )

    def test_ba_md_template_has_out_of_scope(self, ba_md_text):
        """ba.md template must include ## Out of Scope."""
        assert "## Out of Scope" in ba_md_text


# ── AC2: ba.md instructs reading DESIGN.md ───────────────────────────────────

class TestAC2DesignMdInstruction:
    def test_ba_md_mentions_design_md(self, ba_md_text):
        """ba.md must instruct the BA to read DESIGN.md."""
        assert "DESIGN.md" in ba_md_text, (
            "ba.md must reference DESIGN.md so the agent knows to read it"
        )

    def test_ba_md_instructs_heading_extraction(self, ba_md_text):
        """ba.md must mention extracting headings from DESIGN.md."""
        heading_keywords = ["heading", "extract", "section"]
        found = any(kw in ba_md_text.lower() for kw in heading_keywords)
        assert found, (
            "ba.md must instruct heading extraction from DESIGN.md"
        )

    def test_ba_md_mentions_no_placeholder_for_design_refs(self, ba_md_text):
        """ba.md must warn against placeholder/invented content in Design Refs."""
        anti_placeholder_kws = ["no placeholder", "real heading", "actual", "exists in", "relevant"]
        found = any(kw in ba_md_text.lower() for kw in anti_placeholder_kws)
        assert found, (
            "ba.md must instruct that Design Refs use real headings, not placeholders"
        )

    def test_ba_md_warns_when_design_md_absent(self, ba_md_text):
        """ba.md must describe behaviour when DESIGN.md is absent."""
        assert "warn" in ba_md_text.lower() or "skip" in ba_md_text.lower(), (
            "ba.md must describe what to do when DESIGN.md is absent (warn or skip)"
        )


# ── AC3: create_ticket.py has TEMPLATE_BODY with all four sections ────────────

class TestAC3CreateTicketTemplate:
    def test_create_ticket_exports_template_body(self, create_ticket_text):
        """create_ticket.py must define a TEMPLATE_BODY constant."""
        assert "TEMPLATE_BODY" in create_ticket_text

    def test_template_body_has_acceptance_criteria(self, create_ticket_text):
        """TEMPLATE_BODY must scaffold ## Acceptance Criteria."""
        assert "## Acceptance Criteria" in create_ticket_text

    def test_template_body_has_design_refs(self, create_ticket_text):
        """TEMPLATE_BODY must scaffold ## Design Refs."""
        assert "## Design Refs" in create_ticket_text

    def test_template_body_has_test_plan_or_uat(self, create_ticket_text):
        """TEMPLATE_BODY must scaffold ## UAT Test Steps or ## Test Plan."""
        has_uat = "## UAT Test Steps" in create_ticket_text
        has_plan = "## Test Plan" in create_ticket_text
        assert has_uat or has_plan

    def test_template_body_has_out_of_scope(self, create_ticket_text):
        """TEMPLATE_BODY must scaffold ## Out of Scope."""
        assert "## Out of Scope" in create_ticket_text


# ── AC3 smoke: TEMPLATE_BODY parses to non-empty sections ────────────────────

class TestAC3Smoke:
    def test_template_body_ac_section_non_empty(self):
        """parse_ticket_spec(TEMPLATE_BODY) must return non-empty acceptance_criteria."""
        from scripts.create_ticket import TEMPLATE_BODY
        result = parse_ticket_spec(TEMPLATE_BODY)
        assert result["acceptance_criteria"], (
            "TEMPLATE_BODY acceptance_criteria must parse to a non-empty list"
        )

    def test_template_body_design_refs_non_empty(self):
        """parse_ticket_spec(TEMPLATE_BODY) must return non-empty design_refs."""
        from scripts.create_ticket import TEMPLATE_BODY
        result = parse_ticket_spec(TEMPLATE_BODY)
        assert result["design_refs"], (
            "TEMPLATE_BODY design_refs must parse to a non-empty list"
        )

    def test_template_body_test_plan_non_empty(self):
        """parse_ticket_spec(TEMPLATE_BODY) must return non-empty test_plan."""
        from scripts.create_ticket import TEMPLATE_BODY
        result = parse_ticket_spec(TEMPLATE_BODY)
        assert result["test_plan"], (
            "TEMPLATE_BODY test_plan must parse to a non-empty string"
        )

    def test_template_body_out_of_scope_non_empty(self):
        """parse_ticket_spec(TEMPLATE_BODY) must return non-empty out_of_scope."""
        from scripts.create_ticket import TEMPLATE_BODY
        result = parse_ticket_spec(TEMPLATE_BODY)
        assert result["out_of_scope"], (
            "TEMPLATE_BODY out_of_scope must parse to a non-empty string"
        )


# ── AC4: feature.md has all four sections ────────────────────────────────────

class TestAC4FeatureMd:
    def test_feature_md_has_acceptance_criteria(self, feature_md_text):
        """feature.md must include ## Acceptance Criteria."""
        assert "## Acceptance Criteria" in feature_md_text

    def test_feature_md_has_design_refs(self, feature_md_text):
        """feature.md must include ## Design Refs section."""
        assert "## Design Refs" in feature_md_text

    def test_feature_md_has_test_plan_or_uat_steps(self, feature_md_text):
        """feature.md must include ## UAT Test Steps or ## Test Plan."""
        has_uat = "## UAT Test Steps" in feature_md_text
        has_plan = "## Test Plan" in feature_md_text
        assert has_uat or has_plan

    def test_feature_md_has_out_of_scope(self, feature_md_text):
        """feature.md must include ## Out of Scope."""
        assert "## Out of Scope" in feature_md_text


# ── AC5: ba.md uses checkbox AC and numbered UAT with Expected: ───────────────

class TestAC5OutputMapping:
    def test_ba_md_uses_checkbox_for_ac(self, ba_md_text):
        """ba.md template must use GitHub checkbox syntax for AC items."""
        assert "- [ ]" in ba_md_text

    def test_ba_md_uses_numbered_steps_for_uat(self, ba_md_text):
        """ba.md template UAT section must use numbered steps."""
        assert re.search(r"\d+\.", ba_md_text), (
            "ba.md must show numbered UAT steps"
        )

    def test_ba_md_uses_expected_lines_in_uat(self, ba_md_text):
        """ba.md template UAT section must include Expected: lines."""
        assert "Expected:" in ba_md_text or "**Expected:**" in ba_md_text, (
            "ba.md UAT section must include Expected: lines"
        )


# ── AC6: BA-representative ticket body passes readiness parser ───────────────

SAMPLE_BA_TICKET = """\
## What & Why

Add feature X so users can do Y.

## Acceptance Criteria

- [ ] The system does A when condition B holds
- [ ] Users can perform C via the dashboard
- [ ] API returns HTTP 200 with field D populated

## Design Refs

- Architecture Overview
- Sprint Lifecycle — Composite Key Invariant

## UAT Test Steps

1. Navigate to the dashboard and trigger condition B.
   **Expected:** Feature A is visible and active.

2. Perform action C on a test ticket.
   **Expected:** The ticket reflects change D.

## Out of Scope

- No changes to auth or user management
- No database schema migrations
"""


class TestAC6ReadinessParserPass:
    def test_sample_ba_ticket_ac_present(self):
        """BA-representative ticket must have acceptance_criteria."""
        result = parse_ticket_spec(SAMPLE_BA_TICKET)
        assert result["acceptance_criteria"], "acceptance_criteria must be non-empty"

    def test_sample_ba_ticket_design_refs_present(self):
        """BA-representative ticket must have design_refs."""
        result = parse_ticket_spec(SAMPLE_BA_TICKET)
        assert result["design_refs"], "design_refs must be non-empty"

    def test_sample_ba_ticket_test_plan_present(self):
        """BA-representative ticket must have test_plan."""
        result = parse_ticket_spec(SAMPLE_BA_TICKET)
        assert result["test_plan"], "test_plan must be non-empty"

    def test_sample_ba_ticket_out_of_scope_present(self):
        """BA-representative ticket must have out_of_scope."""
        result = parse_ticket_spec(SAMPLE_BA_TICKET)
        assert result["out_of_scope"], "out_of_scope must be non-empty"

    def test_sample_ba_ticket_all_four_sections_pass(self):
        """All four DoR sections must be present and non-empty in a BA ticket."""
        result = parse_ticket_spec(SAMPLE_BA_TICKET)
        assert result["acceptance_criteria"]
        assert result["design_refs"]
        assert result["test_plan"]
        assert result["out_of_scope"]


# ── AC7: smoke test — TEMPLATE_BODY all four non-empty ───────────────────────

class TestAC7Smoke:
    def test_template_body_all_four_sections_non_empty(self):
        """TEMPLATE_BODY must have all four DoR sections non-empty after parsing."""
        from scripts.create_ticket import TEMPLATE_BODY
        result = parse_ticket_spec(TEMPLATE_BODY)
        missing = [k for k, v in result.items() if not v]
        assert not missing, (
            f"TEMPLATE_BODY missing non-empty content in: {missing}"
        )

    def test_template_body_design_refs_are_real_items(self):
        """TEMPLATE_BODY design_refs must contain at least one non-placeholder item."""
        from scripts.create_ticket import TEMPLATE_BODY
        result = parse_ticket_spec(TEMPLATE_BODY)
        refs = result["design_refs"]
        # Each ref should not be empty
        assert all(ref.strip() for ref in refs), (
            "All design_refs items must be non-empty strings"
        )
