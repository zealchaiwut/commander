"""Tests for issue #1298: Follow-up resolve disk-read contradiction.

Consolidates tests from test_disk_read_contradiction__1298.py and
test_1298_source_of_truth_doc_17.py (see issue #1338).

Verifies that docs/architecture/1_state-and-source-of-truth.md §1.7 is
internally consistent and aligns with shipped behavior from #1161.
"""
import re
import pathlib

DOC_PATH = (
    pathlib.Path(__file__).parent.parent
    / "docs/architecture/1_state-and-source-of-truth.md"
)


def _read_section_1_7():
    """Extract §1.7 text (from '## 1.7' to next '## ' heading or EOF)."""
    text = DOC_PATH.read_text()
    match = re.search(r"## 1\.7.*?(?=\n## |\Z)", text, re.DOTALL)
    assert match, "§1.7 heading not found"
    return match.group(0), text


def test_disk_read_contradiction__lazy_ingest_phrase_removed():
    """AC1: §1.7 no longer describes a lazy-ingest-on-read path."""
    section, text = _read_section_1_7()

    assert "trigger a lazy ingest" not in text, (
        "§1.7 still mentions 'trigger a lazy ingest' — contradiction not resolved"
    )
    # ingest_sprint_run_artifact is allowed only if scoped to end-of-run, not HTTP handlers
    if "ingest_sprint_run_artifact" in section:
        assert "never in response to HTTP" in section or "end-of-run only" in section, (
            "§1.7 references ingest_sprint_run_artifact without scoping it to end-of-run"
        )


def test_disk_read_contradiction__no_disk_reads_rule_present():
    """AC2: The 'Zero disk reads in HTTP handlers' rule is present and not contradicted."""
    section, _ = _read_section_1_7()

    assert "Zero disk reads" in section, (
        "Rule 'Zero disk reads' not found in section 1.7"
    )
    for phrase in ("trigger a lazy ingest", "fallback to disk"):
        assert phrase not in section.lower(), (
            f"§1.7 contains '{phrase}' which contradicts the Zero-disk-reads rule"
        )


def test_disk_read_contradiction__404_or_no_data_documented():
    """AC3: §1.7 explicitly states that a missing row results in 404/no_data."""
    section, _ = _read_section_1_7()

    assert "404" in section or "no_data" in section, (
        "Section 1.7 does not explicitly mention 404 or no_data response. "
        "Should describe the behavior shipped in #1161."
    )


def test_disk_read_contradiction__no_disk_fallback_described():
    """AC4: No disk-read fallback path is described anywhere in §1.7."""
    section, _ = _read_section_1_7()

    assert "disk-read fallback" not in section.lower(), (
        "§1.7 describes a disk-read fallback path, which contradicts Zero-disk-reads rule"
    )
    assert "fall back to disk" not in section.lower(), (
        "§1.7 describes falling back to disk, which contradicts Zero-disk-reads rule"
    )


def test_disk_read_contradiction__consistency_with_1161():
    """AC5: The updated text is consistent with #1161 (lazy-ingest path removed)."""
    section, _ = _read_section_1_7()

    has_1161_ref = "#1161" in section
    has_no_fallback = "never in response to HTTP requests" in section

    assert has_1161_ref or has_no_fallback, (
        "Section 1.7 should reference #1161 or clearly state that ingestion "
        "never happens in response to HTTP requests."
    )


def test_disk_read_contradiction__no_unrelated_changes():
    """AC6: No other sections modified beyond the minimum needed."""
    _, full_content = _read_section_1_7()

    for heading in ("## 1.1", "## 1.2", "## 1.3", "## 1.4", "## 1.5", "## 1.6"):
        assert heading in full_content, f"Heading '{heading}' unexpectedly removed"

    section_count = full_content.count("## 1.")
    assert section_count >= 7, (
        f"Expected at least 7 numbered sections; found {section_count}. "
        "Unrelated content may have been removed."
    )
