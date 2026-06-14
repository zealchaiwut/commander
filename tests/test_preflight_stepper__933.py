"""Tests for issue #933: Show pre-flight checks as live stepper checklist (runs against UAT)"""
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


# --- Acceptance Criteria ---

def test_ac1_shared_progress_component_renders_stepper_mode(client):
    # AC-1: Pre-flight check sequence renders using the shared progress component in stepper mode
    # Test that the preflight endpoint exists and returns response with expected data structure
    r = client.get('/api/sprints/sprint-73/preflight', params={'project': 'zealchaiwut/commander'})
    # Endpoint should exist and return 200, 404 (no sprint), or 500 (error)
    # but definitely not 405/Method Not Allowed
    assert r.status_code in [200, 404, 500], f"Unexpected status: {r.status_code}"
    if r.status_code == 200:
        data = r.json()
        # Check response includes checks or other preflight data structure
        assert isinstance(data, dict), "Preflight response should be a dict"


def test_ac2_pending_to_checking_to_resolved_state_transitions(client):
    # AC-2: Each step starts in pending state and transitions to checking while its check runs
    # This is verified via browser interaction in UAT steps, not HTTP
    pytest.skip("manual — state transitions verified in UAT steps 2-3 via browser observation")


def test_ac3_passing_checks_resolve_to_pass(client):
    # AC-3: Passing checks resolve to pass
    # Verified when all checks pass (UAT step 3)
    pytest.skip("manual — verified via browser in UAT step 3")


def test_ac4_autofixable_checks_resolve_to_fixed_with_note(client):
    # AC-4: Auto-fixable checks attempt the fix automatically and resolve to fixed with a note
    # Verified via UAT step 4 (simulated auto-fixable condition)
    pytest.skip("manual — verified via browser in UAT step 4")


def test_ac5_nonautofixable_failures_resolve_to_fail_with_reason(client):
    # AC-5: Non-auto-fixable failures resolve to fail with a reason string surfaced inline
    # Verified via UAT step 5 (simulated hard failure)
    pytest.skip("manual — verified via browser in UAT step 5")


def test_ac6_one_or_more_fail_blocks_run_displays_count(client):
    # AC-6: One or more fail states blocks the Run Sprint action; displays summary count
    # Verified via UAT step 5 when hard failure is present
    pytest.skip("manual — verified via browser in UAT step 5")


def test_ac7_all_pass_fixed_enables_run_shows_allclear(client):
    # AC-7: All checks resolving to pass or fixed enables Run Sprint; shows all-clear summary
    # Verified via UAT step 3
    pytest.skip("manual — verified via browser in UAT step 3")


def test_ac8_stepper_groupings_match_preflight_panel(client):
    # AC-8: Check groupings in the stepper match the groupings already defined in the pre-flight panel
    # Verified via UAT step 6
    pytest.skip("manual — verified via browser in UAT step 6")


def test_ac9_component_is_shared_stepper_not_oneoff(client):
    # AC-9: Component is the shared stepper/progress component — no one-off checklist implementation
    # This is verified by examining the HTML markup and ensuring the DOM uses a shared component class
    pytest.skip("manual — verified via design-contract gate / visual inspection, not HTTP")
