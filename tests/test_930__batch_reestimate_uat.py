"""Tests for issue #930: Batch reestimate: show shared progress bar component (UAT).

AC coverage:
  AC1:  Batch reestimate (sprint or selection >=2 tickets) opens shared progress component in bar mode
  AC2:  Progress bar shows current/total tickets (e.g. "3 of 12")
  AC3:  Current ticket being estimated is visible in the component
  AC4:  Per-ticket results appear in the log slot as each completes
  AC5:  Progress advances immediately when each ticket returns a size/time estimate
  AC6:  Component is background-able; user can dismiss and return to see live state
  AC7:  On completion, a done summary is shown (e.g. "12 reestimated")
  AC8:  On partial failure, an error/retry end state is shown with actionable retry
  AC9:  Single-ticket reestimate shows only a lightweight inline spinner (NOT the shared component)
  AC10: Depends on the shared progress component (bar mode) being available
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


# ── Acceptance Criteria ──

def test_ac1_batch_reestimate_opens_progress_overlay(client):
    """AC1: Batch reestimate (≥2 tickets) opens shared progress component in bar mode."""
    pytest.skip("manual — browser step: navigate to sprint, select >=2 tickets, click reestimate")


def test_ac2_progress_bar_shows_current_of_total(client):
    """AC2: Progress bar shows 'X of Y' format for ticket count."""
    pytest.skip("manual — browser step: verify progress bar shows '0 of N' initially")


def test_ac3_current_ticket_visible_in_component(client):
    """AC3: Current ticket being estimated is visible in the component."""
    pytest.skip("manual — browser step: verify current ticket label updates during estimation")


def test_ac4_per_ticket_results_in_log_slot(client):
    """AC4: Per-ticket results appear in the log slot as each completes."""
    pytest.skip("manual — browser step: verify each ticket logs '#N → size, time' as it completes")


def test_ac5_progress_advances_per_ticket(client):
    """AC5: Progress advances immediately when each ticket returns an estimate."""
    pytest.skip("manual — browser step: verify progress bar increments for each completed ticket")


def test_ac6_component_is_backgroundable(client):
    """AC6: Component is background-able; user can dismiss and return to see live state."""
    pytest.skip("manual — browser step: dismiss overlay mid-estimation, navigate away, return and verify state persists")


def test_ac7_done_summary_on_completion(client):
    """AC7: On completion, a done summary is shown (e.g. '12 reestimated')."""
    pytest.skip("manual — browser step: let batch complete and verify done summary displays total count")


def test_ac8_error_and_retry_on_partial_failure(client):
    """AC8: On partial failure, error/retry end state appears with actionable retry option."""
    pytest.skip("manual — browser step: simulate failure (network drop) and verify error state with retry option")


def test_ac9_single_ticket_uses_lightweight_spinner(client):
    """AC9: Single-ticket reestimate shows only lightweight inline spinner, NOT shared component."""
    pytest.skip("manual — browser step: select 1 ticket, trigger reestimate, verify inline spinner (no progress overlay)")


def test_ac10_depends_on_shared_progress_component(client):
    """AC10: Feature depends on shared progress component (bar mode) being available."""
    pytest.skip("manual — verify shared progress component bar mode is available")
