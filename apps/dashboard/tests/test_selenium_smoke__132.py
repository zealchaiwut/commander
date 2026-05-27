"""
Selenium smoke test for issue #132.

Verifies that the dashboard root page loads in a real Chrome browser and
that the page title contains "Commander".

Run all Selenium tests:
  pytest tests/test_selenium_smoke__132.py -v

Skip in environments without Chrome:
  pytest tests/ -m "not selenium"
"""
import pytest
from tests.selenium_helpers import BASE_URL, driver  # noqa: F401


@pytest.mark.selenium
def test_dashboard_root_title(driver):
    driver.get(BASE_URL + "/")
    assert "Commander" in driver.title, (
        f"Expected 'Commander' in page title, got: {driver.title!r}"
    )
