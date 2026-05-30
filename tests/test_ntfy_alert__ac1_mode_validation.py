"""Test AC1: AlertMode.NTFY validates without error"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "services" / "sprint_manager"))

from sprint_manager import AlertMode


def test_ntfy_in_alert_modes():
    """NTFY is in ALL_MODES"""
    assert AlertMode.NTFY in AlertMode.ALL_MODES


def test_ntfy_value():
    """NTFY = 'ntfy' string"""
    assert AlertMode.NTFY == "ntfy"


def test_all_modes_contains_ntfy():
    """ALL_MODES is a set that contains NTFY"""
    assert "ntfy" in AlertMode.ALL_MODES
