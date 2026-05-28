"""Shared pytest fixtures for Commander dashboard tests.

Tests hit http://localhost:8000 directly (dev server must be running).
The `client` fixture is a plain httpx.Client pointed at that URL.
"""
import os
import pytest
import httpx

BASE_URL = os.environ.get("TEST_BASE_URL", "http://localhost:8000")


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live: mark test as requiring a running dashboard server (deselected by default)",
    )
    config.addinivalue_line(
        "markers",
        "selenium: mark test as requiring Chrome/ChromeDriver and a running dashboard server",
    )


@pytest.fixture(scope="session")
def base_url() -> str:
    return BASE_URL


@pytest.fixture
def client(base_url) -> httpx.Client:
    """httpx client pointed at the running dev server."""
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        yield c


@pytest.fixture
def async_client(base_url):
    """httpx AsyncClient for tests that need async. Use with pytest-asyncio."""
    import httpx

    async def _make():
        async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as c:
            yield c

    return _make


@pytest.fixture
def allow_stub_success(monkeypatch):
    """Set COMMANDER_ALLOW_STUB_SUCCESS=1 so that FileNotFoundError in
    _dispatch_coder/_dispatch_tester is treated as a silent stub success.
    Apply to test classes/functions that don't need a real claude CLI."""
    monkeypatch.setenv("COMMANDER_ALLOW_STUB_SUCCESS", "1")


@pytest.fixture(scope="session")
def driver():
    """Selenium WebDriver for real browser testing."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    options = Options()
    # Run headless by default (no visible window)
    # Uncomment to debug: options.add_argument("--no-headless")
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")

    driver = webdriver.Chrome(options=options)
    yield driver
    driver.quit()
