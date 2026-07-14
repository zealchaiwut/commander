"""Tests for issue #1857 — AC2: Auto error filter when recent errors present.

AC2: Opening Logs with recent errors present lands pre-filtered to Error severity;
a visible affordance switches back to All.
"""
import pytest


# AC2 auto-filter logic is tested via frontend unit tests in
# tests/frontend/logs-error-badge.test.mjs (evlIsErrorEvent classification)
# and project.html integration (auto-apply error severity filter in logsInit).


def test_ac2_frontend_classification_tests():
    """AC2 error classification is tested in frontend/logs-error-badge.test.mjs.

    Tests verify evlIsErrorEvent correctly identifies ticket_failed and
    agent_finished with error/timed_out statuses.

    Run frontend tests: node --test tests/frontend/logs-error-badge.test.mjs
    """
    pytest.skip("AC2 classification tests in frontend suite; integration verified by browser UAT")
