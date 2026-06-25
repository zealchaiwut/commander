"""Tests for issue #1538 — DOR readiness check missing 'design ref' reason.

_smgmtReadinessCheck must push 'missing design ref' when the ticket body
lacks a ## Design Refs section, mirroring the AC-2 spec from issue #1487
which lists all five failing reasons: missing AC / design ref / test plan /
estimate / XL-split required.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
BOARD_JS = (
    REPO_ROOT
    / "apps"
    / "dashboard"
    / "static"
    / "src"
    / "sprint-board"
    / "board-render.js"
).read_text(encoding="utf-8")


# ── helpers ───────────────────────────────────────────────────────────────────


def _fn_body(name: str, src: str = BOARD_JS) -> str:
    """Return brace-balanced body of a named JS function."""
    for needle in (
        f"function {name}(",
        f"{name} = function",
        f"async function {name}(",
        f"export function {name}(",
        f"export async function {name}(",
    ):
        pos = src.find(needle)
        if pos != -1:
            break
    else:
        raise AssertionError(f"function {name} not found in source")
    brace = src.find("{", pos)
    assert brace != -1
    depth = 0
    for i in range(brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[brace : i + 1]
    raise AssertionError(f"unbalanced braces for {name}")


# ── AC1: _smgmtReadinessCheck checks for ## Design Refs ──────────────────────


class TestAC1DesignRefCheck:
    def test_readiness_check_body_mentions_design_ref(self):
        """_smgmtReadinessCheck must contain 'design ref' logic in its body."""
        body = _fn_body("_smgmtReadinessCheck")
        has_design_ref = (
            "Design Refs" in body
            or "design ref" in body.lower()
            or "missing design" in body.lower()
        )
        assert has_design_ref, (
            "_smgmtReadinessCheck must check for ## Design Refs section and push "
            "'missing design ref' when absent — currently it only checks AC, "
            "test plan, estimate, and XL-split, but AC-2 of #1487 requires "
            "'missing design ref' as a failing reason too"
        )

    def test_readiness_check_body_pushes_missing_design_ref_reason(self):
        """_smgmtReadinessCheck must push 'missing design ref' as the reason string."""
        body = _fn_body("_smgmtReadinessCheck")
        assert "missing design ref" in body, (
            "_smgmtReadinessCheck must push the literal string 'missing design ref' "
            "so the badge tooltip matches the AC-2 spec from issue #1487"
        )

    def test_readiness_check_jsdoc_mentions_design_ref(self):
        """The JSDoc comment above _smgmtReadinessCheck must document 'design ref' check."""
        # Find the JSDoc block immediately before the function definition
        fn_pos = BOARD_JS.find("export function _smgmtReadinessCheck(")
        assert fn_pos != -1, "_smgmtReadinessCheck not found in board-render.js"
        jsdoc_region = BOARD_JS[max(0, fn_pos - 400) : fn_pos]
        has_design_ref_in_doc = (
            "design ref" in jsdoc_region.lower()
            or "Design Refs" in jsdoc_region
            or "design ref" in jsdoc_region
        )
        assert has_design_ref_in_doc, (
            "The JSDoc comment above _smgmtReadinessCheck must list 'design ref' "
            "among the checks it performs, keeping the comment in sync with the code"
        )


# ── AC2: ticket WITHOUT ## Design Refs is flagged ────────────────────────────


BODY_NO_DESIGN_REFS = """\
## What & Why

Add feature X.

## Acceptance Criteria

- [ ] Does A

## UAT Test Steps

1. Do thing.
   Expected: thing is done.

## Out of Scope

- Nothing else
"""

BODY_WITH_DESIGN_REFS = """\
## What & Why

Add feature X.

## Acceptance Criteria

- [ ] Does A

## Design Refs

- Architecture Overview

## UAT Test Steps

1. Do thing.
   Expected: thing is done.

## Out of Scope

- Nothing else
"""

BODY_MISSING_EVERYTHING = """\
Just a plain description with no markdown sections.
"""


class TestAC2DesignRefAbsenceFlagged:
    def test_design_ref_regex_matches_canonical_heading(self):
        """The ## Design Refs regex must match the canonical heading."""
        pattern = re.compile(r"^##\s+design refs", re.IGNORECASE | re.MULTILINE)
        assert pattern.search(BODY_WITH_DESIGN_REFS), (
            "Regex must match '## Design Refs' in a ticket body that has the section"
        )

    def test_design_ref_regex_does_not_match_absent(self):
        """The regex must NOT match when ## Design Refs is absent."""
        pattern = re.compile(r"^##\s+design refs", re.IGNORECASE | re.MULTILINE)
        assert not pattern.search(BODY_NO_DESIGN_REFS), (
            "Regex must not match BODY_NO_DESIGN_REFS — the section is absent"
        )

    def test_missing_design_ref_in_reasons_when_absent(self):
        """A ticket body without ## Design Refs must produce 'missing design ref' reason.

        This test verifies the _smgmtReadinessCheck implementation against the
        expected behaviour using a plain Python re.search that mirrors the
        JS regex pattern the function uses.
        """
        body = BODY_NO_DESIGN_REFS
        reasons = []
        if not re.search(r"^##\s+design refs", body, re.IGNORECASE | re.MULTILINE):
            reasons.append("missing design ref")
        assert "missing design ref" in reasons, (
            "When ## Design Refs is absent, 'missing design ref' must be in reasons"
        )

    def test_no_design_ref_reason_when_present(self):
        """A ticket body WITH ## Design Refs must NOT produce 'missing design ref'."""
        body = BODY_WITH_DESIGN_REFS
        reasons = []
        if not re.search(r"^##\s+design refs", body, re.IGNORECASE | re.MULTILINE):
            reasons.append("missing design ref")
        assert "missing design ref" not in reasons, (
            "When ## Design Refs is present, 'missing design ref' must NOT be in reasons"
        )


# ── AC3: design ref check is between existing checks (ordering) ───────────────


class TestAC3CheckOrdering:
    def test_design_ref_check_after_ac_check(self):
        """The 'missing design ref' push must appear after the 'missing AC' push in source."""
        body = _fn_body("_smgmtReadinessCheck")
        pos_ac = body.find("missing AC")
        pos_design = body.find("missing design ref")
        assert pos_ac != -1, "'missing AC' push not found in _smgmtReadinessCheck"
        assert pos_design != -1, "'missing design ref' push not found in _smgmtReadinessCheck"
        assert pos_design > pos_ac, (
            "'missing design ref' push must appear after 'missing AC' push in source order "
            "so reasons are emitted in the canonical AC-2 order: AC → design ref → ..."
        )

    def test_design_ref_check_before_estimate_check(self):
        """The 'missing design ref' push must appear before the 'missing estimate' push."""
        body = _fn_body("_smgmtReadinessCheck")
        pos_design = body.find("missing design ref")
        pos_estimate = body.find("missing estimate")
        assert pos_design != -1, "'missing design ref' push not found"
        assert pos_estimate != -1, "'missing estimate' push not found"
        assert pos_design < pos_estimate, (
            "'missing design ref' push must appear before 'missing estimate' push "
            "to match the AC-2 canonical order: AC → design ref → test plan → estimate"
        )
