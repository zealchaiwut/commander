"""Tests for issue #1298: Follow-up resolve disk-read contradiction.

This ticket verifies that docs/architecture/1_state-and-source-of-truth.md
section 1.7 is internally consistent and aligns with shipped behavior from #1161.
The tests are primarily inspection-based (reading and asserting on file content).
"""
import os
import pytest


def read_section_1_7():
    """Read section 1.7 from the documentation file."""
    doc_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "docs",
        "architecture",
        "1_state-and-source-of-truth.md"
    )
    with open(doc_path, "r") as f:
        content = f.read()

    # Extract section 1.7 (from "## 1.7" to the next "##" or end of file)
    start = content.find("## 1.7")
    if start == -1:
        raise RuntimeError("Section 1.7 not found in documentation")

    # Find the next section header or end of file
    next_section = content.find("\n## ", start + 1)
    if next_section == -1:
        section = content[start:]
    else:
        section = content[start:next_section]

    return section, content


def test_disk_read_contradiction__lazy_ingest_phrase_removed():
    """AC: Line 113 no longer describes a lazy-ingest-on-read path."""
    section, _ = read_section_1_7()

    # The old phrase that should be removed
    old_phrase = "trigger a lazy ingest"
    assert old_phrase not in section, (
        f"Found forbidden phrase '{old_phrase}' in section 1.7. "
        "This phrase contradicts the 'Zero disk reads' rule."
    )


def test_disk_read_contradiction__no_disk_reads_rule_present():
    """AC: The 'Zero disk reads in HTTP handlers' rule is not contradicted."""
    section, _ = read_section_1_7()

    # The rule should be present
    rule_text = "Zero disk reads"
    assert rule_text in section, (
        f"Rule '{rule_text}' not found in section 1.7"
    )


def test_disk_read_contradiction__404_or_no_data_documented():
    """AC: §1.7 explicitly states that a missing row results in 404/no_data."""
    section, _ = read_section_1_7()

    # The new behavior should be documented
    assert "404" in section or "no_data" in section, (
        "Section 1.7 does not explicitly mention 404 or no_data response. "
        "Should describe the behavior shipped in #1161."
    )


def test_disk_read_contradiction__no_lazy_ingest_on_read():
    """AC: No mention of lazy ingest as the current behavior."""
    section, _ = read_section_1_7()

    # The phrase "trigger a lazy ingest" describes the old (removed) behavior.
    # We should not describe current behavior as doing lazy ingest.
    forbidden_phrase = "trigger a lazy ingest"
    assert forbidden_phrase not in section, (
        f"Found '{forbidden_phrase}' in section 1.7. "
        "This was the old behavior removed in #1161."
    )


def test_disk_read_contradiction__consistency_with_1161():
    """AC: The updated text is consistent with #1161 (lazy-ingest path removed)."""
    section, _ = read_section_1_7()

    # Should mention #1161 or describe the no-fallback path clearly
    has_1161_ref = "#1161" in section
    has_no_fallback = "never in response to HTTP requests" in section

    assert has_1161_ref or has_no_fallback, (
        "Section 1.7 should reference #1161 or clearly state that ingestion "
        "never happens in response to HTTP requests."
    )


def test_disk_read_contradiction__no_unrelated_changes():
    """AC: No other sections modified beyond the minimum needed."""
    _, full_content = read_section_1_7()

    # Rough heuristic: check that the document structure is intact
    section_count = full_content.count("## 1.")
    assert section_count >= 7, (
        f"Expected at least 7 numbered sections; found {section_count}. "
        "Unrelated content may have been removed."
    )
