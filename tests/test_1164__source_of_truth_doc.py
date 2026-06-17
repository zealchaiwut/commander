"""Source-of-truth architecture doc — AC coverage for issue #1164.

Verifies that docs/architecture/1_state-and-source-of-truth.md contains all
required contract elements: four-store table, settled-done definition, pane
metric rules, disk-read prohibition, conflict resolution, and cross-links.
"""
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_DOC = _REPO / "docs" / "architecture" / "1_state-and-source-of-truth.md"
_SPRINT_LIFECYCLE = _REPO / "docs" / "architecture" / "sprint-lifecycle.md"


@pytest.fixture(scope="module")
def doc_text():
    assert _DOC.exists(), f"Missing doc: {_DOC}"
    return _DOC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def lifecycle_text():
    assert _SPRINT_LIFECYCLE.exists(), f"Missing doc: {_SPRINT_LIFECYCLE}"
    return _SPRINT_LIFECYCLE.read_text(encoding="utf-8")


# AC1 — four-store table with required columns
def test_four_store_table_has_required_columns(doc_text):
    """Doc must have a table with Store, Role, Read path, Write path columns."""
    assert "Role" in doc_text, "Table column 'Role' missing"
    assert "Read path" in doc_text, "Table column 'Read path' missing"
    assert "Write path" in doc_text, "Table column 'Write path' missing"


def test_four_store_table_covers_all_stores(doc_text):
    """Table must cover GitHub, SQLite, disk, and Neon."""
    lower = doc_text.lower()
    assert "github" in lower, "GitHub store missing from doc"
    assert "sqlite" in lower, "SQLite store missing from doc"
    assert "disk" in lower, "Disk store missing from doc"
    assert "neon" in lower, "Neon store missing from doc"


# AC2 — canonical settled-done count definition with helper name
def test_settled_done_definition_present(doc_text):
    """Doc must state the canonical settled-done count definition."""
    assert "settled" in doc_text.lower(), "No mention of 'settled' count in doc"
    assert "_settled_done_from_columns" in doc_text, \
        "Canonical helper name '_settled_done_from_columns' not named in doc"


# AC3 — pane metric rules: donut = completed, others = settled
def test_donut_pane_uses_completed_metric(doc_text):
    """Doc must state donut pane uses 'completed' metric."""
    assert "donut" in doc_text.lower(), "No mention of donut pane in doc"
    assert "completed" in doc_text, "Doc does not mention 'completed' metric for donut pane"


def test_other_panes_use_settled_metric(doc_text):
    """Doc must state non-donut panes use 'settled' metric."""
    lower = doc_text.lower()
    assert "settled" in lower, "No mention of 'settled' metric for other panes"


# AC4 — explicit "do not read disk at render" rule
def test_do_not_read_disk_at_render_rule(doc_text):
    """Doc must contain an explicit 'do not read disk at render' rule."""
    lower = doc_text.lower()
    assert "do not read disk" in lower or "never read disk at render" in lower or \
           "disk" in lower and "render" in lower and "not" in lower, \
        "Explicit 'do not read disk at render' rule missing"
    # More precise check — the phrase or equivalent must appear
    assert any(phrase in lower for phrase in [
        "do not read disk at render",
        "never read disk at render",
        "disk is write-once",
        "disk: write-once",
    ]), "Disk write-once / no-render-read rule not stated explicitly"


# AC5 — conflict resolution rule
def test_conflict_resolution_rule_present(doc_text):
    """Doc must state which store wins when stores disagree."""
    lower = doc_text.lower()
    assert "github" in lower and ("authoritative" in lower or "wins" in lower or "authority" in lower), \
        "Doc must state GitHub is authoritative for state"
    assert "sqlite" in lower and ("metric" in lower or "render" in lower), \
        "Doc must state SQLite is the read path for metrics at render"


# AC6 — cross-links from sprint-lifecycle or dashboard architecture doc
def test_cross_link_exists_from_sprint_lifecycle(lifecycle_text):
    """sprint-lifecycle.md must link to 1_state-and-source-of-truth.md."""
    assert "1_state-and-source-of-truth" in lifecycle_text or \
           "source-of-truth" in lifecycle_text.lower(), \
        "sprint-lifecycle.md has no link to 1_state-and-source-of-truth.md"


# AC7 — no existing content removed without replacement
def test_existing_sections_preserved(doc_text):
    """The four original sections (1.1–1.4) must still be present."""
    assert "## 1.1" in doc_text, "Section 1.1 removed"
    assert "## 1.2" in doc_text, "Section 1.2 removed"
    assert "## 1.3" in doc_text, "Section 1.3 removed"
    assert "## 1.4" in doc_text, "Section 1.4 removed"
