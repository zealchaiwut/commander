"""Browser UAT tests for issue #930: Batch reestimate with shared progress bar.

All AC tests are routed as manual since they require user interaction to trigger
batch reestimate, select multiple tickets, and observe the progress bar.
"""
import os
import pytest


BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    raise RuntimeError("UAT_BASE_URL / UAT_PORT not set")


def test_ac1_batch_reestimate_overlay_opens():
    """AC1: Batch reestimate (≥2 tickets) opens shared progress overlay."""
    pytest.skip("manual — navigate to sprint, select ≥2 tickets, click 'Reestimate All', verify progress overlay opens in bar mode")


def test_ac2_progress_bar_shows_of_format():
    """AC2: Progress bar shows 'X of Y' ticket count."""
    pytest.skip("manual — verify progress bar displays '0 of N' format (e.g. '0 of 12')")


def test_ac3_current_ticket_displayed():
    """AC3: Current ticket being estimated is visible."""
    pytest.skip("manual — verify current ticket label in overlay updates as each ticket is estimated")


def test_ac4_log_entries_appear():
    """AC4: Per-ticket results appear in log slot."""
    pytest.skip("manual — verify each ticket logs result (e.g. '#170 → M, ~15m') as estimation completes")


def test_ac5_progress_updates_per_ticket():
    """AC5: Progress advances when each ticket returns an estimate."""
    pytest.skip("manual — verify progress bar increments and fill updates for each completed ticket")


def test_ac6_overlay_backgroundable():
    """AC6: Overlay can be dismissed and returned to."""
    pytest.skip("manual — dismiss overlay, navigate away, return to sprint; verify batch continues in background")


def test_ac7_done_summary_displayed():
    """AC7: Done summary shows total reestimated count."""
    pytest.skip("manual — let batch complete; verify done summary displays 'N reestimated' (e.g. '12 reestimated')")


def test_ac8_error_and_retry_state():
    """AC8: Partial failure shows error/retry end state."""
    pytest.skip("manual — simulate failure (network drop); verify error state appears with 'Retry failed' button")


def test_ac9_single_ticket_spinner():
    """AC9: Single-ticket reestimate uses lightweight spinner, NOT shared overlay."""
    pytest.skip("manual — select 1 ticket, click reestimate; verify inline spinner appears (no progress overlay)")


def test_ac10_shared_progress_component_available():
    """AC10: Feature depends on shared progress component bar mode."""
    pytest.skip("manual — verify shared progress component (_smgmtBoardLock, _smgmtBoardProgress, _smgmtBoardLog) wired in")
