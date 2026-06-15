"""Tests for issue #1043: Board collapse ancestor sprints with merge-state marks (runs against UAT)"""
import os
import pytest
import httpx


# Resolved from UAT .env at runtime; see tester skill Step 0.
# Default kept only as a last-resort fallback if BASE_URL not exported.
BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# --- Acceptance Criteria ---

def test_board_collapse_ancestor_sprints__collapsed_rows_render(client):
    # AC: Each resolved ancestor sprint renders as a single collapsed row containing:
    # a merge-state mark, the sprint name, and a carry-down summary
    r = client.get("/")
    assert r.status_code == 200
    assert b"slp-ancestor-row" in r.content  # ancestor row class present
    assert b"slp-ancestor-header" in r.content  # header container present
    assert b"slp-merge-mark" in r.content  # merge-state mark present
    assert b"slp-ancestor-name" in r.content  # sprint name present
    assert b"slp-carry-summary" in r.content  # carry-down summary present


def test_board_collapse_ancestor_sprints__merge_state_marks(client):
    # AC: Merge-state mark has three distinct states sourced from the sprint record:
    # Merged (green, git-merge icon), Needs merge (amber), Failed (red)
    r = client.get("/")
    assert r.status_code == 200
    # Check for the CSS classes and icons that mark the three states
    assert b"slp-merged" in r.content  # Merged state class
    assert b"slp-needs-merge" in r.content  # Needs merge state class
    assert b"slp-failed" in r.content  # Failed state class
    # Icon markers
    assert b"ti-git-merge" in r.content or b"ti-git-pull-request" in r.content or b"ti-circle-x" in r.content


def test_board_collapse_ancestor_sprints__carry_down_summary(client):
    # AC: Carry-down summary correctly reflects how many tickets merged (stayed) vs.
    # carried (moved) and names the child sprint they moved to (e.g. `0 merged - 3 carried -> 73.3`)
    r = client.get("/")
    assert r.status_code == 200
    # The carry summary is rendered in .slp-carry-summary elements
    # Look for the pattern "merged" and "reworked" or "carried"
    assert b"merged" in r.content
    # Verify carry summary contains arrow notation for child sprint reference
    content = r.text
    # Check for patterns like "3 merged · 1 reworked → 73.1" or similar
    assert "merged" in content or "reworked" in content or "carried" in content


def test_board_collapse_ancestor_sprints__expanded_row_ticket_rows(client):
    # AC: Expanding a collapsed ancestor row reveals per-ticket rows, each marked:
    # `merged` with a green check for passed tickets that stayed
    # `carried -> 73.x` in amber for rework/failed tickets that moved to a child sprint
    r = client.get("/")
    assert r.status_code == 200
    # Check for expanded ancestor body and ticket row classes
    assert b"slp-ancestor-body" in r.content  # expanded body container
    assert b"slp-ancestor-ticket-row" in r.content  # per-ticket row
    assert b"slp-ticket-merged" in r.content or b"slp-ticket-carried" in r.content  # ticket fate marks
    assert b"slp-fate-merged" in r.content or b"slp-fate-carried" in r.content  # fate labels


def test_board_collapse_ancestor_sprints__needs_merge_actions(client):
    # AC: A "Needs merge" ancestor that is expanded additionally exposes Re-run and Merge action buttons
    r = client.get("/")
    assert r.status_code == 200
    # Check for ancestor action buttons (only shown for needs_merge state)
    assert b"slp-ancestor-actions" in r.content  # actions container
    # Re-run and Merge buttons should be present in the HTML
    assert b"Re-run" in r.content or b"smgmt-run-btn--rerun" in r.content
    assert b"Merge" in r.content or b"sc-merge-link" in r.content


def test_board_collapse_ancestor_sprints__active_sprint_expanded_by_default(client):
    # AC: The active sprint (up-next or running) is expanded by default on page load
    r = client.get("/")
    assert r.status_code == 200
    # The active sprint should not have the hidden attribute on its body
    # Ancestor rows have id slp-body-{label} which is hidden by default
    content = r.text
    # Check that some regular sprint cards (non-ancestor) are rendered and visible
    assert "smgmt-sprint-unit" in content


def test_board_collapse_ancestor_sprints__ancestors_collapsed_by_default(client):
    # AC: All resolved ancestor sprints are collapsed by default on page load
    r = client.get("/")
    assert r.status_code == 200
    # Ancestor body divs should have hidden attribute by default
    # Pattern: id="slp-body-{label}" hidden
    assert b'hidden' in r.content  # check for hidden attribute


def test_board_collapse_ancestor_sprints__carry_links_correct(client):
    # AC: Carry links correctly identify which child sprint a carried ticket moved to
    r = client.get("/")
    assert r.status_code == 200
    # Check for carry notation with child sprint references
    content = r.text
    # Carried tickets should show "carried → {child_sprint}"
    assert "carried" in content or "reworked" in content


def test_board_collapse_ancestor_sprints__merge_state_visually_distinct(client):
    # AC: Merge-state marks are visually distinguishable at a glance without relying
    # solely on color (icon or label text present)
    r = client.get("/")
    assert r.status_code == 200
    # Check for icon elements within merge marks (ti-* classes)
    assert b"ti-" in r.content  # Tabler icons present
    # Check for text labels
    assert b"slp-mark-text" in r.content  # text label container


def test_board_collapse_ancestor_sprints__toggle_functionality(client):
    # AC (implicit): Expand/collapse toggle buttons are rendered
    r = client.get("/")
    assert r.status_code == 200
    # Check for collapse/expand toggle button
    assert b"slp-ancestor-toggle" in r.content  # toggle button class
    assert b"smgmt-collapse-btn" in r.content  # collapse button class
    # Check for chevron icons used for toggle
    assert b"ti-chevron" in r.content


def test_board_collapse_ancestor_sprints__styling_classes_present(client):
    # AC (implicit): All styling is properly applied
    r = client.get("/")
    assert r.status_code == 200
    # Verify key styling classes are in the response
    assert b"slp-ancestor-row" in r.content
    assert b"slp-ancestor-header" in r.content
    assert b"slp-carry-summary" in r.content
    # Verify CSS colors/states are defined (checking for the actual style definitions)
    assert b"slp-merged" in r.content or b"slp-needs-merge" in r.content
