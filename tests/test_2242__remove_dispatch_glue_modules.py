"""Tests for issue #2242: Remove dispatch glue modules.

AC1: Both modules deleted (sprint_webhook_service.py, dispatch_service.py)
AC2: startup.py's import of dispatch_service removed
AC3: Dashboard starts clean
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
ROUTERS_DIR = DASHBOARD_DIR / "routers"

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")


class TestModulesDeleted:
    """AC1: Both glue modules are deleted from the filesystem."""

    def test_sprint_webhook_service_deleted(self):
        """sprint_webhook_service.py must not exist."""
        path = ROUTERS_DIR / "sprint_webhook_service.py"
        assert not path.exists(), f"{path.name} must be deleted per issue #2242 AC1"

    def test_dispatch_service_deleted(self):
        """dispatch_service.py must not exist."""
        path = ROUTERS_DIR / "dispatch_service.py"
        assert not path.exists(), f"{path.name} must be deleted per issue #2242 AC1"


class TestStartupNoDispatchImport:
    """AC2: startup._build_sprint_subprocess_env() works without dispatch_service."""

    def test_build_sprint_subprocess_env_returns_dict(self):
        """Behavioral: _build_sprint_subprocess_env() returns a valid dict of
        env vars even when dispatch_service.py no longer exists."""
        result = subprocess.run(
            [
                sys.executable, "-c",
                "\n".join([
                    "import sys, os",
                    f"sys.path.insert(0, {str(DASHBOARD_DIR)!r})",
                    f"sys.path.insert(0, {str(REPO_ROOT)!r})",
                    "os.environ.setdefault('DB_PATH', '/tmp/t.db')",
                    "os.environ.setdefault('COMMANDER_DISABLE_NEON', '1')",
                    "import startup",
                    "env = startup._build_sprint_subprocess_env()",
                    "assert isinstance(env, dict), f'expected dict, got {type(env)}'",
                    "assert 'ANTHROPIC_API_KEY' not in env, 'API key must be stripped'",
                    "print('ok')",
                ]),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"_build_sprint_subprocess_env() failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "ok" in result.stdout


class TestDashboardStartsClean:
    """AC3: Dashboard starts clean — sprint_run* routers import without errors."""

    def test_sprint_run_imports_without_sprint_webhook_service(self):
        """Behavioral: routers.sprint_run can be imported cleanly even though
        sprint_webhook_service.py no longer exists."""
        result = subprocess.run(
            [
                sys.executable, "-c",
                "\n".join([
                    "import sys, os",
                    f"sys.path.insert(0, {str(DASHBOARD_DIR)!r})",
                    f"sys.path.insert(0, {str(REPO_ROOT)!r})",
                    "os.environ.setdefault('DB_PATH', '/tmp/t.db')",
                    "import routers.sprint_run",
                    "print('ok')",
                ]),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"routers.sprint_run import failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "ok" in result.stdout

    def test_sprint_run_service_imports_without_sprint_webhook_service(self):
        """Behavioral: routers.sprint_run_service can be imported cleanly even
        though sprint_webhook_service.py no longer exists."""
        result = subprocess.run(
            [
                sys.executable, "-c",
                "\n".join([
                    "import sys, os",
                    f"sys.path.insert(0, {str(DASHBOARD_DIR)!r})",
                    f"sys.path.insert(0, {str(REPO_ROOT)!r})",
                    "os.environ.setdefault('DB_PATH', '/tmp/t.db')",
                    "import routers.sprint_run_service",
                    "print('ok')",
                ]),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"routers.sprint_run_service import failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "ok" in result.stdout
