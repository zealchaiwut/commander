"""Tests for issue #1298 — resolve disk-read contradiction in §1.7 of
docs/architecture/1_state-and-source-of-truth.md."""

import pathlib
import re

DOC_PATH = (
    pathlib.Path(__file__).parent.parent
    / "docs/architecture/1_state-and-source-of-truth.md"
)


def _section_17(text: str) -> str:
    """Extract §1.7 text (from '## 1.7' to next '## ' heading or EOF)."""
    match = re.search(r"## 1\.7.*?(?=\n## |\Z)", text, re.DOTALL)
    assert match, "§1.7 heading not found"
    return match.group(0)


def test_lazy_ingest_phrase_absent():
    """AC1: line 113 no longer describes a lazy-ingest-on-read path."""
    text = DOC_PATH.read_text()
    assert "trigger a lazy ingest" not in text, (
        "§1.7 still mentions 'trigger a lazy ingest' — contradiction not resolved"
    )
    # The section must not describe calling ingest_sprint_run_artifact from HTTP handlers
    section = _section_17(text)
    if "ingest_sprint_run_artifact" in section:
        # Allowed only if explicitly scoped to end-of-run (sprint manager), not HTTP handlers
        assert "never in response to HTTP" in section or "end-of-run only" in section, (
            "§1.7 references ingest_sprint_run_artifact without scoping it to end-of-run"
        )


def test_404_no_data_documented():
    """AC2: §1.7 describes the shipped behavior — readers return 404 or no_data."""
    section = _section_17(DOC_PATH.read_text())
    has_404 = "404" in section
    has_no_data = "no_data" in section
    assert has_404 or has_no_data, (
        "§1.7 does not mention 404 or no_data — shipped behavior not documented"
    )


def test_zero_disk_reads_rule_not_contradicted():
    """AC3: the 'Zero disk reads' rule is not contradicted by any statement in §1.7."""
    section = _section_17(DOC_PATH.read_text())
    # The rule must still be present
    assert "Zero disk reads" in section, "'Zero disk reads' rule missing from §1.7"
    # No statement should describe reading a disk artifact from an HTTP handler
    # ("read disk" itself appears in the prohibition — we check permissive phrases only)
    for phrase in ("trigger a lazy ingest", "fallback to disk"):
        assert phrase not in section.lower(), (
            f"§1.7 contains '{phrase}' which contradicts the Zero-disk-reads rule"
        )


def test_no_disk_fallback_described():
    """AC4: no disk-read fallback path is described anywhere in §1.7."""
    section = _section_17(DOC_PATH.read_text())
    assert "disk-read fallback" not in section.lower()
    assert "fall back to disk" not in section.lower()


def test_other_sections_unchanged():
    """AC5: only §1.7 is modified — all other section headings still present."""
    text = DOC_PATH.read_text()
    for heading in ("## 1.1", "## 1.2", "## 1.3", "## 1.4", "## 1.5", "## 1.6"):
        assert heading in text, f"Heading '{heading}' unexpectedly removed"
