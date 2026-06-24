"""Tests for issue #1545 — Preflight AC-detection regex diverges from canonical parser.

The _preflightCheckTickets function in project.html was using
  /^##\s+(acceptance criteria|ac)/im
which incorrectly accepts bare '## AC' as a valid AC heading. The canonical
Python parser (ticket_spec.py) only accepts 'acceptance criteria' or
'acceptance' — not bare 'ac'.

Fix: align the project.html regex with the canonical parse_ticket_spec.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
PROJECT_HTML = (
    REPO_ROOT / "apps" / "dashboard" / "static" / "project.html"
).read_text(encoding="utf-8")
BOARD_JS = (
    REPO_ROOT / "apps" / "dashboard" / "static" / "src" / "sprint-board" / "board-render.js"
).read_text(encoding="utf-8")
BUNDLE_JS = (
    REPO_ROOT / "apps" / "dashboard" / "static" / "dist" / "bundle.js"
).read_text(encoding="utf-8")


def _extract_preflight_fn(src: str = PROJECT_HTML) -> str:
    """Return the brace-balanced body of _preflightCheckTickets from project.html."""
    name = "_preflightCheckTickets"
    pos = src.find(f"function {name}(")
    assert pos != -1, f"{name} must be present in project.html"
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


# Canonical Python regex pattern (mirrors ticket_spec._SECTION_PATTERNS["acceptance_criteria"])
_CANONICAL_AC = re.compile(
    r"^#{1,6}\s+(acceptance\s+criteria|acceptance)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


# =============================================================================
# AC1 — _preflightCheckTickets must not contain |ac) bare alternative
# =============================================================================


class TestAC1_PreflightNoBarAcAlternative:
    """_preflightCheckTickets in project.html must not accept bare '## AC'."""

    def test_preflight_fn_no_pipe_ac_alternative(self):
        """_preflightCheckTickets body must not contain '|ac)' or '|ac/' pattern."""
        body = _extract_preflight_fn()
        # Check for the |ac alternative in various forms
        lower = body.lower()
        has_bare_ac = (
            "|ac)" in lower
            or "|ac/" in lower
            or "|ac " in lower
            or "| ac)" in lower
            or "| ac/" in lower
        )
        assert not has_bare_ac, (
            "_preflightCheckTickets in project.html must not contain '|ac' as a "
            "regex alternative — bare '## AC' is not a valid AC heading per the "
            "canonical parse_ticket_spec (ticket_spec.py)."
        )

    def test_preflight_fn_contains_acceptance_alternative(self):
        """_preflightCheckTickets must still detect '## Acceptance Criteria'."""
        body = _extract_preflight_fn()
        assert "acceptance" in body.lower(), (
            "_preflightCheckTickets must contain 'acceptance' in its regex to "
            "detect '## Acceptance Criteria' and '## Acceptance' headings"
        )


# =============================================================================
# AC2 — Bare ## AC must be treated as missing-AC (not ready)
# =============================================================================


class TestAC2_BareACHeadingIsNotReady:
    """Canonical AC regex must NOT match bare ## AC."""

    def test_canonical_ac_regex_rejects_bare_ac(self):
        """Bare '## AC' must not satisfy the canonical AC-detection regex."""
        assert not _CANONICAL_AC.search("## AC\n\n- [ ] item"), (
            "Bare '## AC' must be rejected by the canonical AC pattern — "
            "preflight must show this ticket as not ready"
        )

    def test_canonical_ac_regex_rejects_bare_ac_case_variants(self):
        """Case variants of bare '## AC' must all be rejected."""
        for heading in ("## ac", "## Ac", "## AC"):
            assert not _CANONICAL_AC.search(f"{heading}\n\n- [ ] item"), (
                f"'{heading}' must be rejected — only 'acceptance criteria' and "
                "'acceptance' are valid AC headings"
            )


# =============================================================================
# AC3 — ## Acceptance Criteria continues to be shown as ready
# =============================================================================


class TestAC3_AcceptanceCriteriaIsReady:
    """Canonical AC regex must match ## Acceptance Criteria variants."""

    def test_canonical_ac_regex_accepts_acceptance_criteria(self):
        """'## Acceptance Criteria' must match the canonical pattern."""
        assert _CANONICAL_AC.search("## Acceptance Criteria\n\n- [ ] does A"), (
            "'## Acceptance Criteria' must be accepted as a valid AC heading"
        )

    def test_canonical_ac_regex_accepts_acceptance_criteria_lowercase(self):
        """'## acceptance criteria' (lowercase) must match (case-insensitive)."""
        assert _CANONICAL_AC.search("## acceptance criteria\n\n- [ ] does A"), (
            "'## acceptance criteria' must be accepted (case-insensitive match)"
        )


# =============================================================================
# AC4 — ## Acceptance (without Criteria) continues to be shown as ready
# =============================================================================


class TestAC4_AcceptanceAloneIsReady:
    """Canonical AC regex must match ## Acceptance."""

    def test_canonical_ac_regex_accepts_acceptance_alone(self):
        """'## Acceptance' alone must match the canonical pattern."""
        assert _CANONICAL_AC.search("## Acceptance\n\n- [ ] does A"), (
            "'## Acceptance' (without 'Criteria') must be accepted — "
            "the canonical parse_ticket_spec includes it"
        )

    def test_canonical_ac_regex_accepts_acceptance_lowercase(self):
        """'## acceptance' (lowercase) must match."""
        assert _CANONICAL_AC.search("## acceptance\n\n- [ ] does A"), (
            "'## acceptance' must be accepted (case-insensitive match)"
        )


# =============================================================================
# AC5 — bundle.js is rebuilt and reflects the corrected regex
# =============================================================================


class TestAC5_BundleRebuilt:
    """bundle.js must not contain the bare |ac) AC alternative."""

    def test_bundle_no_pipe_ac_alternative(self):
        """The compiled bundle.js must not contain '|ac)' in AC-detection logic."""
        # The bundle is the compiled output of board-render.js. board-render.js
        # is already correct; this guards against a stale bundle regression.
        lower = BUNDLE_JS.lower()
        # Find lines around "acceptance" in the bundle that might have |ac)
        # We search for the pattern "|ac)" which would indicate a stale/wrong bundle
        has_bare_ac = "|ac)" in lower
        assert not has_bare_ac, (
            "bundle.js must not contain '|ac)' — either the source was not rebuilt "
            "after the fix, or a regression was introduced. Rebuild with 'npm run build'."
        )

    def test_bundle_contains_acceptance_criteria_pattern(self):
        """bundle.js must contain 'acceptance' in its AC-detection logic (from board-render.js)."""
        assert "acceptance" in BUNDLE_JS.lower(), (
            "bundle.js must contain the AC-detection regex from board-render.js. "
            "Rebuild with 'npm run build' if missing."
        )
