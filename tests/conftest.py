"""Shared pytest configuration for the Commander test suite.

Ensures sys.path includes the repo root and apps/dashboard so tests can import
project modules (github_client, config, routers.*, services.*) regardless of the
working directory pytest is invoked from.
"""
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"
_SPRINT_MGR_DIR = _REPO_ROOT / "services" / "sprint_manager"

for _p in (str(_REPO_ROOT), str(_DASHBOARD_DIR), str(_SPRINT_MGR_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
