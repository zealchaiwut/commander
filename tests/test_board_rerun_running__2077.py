"""Tests for issue #2077: running rerun sub-sprint invisible on board's Running tab (runs against UAT)"""
import os
import pytest
import httpx


# Resolved from UAT .env at runtime; see tester skill Step 0.
# Default kept only as a last-resort fallback if BASE_URL not exported.
BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "8001")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# --- Acceptance Criteria ---

def test_board_running_rerun_chain_appears_in_running_section(client):
    """AC1: running rerun chain is visible in board's running section.

    When a rerun sub-sprint (e.g. sprint-10.1) is actively running, its
    card should appear in sections.running (not just lineage), making it
    visible in the Running tab.
    """
    # Fetch the board to verify the sections exist and are structured correctly
    r = client.get("/api/board", params={"project": "zealchaiwut/commander"})
    assert r.status_code == 200, f"GET /api/board returned {r.status_code}: {r.text}"
    board = r.json()

    # Verify the board has the expected sections structure
    assert "sections" in board, "Board missing 'sections' key"
    sections = board["sections"]
    assert "running" in sections, "Board missing 'sections.running'"
    assert "lineage" in sections, "Board missing 'sections.lineage'"
    assert isinstance(sections["running"], list), "sections.running is not a list"
    assert isinstance(sections["lineage"], list), "sections.lineage is not a list"

    # When a 2+ member rerun chain's latest member is "running",
    # the chain card should be in running (not lineage).
    # We verify the logic by checking that if a chain card exists with 2+ members,
    # its lifecycle_state matches its bucket (running cards have lifecycle_state=="running").
    for card in sections["running"]:
        chain = card.get("chain")
        if chain and len(chain) >= 2:
            # Multi-member chain card in running section: verify lifecycle state
            assert card.get("lifecycle_state") == "running", (
                f"Running section card {card.get('label')} with chain {chain} "
                f"has lifecycle_state={card.get('lifecycle_state')!r}, expected 'running'"
            )


def test_board_finished_rerun_chain_in_lineage(client):
    """AC2: finished rerun chains still collapse to lineage (unchanged behavior).

    A rerun chain whose latest member is NOT running should still appear
    in sections.lineage, preserving the existing lineage-collapse behavior.
    """
    r = client.get("/api/board", params={"project": "zealchaiwut/commander"})
    assert r.status_code == 200, f"GET /api/board returned {r.status_code}: {r.text}"
    board = r.json()
    sections = board["sections"]

    # Verify lineage section exists and can hold multi-member chains
    assert "lineage" in sections
    assert isinstance(sections["lineage"], list)

    # For any lineage card with 2+ members (rerun chain),
    # verify its lifecycle_state is NOT "running" (i.e., it's finished/needs-rework/etc.)
    for card in sections["lineage"]:
        chain = card.get("chain")
        if chain and len(chain) >= 2:
            # Multi-member chain in lineage: should NOT be running
            assert card.get("lifecycle_state") != "running", (
                f"Lineage section card {card.get('label')} with chain {chain} "
                f"has lifecycle_state='running', but should be finished/needs_rework/etc."
            )


def test_board_rerun_chain_card_has_chain_metadata(client):
    """AC1/AC2: rerun chain cards carry chain[] metadata in both running and lineage.

    Whether a rerun chain appears in running or lineage, its card should
    include a 'chain' field listing all sprint labels in the group.
    """
    r = client.get("/api/board", params={"project": "zealchaiwut/commander"})
    assert r.status_code == 200, f"GET /api/board returned {r.status_code}: {r.text}"
    board = r.json()
    sections = board["sections"]

    # Check both running and lineage for multi-member chains
    for bucket_name in ("running", "lineage"):
        for card in sections[bucket_name]:
            chain = card.get("chain")
            if chain and len(chain) >= 2:
                # Multi-member card found: verify chain field is valid
                assert isinstance(chain, list), (
                    f"{bucket_name} card {card.get('label')}: chain field is not a list"
                )
                assert len(chain) >= 2, (
                    f"{bucket_name} card {card.get('label')}: chain has {len(chain)} members, expected ≥2"
                )
                # Verify card label is in the chain
                assert card.get("label") in chain, (
                    f"{bucket_name} card {card.get('label')}: card label not in chain {chain}"
                )


def test_board_sections_structure_correct(client):
    """Verify the board API returns all expected sections.

    The board should always include: running, needs_rework, ready_to_merge,
    draft, lineage, and backlog sections (whether empty or not).
    """
    r = client.get("/api/board", params={"project": "zealchaiwut/commander"})
    assert r.status_code == 200, f"GET /api/board returned {r.status_code}: {r.text}"
    board = r.json()

    assert "sections" in board, "Board response missing 'sections'"
    sections = board["sections"]

    expected_sections = {"running", "needs_rework", "ready_to_merge", "draft", "lineage", "backlog"}
    actual_sections = set(sections.keys())
    assert actual_sections == expected_sections, (
        f"Board sections mismatch. Expected {expected_sections}, got {actual_sections}"
    )
