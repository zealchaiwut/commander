"""Shared pytest configuration for the Commander test suite.

Sets DB_PATH and COMMANDER_DISABLE_NEON before any test module imports
apps.dashboard.db (which hard-exits when DB_PATH is unset).
"""
from __future__ import annotations

import os
from pathlib import Path

# Must run at conftest import time — before test modules import server/db.
_TEST_DB = Path(os.environ.get("COMMANDER_TEST_DB", "/tmp/commander-pytest.db"))
os.environ.setdefault("DB_PATH", str(_TEST_DB))
os.environ.setdefault("COMMANDER_DISABLE_NEON", "1")

import pytest


@pytest.fixture(scope="session")
def driver():
    """Headless Chrome WebDriver for @pytest.mark.selenium browser tests (issue #334 AC-9).

    Opt in with: pytest -m selenium
    Requires TEST_BASE_URL (default http://localhost:8000) serving project.html.
    """
    pytest.importorskip("selenium")
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    wd = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=opts,
    )
    yield wd
    wd.quit()
