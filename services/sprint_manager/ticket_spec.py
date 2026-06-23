"""Canonical ticket-spec parser for Commander issue bodies (issue #1485).

Single source of truth for extracting structured data from a GitHub issue body.
All AC-detection logic must go through `parse_ticket_spec` — do not duplicate.
"""
from __future__ import annotations

import re


# Map canonical output key → list of regex patterns that match the heading text.
_SECTION_PATTERNS: dict[str, list[str]] = {
    "acceptance_criteria": [r"acceptance\s+criteria", r"acceptance"],
    "design_refs": [r"design\s+references?", r"design\s+refs?"],
    "test_plan": [r"uat\s+test\s+steps", r"test\s+plan"],
    "out_of_scope": [r"out\s+of\s+scope"],
}

# Sections whose content is a list of items (bullet/checklist lines).
_LIST_SECTIONS = {"acceptance_criteria", "design_refs"}

# Pre-compile a single regex that can detect any known heading.
_ANY_HEADING_RE = re.compile(
    r"^#{1,6}\s+(.+)$",
    re.MULTILINE,
)


def _matches_key(heading_text: str, key: str) -> bool:
    for pattern in _SECTION_PATTERNS[key]:
        if re.fullmatch(pattern, heading_text.strip(), re.IGNORECASE):
            return True
    return False


def _classify_heading(heading_text: str) -> str | None:
    for key in _SECTION_PATTERNS:
        if _matches_key(heading_text, key):
            return key
    return None


def _extract_sections(body: str) -> dict[str, str]:
    """Split body into raw section texts keyed by canonical name."""
    sections: dict[str, str] = {}
    matches = list(_ANY_HEADING_RE.finditer(body))
    for i, m in enumerate(matches):
        key = _classify_heading(m.group(1))
        if key is None:
            continue
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections[key] = body[start:end].strip()
    return sections


def _parse_list(text: str) -> list[str]:
    """Extract item text from bullet or checklist lines."""
    items = []
    for line in text.splitlines():
        stripped = line.strip()
        # Match: "- [ ] text", "- [x] text", "- text", "* text"
        m = re.match(r"^[-*]\s+(?:\[[ xX]\]\s+)?(.+)$", stripped)
        if m:
            items.append(m.group(1).strip())
    return items


def parse_ticket_spec(body: str) -> dict:
    """Parse a GitHub issue body into canonical ticket-spec fields.

    Returns a dict with keys:
      - acceptance_criteria: list[str]
      - design_refs: list[str]
      - test_plan: str
      - out_of_scope: str

    Missing sections return empty values ([] or ""). Never raises.
    """
    sections = _extract_sections(body or "")
    return {
        "acceptance_criteria": _parse_list(sections.get("acceptance_criteria", "")),
        "design_refs": _parse_list(sections.get("design_refs", "")),
        "test_plan": sections.get("test_plan", ""),
        "out_of_scope": sections.get("out_of_scope", ""),
    }
