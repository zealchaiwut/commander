"""Shared pytest fixtures for Commander dashboard tests.

Tests hit http://localhost:8000 directly (dev server must be running).
The `client` fixture is a plain httpx.Client pointed at that URL.
"""
import os
from pathlib import Path

# ── DB isolation guard (issue #2047) ─────────────────────────────────────────
# Force the test DB path BEFORE any test module imports apps.dashboard.db.
# Use = (not setdefault) so this overrides any pre-exported DB_PATH (e.g. from
# sourcing apps/dashboard/.env which has the relative value ./commander.db).
_TEST_DB = Path(os.environ.get("COMMANDER_TEST_DB", "/tmp/commander-pytest.db"))
os.environ["DB_PATH"] = str(_TEST_DB)
os.environ.setdefault("COMMANDER_DISABLE_NEON", "1")

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


@pytest.fixture(scope="session", autouse=True)
def _assert_db_not_in_repo() -> None:
    """Fail the entire test run immediately if DB_PATH resolves inside the repo.

    Belt-and-braces guard (issue #2047): aborts the session before any test runs
    so a repo-internal DB_PATH can never silently corrupt a production database.
    """
    _repo_root = Path(__file__).parent.parent.parent.resolve()  # apps/dashboard/tests → repo root
    _db_path_str = os.environ.get("DB_PATH", "")
    if _db_path_str:
        _db_path = Path(_db_path_str)
        if _db_path.is_absolute():
            try:
                _db_path.relative_to(_repo_root)
                _in_repo = True
            except ValueError:
                _in_repo = False
            if _in_repo:
                pytest.exit(
                    f"\nSAFETY ABORT: DB_PATH is inside the repo working tree!\n"
                    f"  DB_PATH    = {_db_path}\n"
                    f"  Repo root  = {_repo_root}\n"
                    "Tests must never write to a database inside the repo.  "
                    "Ensure this conftest.py forces DB_PATH before any module import.",
                    returncode=1,
                )
    yield


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
