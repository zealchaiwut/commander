"""Shared pytest configuration for the Commander test suite.

Sets DB_PATH and COMMANDER_DISABLE_NEON before any test module imports
apps.dashboard.db (which hard-exits when DB_PATH is unset).

Also ensures sys.path includes the repo root and apps/dashboard so tests can
import project modules regardless of the working directory pytest is invoked from.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Must run at conftest import time — before test modules import server/db.
_TEST_DB = Path(os.environ.get("COMMANDER_TEST_DB", "/tmp/commander-pytest.db"))
os.environ.setdefault("DB_PATH", str(_TEST_DB))
os.environ.setdefault("COMMANDER_DISABLE_NEON", "1")

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"
_SPRINT_MGR_DIR = _REPO_ROOT / "services" / "sprint_manager"

for _p in (str(_REPO_ROOT), str(_DASHBOARD_DIR), str(_SPRINT_MGR_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

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
