"""Tests for services/sprint_manager/ticket_spec.py — issue #1485.

Covers:
  - Well-formed body: all four sections populated
  - Partial body: some sections missing
  - Empty body: no sections at all
  - Case-insensitive section headings
"""
import pytest
from services.sprint_manager.ticket_spec import parse_ticket_spec

WELL_FORMED_BODY = """\
## What & Why

Build the thing.

## Acceptance Criteria

- [ ] First criterion
- [ ] Second criterion
- [x] Already done criterion

## Design References

- docs/architecture/sprint-lifecycle.md
- docs/features/sprint-board.md

## UAT Test Steps

1. Open the page.
   **Expected:** Page loads.

2. Click the button.
   **Expected:** Modal appears.

## Out of Scope

- No UI changes
- No auth changes
"""

PARTIAL_BODY = """\
## What & Why

Build something partial.

## Acceptance Criteria

- [ ] Only criterion

## Out of Scope

- Nothing else
"""

EMPTY_BODY = ""


# ── AC1: well-formed body, all four sections populated ──────────────────────

def test_well_formed_acceptance_criteria():
    result = parse_ticket_spec(WELL_FORMED_BODY)
    assert result["acceptance_criteria"] == [
        "First criterion",
        "Second criterion",
        "Already done criterion",
    ]


def test_well_formed_design_refs():
    result = parse_ticket_spec(WELL_FORMED_BODY)
    assert result["design_refs"] == [
        "docs/architecture/sprint-lifecycle.md",
        "docs/features/sprint-board.md",
    ]


def test_well_formed_test_plan():
    result = parse_ticket_spec(WELL_FORMED_BODY)
    assert "Open the page" in result["test_plan"]
    assert "Click the button" in result["test_plan"]


def test_well_formed_out_of_scope():
    result = parse_ticket_spec(WELL_FORMED_BODY)
    assert "No UI changes" in result["out_of_scope"]


def test_well_formed_return_type():
    result = parse_ticket_spec(WELL_FORMED_BODY)
    assert isinstance(result["acceptance_criteria"], list)
    assert isinstance(result["design_refs"], list)
    assert isinstance(result["test_plan"], str)
    assert isinstance(result["out_of_scope"], str)


# ── AC2: partial body — missing sections return empty defaults ───────────────

def test_partial_missing_design_refs_returns_empty_list():
    result = parse_ticket_spec(PARTIAL_BODY)
    assert result["design_refs"] == []


def test_partial_missing_test_plan_returns_empty_string():
    result = parse_ticket_spec(PARTIAL_BODY)
    assert result["test_plan"] == ""


def test_partial_present_sections_still_parsed():
    result = parse_ticket_spec(PARTIAL_BODY)
    assert result["acceptance_criteria"] == ["Only criterion"]
    assert "Nothing else" in result["out_of_scope"]


# ── AC3: empty body — no exceptions, all empty defaults ─────────────────────

def test_empty_body_no_exception():
    result = parse_ticket_spec(EMPTY_BODY)
    assert result is not None


def test_empty_body_acceptance_criteria_is_empty_list():
    result = parse_ticket_spec("")
    assert result["acceptance_criteria"] == []


def test_empty_body_design_refs_is_empty_list():
    result = parse_ticket_spec("")
    assert result["design_refs"] == []


def test_empty_body_test_plan_is_empty_string():
    result = parse_ticket_spec("")
    assert result["test_plan"] == ""


def test_empty_body_out_of_scope_is_empty_string():
    result = parse_ticket_spec("")
    assert result["out_of_scope"] == ""


def test_empty_body_exact_shape():
    result = parse_ticket_spec("")
    assert result == {
        "acceptance_criteria": [],
        "design_refs": [],
        "test_plan": "",
        "out_of_scope": "",
    }


# ── AC2 (parser): case-insensitive section headings ─────────────────────────

def test_lowercase_acceptance_criteria_heading():
    body = "## acceptance criteria\n\n- [ ] lower case heading item\n"
    result = parse_ticket_spec(body)
    assert result["acceptance_criteria"] == ["lower case heading item"]


def test_uppercase_acceptance_criteria_heading():
    body = "## ACCEPTANCE CRITERIA\n\n- [ ] UPPER CASE ITEM\n"
    result = parse_ticket_spec(body)
    assert result["acceptance_criteria"] == ["UPPER CASE ITEM"]


def test_mixed_case_acceptance_criteria_heading():
    body = "## acceptance CRITERIA\n\n- [ ] mixed case item\n"
    result = parse_ticket_spec(body)
    assert result["acceptance_criteria"] == ["mixed case item"]


def test_case_insensitive_out_of_scope():
    body = "## out of scope\n\n- nothing here\n"
    result = parse_ticket_spec(body)
    assert "nothing here" in result["out_of_scope"]


def test_case_insensitive_uat_test_steps():
    body = "## uat test steps\n\n1. Do thing.\n   **Expected:** Works.\n"
    result = parse_ticket_spec(body)
    assert "Do thing" in result["test_plan"]


def test_case_insensitive_design_references():
    body = "## design references\n\n- some/path.md\n"
    result = parse_ticket_spec(body)
    assert result["design_refs"] == ["some/path.md"]
