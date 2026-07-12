"""Tests for issue #1857 — AC3: Initial Activity fetch bounded by last-visit window.

AC3: Initial Activity fetch is bounded by last-visit timestamp (with sane fallback
for first visit); "show full history" loads the old flat window.
"""
import pytest


# AC3 URL building is tested via frontend unit tests.


def test_ac3_frontend_url_building():
    """AC3 URL building logic is tested in frontend/logs-error-badge.test.mjs.

    Tests verify:
    - buildEvlFetchUrl includes since= when sinceTs is provided
    - buildEvlFetchUrl falls back to limit=200 when sinceTs is null (first visit)
    - buildEvlFetchUrl falls back to limit=200 when sinceTs is empty string

    Run frontend tests: node --test tests/frontend/logs-error-badge.test.mjs
    """
    pytest.skip("AC3 URL building tests in frontend suite; backend since= support tested in AC4")
