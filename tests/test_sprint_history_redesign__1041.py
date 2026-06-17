"""Tests for issue #1041: Redesign sprint history cards (runs against UAT).

Acceptance Criteria validation: header order, outcome pills, loose-end band,
what-lists (failed/partial), details block, visual hierarchy, color discipline.
"""
import os
import pytest
import httpx


BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


@pytest.fixture
def browser():
    # Browser tests require agent-browser to be installed and available.
    # Placeholder for browser-based UAT steps.
    pytest.skip("Browser tests require agent-browser runner — use Step 6 for UAT verification")


# --- Acceptance Criteria Tests ---

def test_sprint_history_redesign__header_and_section_order(client):
    """AC1: Sections render in exact order: Header → Loose-end → What-list → Details (collapsed)"""
    # This is a visual/DOM assertion that requires browser testing in Step 6.
    pytest.skip("Manual — visual ordering verified in Step 6 browser test")


def test_sprint_history_redesign__header_contains_pill_and_action(client):
    """AC2: Header contains: sprint name, outcome pill, compact meta (date/count), recovery action button"""
    pytest.skip("Visual pill rendering verified in Step 6 browser test")


def test_sprint_history_redesign__outcome_pill_colors(client):
    """AC3: Outcome pills are neutral: Partial=grey, Failed=red, Complete=green"""
    pytest.skip("Color assertions verified in Step 6 design-contract gate")


def test_sprint_history_redesign__single_amber_band(client):
    """AC4: Exactly one amber band per card, showing single actionable loose end"""
    pytest.skip("Amber band visual assertions verified in Step 6")


def test_sprint_history_redesign__failed_sprint_why_list(client):
    """AC5: Failed sprints show single 'Why it failed' list; no duplication in other sections"""
    pytest.skip("Failed sprint list deduplication verified in Step 6 browser test")


def test_sprint_history_redesign__partial_sprint_unfinished_list(client):
    """AC6: Partial sprints show single 'Unfinished N of M' list with inline actions"""
    pytest.skip("Partial sprint list rendering verified in Step 6 browser test")


def test_sprint_history_redesign__complete_sprint_collapsed(client):
    """AC7: Complete sprint cards show no loose-end band, no what-list, collapsed Details"""
    pytest.skip("Complete sprint collapsed state verified in Step 6 browser test")


def test_sprint_history_redesign__details_block_collapsed_by_default(client):
    """AC8: Details block is collapsed by default; contains metrics, agent split, timeline, checks"""
    pytest.skip("Details block expansion state verified in Step 6 browser test")


def test_sprint_history_redesign__reconciliation_checks_grey_icons(client):
    """AC9: Reconciliation checks are small grey icons; never green boxes outside Details"""
    pytest.skip("Reconciliation icon rendering verified in Step 6 design-contract")


def test_sprint_history_redesign__metrics_desaturated_when_collapsed(client):
    """AC10: Metrics chips, timeline, agent-time split are desaturated (grey) when collapsed"""
    pytest.skip("Metrics desaturation verified in Step 6 design-contract gate")


def test_sprint_history_redesign__delete_action_quiet_icon(client):
    """AC11: Delete action is quiet icon (no label, no destructive color) in header or footer"""
    pytest.skip("Delete icon rendering verified in Step 6 browser test")


def test_sprint_history_redesign__secondary_actions_subordinate(client):
    """AC12: Secondary actions (view log, copy ID) are visually subordinate to primary recovery action"""
    pytest.skip("Action visual hierarchy verified in Step 6 browser test")


def test_sprint_history_redesign__color_discipline_consistency(client):
    """AC13: Color discipline applies consistently across Partial, Failed, Complete variants"""
    pytest.skip("Color discipline verified in Step 6 design-contract gate")


def test_sprint_history_redesign__no_duplicate_issue_keys(client):
    """AC14: No issue key appears more than once within a single card at any zoom level"""
    pytest.skip("Issue deduplication verified in Step 6 browser test")


def test_sprint_history_redesign__attachment_mock_match(client):
    """AC15 (See attachment): Layout and visual structure match the provided mock HTML exactly"""
    pytest.skip("Mock comparison verified in Step 6 design-contract gate")
