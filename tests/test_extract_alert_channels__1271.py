"""Tests for issue #1271: Extract sprint_manager alert channels to alerts.py (runs against UAT)"""
import os
import subprocess
import sys


def test_extract_alert_channels__alerts_py_exists():
    """AC: services/sprint_manager/alerts.py exists and contains HangDetector, dispatch_alerts, _alert_*, etc."""
    # Find repo root by looking for services directory from current working directory
    cwd = os.getcwd()
    alerts_path = os.path.join(cwd, "services", "sprint_manager", "alerts.py")
    assert os.path.isfile(alerts_path), f"alerts.py not found at {alerts_path}"


def test_extract_alert_channels__alerts_py_compiles():
    """AC: python -m py_compile services/sprint_manager/alerts.py exits 0"""
    cwd = os.getcwd()
    alerts_file = os.path.join(cwd, "services", "sprint_manager", "alerts.py")
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", alerts_file],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"alerts.py compilation failed: {result.stderr}"


def test_extract_alert_channels__sprint_manager_compiles():
    """AC: python -m py_compile services/sprint_manager/sprint_manager.py exits 0"""
    cwd = os.getcwd()
    sm_file = os.path.join(cwd, "services", "sprint_manager", "sprint_manager.py")
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", sm_file],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"sprint_manager.py compilation failed: {result.stderr}"


def test_extract_alert_channels__symbols_importable():
    """AC: All moved symbols are importable from alerts.py with no circular imports"""
    try:
        from services.sprint_manager.alerts import (
            HangDetector,
            dispatch_alerts,
            _alert_dashboard_banner,
            _alert_email,
            _alert_discord,
            _alert_ntfy,
            _alert_file,
            AlertMode,
        )
        # If we got here, all imports succeeded
        assert callable(dispatch_alerts)
        assert callable(HangDetector)
        assert hasattr(AlertMode, "DASHBOARD_BANNER")
    except ImportError as e:
        raise AssertionError(f"Import failed: {e}") from e


def test_extract_alert_channels__no_new_logic():
    """AC: No alert dispatch logic is added, removed, or altered — diff confirms move only"""
    # Check that dispatch_alerts signature and behavior matches expectations
    from services.sprint_manager.alerts import dispatch_alerts
    import inspect

    sig = inspect.signature(dispatch_alerts)
    params = list(sig.parameters.keys())
    # Expected parameters based on the AC description
    expected_params = ["alert_modes", "title", "body", "issue_num", "category", "cfg", "repo", "sprint_label"]
    assert params == expected_params, f"dispatch_alerts signature changed: {params} != {expected_params}"


def test_extract_alert_channels__re_export_in_sprint_manager():
    """AC: Original module re-exports or updates imports so call sites are unaffected"""
    # Verify sprint_manager can still access the alert functions via import
    try:
        from services.sprint_manager.sprint_manager import dispatch_alerts, HangDetector, AlertMode
        assert callable(dispatch_alerts)
        assert callable(HangDetector)
        assert hasattr(AlertMode, "EMAIL")
    except ImportError as e:
        raise AssertionError(f"sprint_manager does not re-export alert symbols: {e}") from e
