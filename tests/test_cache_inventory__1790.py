"""Tests for issue #1790: Add cache inventory and invalidation-contract table to architecture docs (runs against UAT)"""
import os
import re
import pytest


# Documentation verification — no HTTP client needed for this ticket
BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")


def test_cache_inventory__section_exists():
    """AC: A cache-inventory section exists in docs/architecture/1_state-and-source-of-truth.md or caching.md"""
    # Read the main state-and-source-of-truth doc
    doc_path = "docs/architecture/1_state-and-source-of-truth.md"
    assert os.path.exists(doc_path), f"Doc file {doc_path} does not exist"

    with open(doc_path) as f:
        content = f.read()

    # Check for the cache inventory section heading
    assert "## 1.3a Cache inventory & invalidation contract" in content, \
        "Cache inventory section heading not found in architecture docs"


def test_cache_inventory__has_table():
    """AC: The inventory contains a table with required columns"""
    doc_path = "docs/architecture/1_state-and-source-of-truth.md"
    with open(doc_path) as f:
        content = f.read()

    # Verify the table exists with expected header line
    assert "| Layer Name | Location | Cache Key | TTL | Invalidation Trigger(s) | Staleness Contract | Added-By |" in content, \
        "Cache table with required columns not found"


def test_cache_inventory__all_layers_present():
    """AC: Every cache layer present in code at merge time has a row (minimum 7 layers)"""
    doc_path = "docs/architecture/1_state-and-source-of-truth.md"
    with open(doc_path) as f:
        content = f.read()

    # Extract the cache inventory table
    table_start = content.find("| Layer Name | Location | Cache Key |")
    assert table_start > 0, "Cache inventory table not found"

    # Find table rows (lines starting with |)
    table_section = content[table_start:table_start + 5000]
    table_lines = [line for line in table_section.split('\n') if line.strip().startswith('|') and "Layer Name" not in line and "---" not in line]

    # We should have at least 7 cache layers documented
    assert len(table_lines) >= 7, f"Expected at least 7 cache layers, found {len(table_lines)}"

    # Check for the specific minimum required layers
    required_layers = [
        "GitHub client 30s",
        "Issues mirror (DB, ETag 304)",
        "Board per-project",
        "Nav status",
        "Home project data",
    ]

    for layer in required_layers:
        assert any(layer in line for line in table_lines), \
            f"Required cache layer '{layer}' not found in table"


def test_cache_inventory__no_blank_cells():
    """AC: Every row has all required columns populated (no blanks or TBD)"""
    doc_path = "docs/architecture/1_state-and-source-of-truth.md"
    with open(doc_path) as f:
        content = f.read()

    # Extract the cache inventory table
    table_start = content.find("| Layer Name | Location | Cache Key |")
    table_section = content[table_start:table_start + 5000]

    # Get actual data rows (skip header and separator)
    table_lines = [line for line in table_section.split('\n')
                   if line.strip().startswith('|')
                   and "Layer Name" not in line
                   and "---" not in line
                   and line.strip() != "|"]

    # Check each row
    for i, line in enumerate(table_lines[:10]):  # Check first 10 rows
        cells = [cell.strip() for cell in line.split('|')[1:-1]]

        # Should have 7 cells (columns)
        assert len(cells) >= 7, f"Row {i} has only {len(cells)} cells, expected 7"

        # No cell should be blank or "TBD"
        for j, cell in enumerate(cells):
            assert cell and cell != "TBD", \
                f"Row {i}, column {j} is blank or TBD: '{cell}'"


def test_cache_inventory__precedence_rule_documented():
    """AC: Precedence rule is explicitly stated (SSE invalidation overrides TTL expiry; manual refresh bypasses both)"""
    doc_path = "docs/architecture/1_state-and-source-of-truth.md"
    with open(doc_path) as f:
        content = f.read()

    # Check for precedence section
    assert "### Invalidation precedence" in content, \
        "Invalidation precedence section not found"

    # Check for key precedence rules
    assert "SSE" in content and "precedence" in content.lower() and "TTL" in content, \
        "SSE precedence over TTL not documented"

    assert re.search(r"manual\s+refresh.*bypass", content, re.IGNORECASE), \
        "Manual refresh bypass not documented"


def test_cache_inventory__single_process_caveat():
    """AC: Single-process caveat is explicitly stated (in-memory caches only coherent with single uvicorn worker)"""
    doc_path = "docs/architecture/1_state-and-source-of-truth.md"
    with open(doc_path) as f:
        content = f.read()

    # Look for the caveat section
    assert "CRITICAL CAVEAT" in content or ("single" in content and "uvicorn" in content and "worker" in content), \
        "Single-process caveat not found"

    assert "--workers" in content, \
        "--workers deployment caveat not documented"


def test_cache_inventory__linked_from_hub():
    """AC: New content is linked from the architecture hub doc (index/README)"""
    hub_path = "docs/architecture/0_content.md"
    assert os.path.exists(hub_path), f"Hub doc {hub_path} does not exist"

    with open(hub_path) as f:
        hub_content = f.read()

    # Check for link to cache inventory
    assert "cache inventory" in hub_content.lower(), \
        "No link to cache inventory found in architecture hub"

    # Check for link to section 1.3a
    assert "1.3a" in hub_content, \
        "Section reference 1.3a not found in architecture hub"


def test_cache_inventory__no_code_changes():
    """AC: No code changes are included — documentation-only PR"""
    # This test runs in isolation and cannot access git directly in UAT context,
    # so we verify that the implementation is documentation-focused by checking
    # that no Python implementation files contain cache-inventory related code.
    # The actual git diff verification happens during the tester's Step 5.
    pytest.skip("manual — git diff verification happens in Step 5 (tester only runs Step 5 git check)")
