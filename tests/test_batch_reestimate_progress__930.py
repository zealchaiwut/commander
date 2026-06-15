"""Tests for issue #930: Batch reestimate: show shared progress bar component (runs against UAT)"""
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

def test_batch_reestimate_sprint_or_selection_opens_progress_component(client):
    # AC-1: Batch reestimate (sprint or selection ≥2 tickets) opens the shared progress component in bar mode
    # This is verified via UAT steps (browser interaction), not HTTP
    pytest.skip("manual — verified via browser in UAT steps 1-7")


def test_progress_bar_shows_current_of_total_tickets(client):
    # AC-2: Progress bar shows `current / total` tickets (e.g. "3 of 12")
    pytest.skip("manual — verified via browser in UAT steps 1-2")


def test_current_ticket_being_estimated_is_visible(client):
    # AC-3: Current ticket being estimated is visible in the component
    pytest.skip("manual — verified via browser in UAT steps 2-4")


def test_per_ticket_results_appear_in_log_slot(client):
    # AC-4: Per-ticket results appear in the log slot as each completes (e.g. `#170 -> M, ~15m`)
    pytest.skip("manual — verified via browser in UAT step 2")


def test_progress_advances_immediately_on_ticket_return(client):
    # AC-5: Progress advances immediately when each ticket returns a size/time estimate
    pytest.skip("manual — verified via browser in UAT step 2")


def test_component_is_backgroundable(client):
    # AC-6: Component is background-able; user can dismiss and return to see live state
    pytest.skip("manual — verified via browser in UAT step 3")


def test_done_summary_on_completion(client):
    # AC-7: On completion, a done summary is shown (e.g. "12 reestimated")
    pytest.skip("manual — verified via browser in UAT step 4")


def test_error_retry_end_state_on_partial_failure(client):
    # AC-8: On partial failure, an error/retry end state is shown with actionable option to retry failed tickets
    pytest.skip("manual — verified via browser in UAT step 5")


def test_single_ticket_reestimate_shows_lightweight_inline_spinner(client):
    # AC-9: Single-ticket reestimate shows only a lightweight inline spinner — shared progress component is NOT used
    pytest.skip("manual — verified via browser in UAT step 6")


def test_depends_on_shared_progress_component_bar_mode(client):
    # AC-10: Depends on the shared progress component (bar mode) being available
    pytest.skip("manual — verified by design-contract gate and component availability")
