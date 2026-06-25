"""Tests for issue #1539 — Board readiness check must align with canonical parse_ticket_spec.

_smgmtReadinessCheck in board-render.js was using its own regex patterns that
diverged from the canonical Python parser in ticket_spec.py:
  - JS accepted bare `## AC` as an AC heading; Python (parse_ticket_spec) does not
  - JS only matched `## Design Refs`; Python also accepts `## Design References/Reference`
  - JS accepted bare `## Test Steps`; Python only accepts `## UAT Test Steps` / `## Test Plan`

Fix: align the JS heading regexes with _SECTION_PATTERNS in ticket_spec.py.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
BOARD_JS = (
    REPO_ROOT / "apps/dashboard/static/src/sprint-board/board-render.js"
).read_text(encoding="utf-8")
TICKET_SPEC_PY = (
    REPO_ROOT / "services/sprint_manager/ticket_spec.py"
).read_text(encoding="utf-8")


def _fn_body(name: str, src: str = BOARD_JS) -> str:
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


# ── Canonical Python patterns from ticket_spec._SECTION_PATTERNS ─────────────
# These mirror what the JS must use after the fix.

AC_PATTERN = re.compile(
    r"^#{1,6}\s+(acceptance\s+criteria|acceptance)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
DESIGN_REF_PATTERN = re.compile(
    r"^#{1,6}\s+(design\s+references?|design\s+refs?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
TEST_PLAN_PATTERN = re.compile(
    r"^#{1,6}\s+(uat\s+test\s+steps|test\s+plan)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


# =============================================================================
# Structural tests — these FAIL before the fix because the old patterns are present
# =============================================================================


class TestStructural_ACNoBareACAlternative:
    """The AC regex must not accept bare `## AC`."""

    def test_no_pipe_ac_alternative_in_fn_body(self):
        """_smgmtReadinessCheck must not contain `|ac)` — the bare AC alternative."""
        body = _fn_body("_smgmtReadinessCheck")
        assert "|ac)" not in body.lower(), (
            "_smgmtReadinessCheck must not use '|ac)' in the acceptance-criteria regex. "
            "The canonical parse_ticket_spec only accepts 'acceptance criteria' and "
            "'acceptance' — bare 'ac' is an over-match that diverges from the server parser."
        )


class TestStructural_DesignRefCoversReferences:
    """The design-ref regex must cover `## Design References`, not just `## Design Refs`."""

    def test_fn_body_contains_references_alternative(self):
        """_smgmtReadinessCheck must mention 'references' to cover the Design References variant."""
        body = _fn_body("_smgmtReadinessCheck")
        has_references = "references" in body.lower()
        assert has_references, (
            "_smgmtReadinessCheck must cover '## Design References' in addition to "
            "'## Design Refs'. The canonical parse_ticket_spec pattern is "
            r"design\s+references? | design\s+refs?, but the JS only had 'design refs'."
        )


class TestStructural_TestPlanNoBareTestSteps:
    """The test-plan regex must not accept bare `## Test Steps`."""

    def test_no_bare_test_steps_alternative_in_fn_body(self):
        """_smgmtReadinessCheck must not contain `|test steps)` bare alternative."""
        body = _fn_body("_smgmtReadinessCheck")
        assert "|test steps)" not in body.lower(), (
            "_smgmtReadinessCheck must not use '|test steps)' as a standalone alternative. "
            "The canonical parse_ticket_spec only accepts 'uat test steps' and 'test plan'. "
            "Bare 'test steps' is an over-match not present in the server parser."
        )


# =============================================================================
# Behavioural tests — document the canonical correct behaviour
# These use Python regexes mirroring the aligned JS patterns
# =============================================================================


class TestBehaviouralAC:
    def test_canonical_ac_accepts_acceptance_criteria(self):
        assert AC_PATTERN.search("## Acceptance Criteria\n\n- [ ] does A"), (
            "'## Acceptance Criteria' must be accepted"
        )

    def test_canonical_ac_accepts_acceptance_alone(self):
        assert AC_PATTERN.search("## Acceptance\n\n- [ ] does A"), (
            "'## Acceptance' alone must be accepted (canonical pattern includes it)"
        )

    def test_canonical_ac_rejects_bare_ac_heading(self):
        assert not AC_PATTERN.search("## AC\n\n- [ ] does A"), (
            "Bare '## AC' must NOT be treated as an acceptance-criteria heading"
        )

    def test_canonical_ac_rejects_unrelated_heading(self):
        assert not AC_PATTERN.search("## What & Why\n\nSome content"), (
            "'## What & Why' must not match the acceptance-criteria pattern"
        )


class TestBehaviouralDesignRef:
    def test_canonical_design_ref_accepts_design_references(self):
        assert DESIGN_REF_PATTERN.search("## Design References\n\n- Ref A"), (
            "'## Design References' must be accepted"
        )

    def test_canonical_design_ref_accepts_design_reference_singular(self):
        assert DESIGN_REF_PATTERN.search("## Design Reference\n\n- Ref A"), (
            "'## Design Reference' (singular) must be accepted"
        )

    def test_canonical_design_ref_accepts_design_refs(self):
        assert DESIGN_REF_PATTERN.search("## Design Refs\n\n- Ref A"), (
            "'## Design Refs' must be accepted (regression guard from #1538)"
        )

    def test_canonical_design_ref_accepts_design_ref_singular(self):
        assert DESIGN_REF_PATTERN.search("## Design Ref\n\n- Ref A"), (
            "'## Design Ref' (singular) must be accepted"
        )

    def test_canonical_design_ref_rejects_unrelated_heading(self):
        assert not DESIGN_REF_PATTERN.search("## What & Why\n\nContent"), (
            "'## What & Why' must not match the design-ref pattern"
        )


class TestBehaviouralTestPlan:
    def test_canonical_test_plan_accepts_uat_test_steps(self):
        assert TEST_PLAN_PATTERN.search("## UAT Test Steps\n\n1. Do X."), (
            "'## UAT Test Steps' must be accepted"
        )

    def test_canonical_test_plan_accepts_test_plan(self):
        assert TEST_PLAN_PATTERN.search("## Test Plan\n\n1. Do X."), (
            "'## Test Plan' must be accepted"
        )

    def test_canonical_test_plan_rejects_bare_test_steps(self):
        assert not TEST_PLAN_PATTERN.search("## Test Steps\n\n1. Do X."), (
            "Bare '## Test Steps' must NOT be treated as a test-plan heading — "
            "canonical parse_ticket_spec does not accept it"
        )

    def test_canonical_test_plan_rejects_unrelated_heading(self):
        assert not TEST_PLAN_PATTERN.search("## What & Why\n\nContent"), (
            "'## What & Why' must not match the test-plan pattern"
        )


# =============================================================================
# Canonical source integrity — ticket_spec.py defines the patterns JS must follow
# =============================================================================


class TestCanonicalSourceIntegrity:
    def test_ticket_spec_defines_acceptance_criteria_key(self):
        assert "acceptance_criteria" in TICKET_SPEC_PY, (
            "ticket_spec.py must define 'acceptance_criteria' patterns — "
            "this is the canonical source that JS must align with"
        )

    def test_ticket_spec_defines_design_refs_key(self):
        assert "design_refs" in TICKET_SPEC_PY, (
            "ticket_spec.py must define 'design_refs' patterns — "
            "this is the canonical source that JS must align with"
        )

    def test_ticket_spec_defines_test_plan_key(self):
        assert "test_plan" in TICKET_SPEC_PY, (
            "ticket_spec.py must define 'test_plan' patterns — "
            "this is the canonical source that JS must align with"
        )

    def test_ticket_spec_does_not_have_bare_ac_pattern(self):
        """ticket_spec.py must not list bare 'ac' as an acceptance_criteria pattern."""
        # The _SECTION_PATTERNS dict value for acceptance_criteria should not
        # contain a pattern that fullmatches the string "AC"
        assert '"ac"' not in TICKET_SPEC_PY and "'ac'" not in TICKET_SPEC_PY, (
            "ticket_spec.py must not accept bare 'ac' as an acceptance_criteria pattern — "
            "neither should the JS"
        )
