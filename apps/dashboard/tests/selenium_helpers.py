"""
Shared helper for Selenium browser tests.

Requirements:
  - Chrome/Chromium >= 114 installed (ChromeDriver managed automatically by webdriver-manager)
  - Dashboard running at BASE_URL (default: http://localhost:8000)

Skip all Selenium tests in headless-CI or no-Chrome envs:
  pytest tests/ -m "not selenium"
"""
import os
import pytest
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

BASE_URL = os.environ.get("TEST_BASE_URL", "http://localhost:8000")


def build_driver() -> webdriver.Chrome:
    """Return a headless Chrome WebDriver pointed at BASE_URL."""
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=options)


@pytest.fixture
def driver():
    """Per-test headless Chrome WebDriver; tears down after each test."""
    drv = build_driver()
    drv.implicitly_wait(5)
    yield drv
    drv.quit()
