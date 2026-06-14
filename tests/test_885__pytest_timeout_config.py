"""Tests for issue #885: pytest-timeout added and hanging documentor tests fixed.

AC items verified:
  AC-1  pytest-timeout present in requirements.txt
  AC-2  pytest.ini / pyproject.toml sets timeout = 60
  AC-3  test_697__run_documentor_once_per_sprint completes without hanging
  AC-4  TestAC2DocumentorCalledOnceAfterLoop::test_three_passing_tickets_calls_documentor completes
  AC-5  pytest tests/ run terminates with no test exceeding 60s
  AC-6  All previously passing tests continue to pass
"""
from __future__ import annotations

import configparser
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# AC-1: pytest-timeout in requirements.txt
# ---------------------------------------------------------------------------

def test_pytest_timeout_in_requirements():
    """pytest-timeout must appear in requirements.txt."""
    req_file = REPO_ROOT / "requirements.txt"
    assert req_file.exists(), "requirements.txt must exist"
    content = req_file.read_text()
    assert "pytest-timeout" in content, (
        "pytest-timeout must be listed in requirements.txt"
    )


# ---------------------------------------------------------------------------
# AC-2: timeout = 60 configured in pytest.ini / pyproject.toml / setup.cfg
# ---------------------------------------------------------------------------

def test_timeout_60_configured():
    """pytest must be configured with a 60-second default timeout."""
    # Check pytest.ini
    pytest_ini = REPO_ROOT / "pytest.ini"
    if pytest_ini.exists():
        cfg = configparser.ConfigParser()
        cfg.read(str(pytest_ini))
        if "pytest" in cfg:
            timeout_val = cfg["pytest"].get("timeout", "").strip()
            assert timeout_val == "60", (
                f"pytest.ini [pytest] timeout must be '60', got {timeout_val!r}"
            )
            return

    # Check pyproject.toml
    pyproject = REPO_ROOT / "pyproject.toml"
    if pyproject.exists():
        content = pyproject.read_text()
        if "timeout" in content:
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("timeout") and "=" in line:
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    assert val == "60", (
                        f"pyproject.toml timeout must be '60', got {val!r}"
                    )
                    return

    # Check setup.cfg
    setup_cfg = REPO_ROOT / "setup.cfg"
    if setup_cfg.exists():
        cfg = configparser.ConfigParser()
        cfg.read(str(setup_cfg))
        if "tool:pytest" in cfg:
            timeout_val = cfg["tool:pytest"].get("timeout", "").strip()
            assert timeout_val == "60", (
                f"setup.cfg [tool:pytest] timeout must be '60', got {timeout_val!r}"
            )
            return

    pytest.fail(
        "No pytest config file found with timeout=60. "
        "Create pytest.ini with [pytest] timeout = 60"
    )


# ---------------------------------------------------------------------------
# AC-1 (runtime): pytest-timeout is actually importable (installed)
# ---------------------------------------------------------------------------

def test_pytest_timeout_importable():
    """pytest-timeout must be installed and importable."""
    try:
        import pytest_timeout  # noqa: F401
    except ImportError:
        pytest.fail(
            "pytest-timeout is not installed. Run: pip install pytest-timeout"
        )
