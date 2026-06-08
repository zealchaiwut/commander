"""
Verification for #463: AlertMode.NTFY enum and dispatch_alerts routing wired correctly.

Confirms that code shipped in #405 is correctly wired:
  - AlertMode.NTFY exists with value "ntfy"
  - AlertMode.ALL_MODES contains NTFY
  - dispatch_alerts routes NTFY mode to _alert_ntfy(title, body, category)
"""
import sys
import os
from pathlib import Path
from unittest.mock import patch, call

sys.path.insert(0, str(Path(__file__).parent.parent / "services" / "sprint_manager"))

from sprint_manager import AlertMode, dispatch_alerts


def test_alertmode_ntfy_enum_value():
    assert AlertMode.NTFY == "ntfy"


def test_alertmode_ntfy_in_all_modes():
    assert AlertMode.NTFY in AlertMode.ALL_MODES


@patch("sprint_manager._alert_ntfy")
def test_dispatch_alerts_routes_ntfy_to_alert_ntfy(mock_ntfy):
    dispatch_alerts(
        alert_modes=[AlertMode.NTFY],
        title="Sprint Alert",
        body="A coder timed out.",
        category="failure",
    )

    mock_ntfy.assert_called_once_with("Sprint Alert", "A coder timed out.", "failure")


@patch("sprint_manager._alert_ntfy")
def test_dispatch_alerts_ntfy_not_called_for_other_modes(mock_ntfy):
    """NTFY handler not triggered when mode is dashboard-banner."""
    with patch("sprint_manager._alert_dashboard_banner"):
        dispatch_alerts(
            alert_modes=[AlertMode.DASHBOARD_BANNER],
            title="T",
            body="B",
        )
    mock_ntfy.assert_not_called()


@patch("sprint_manager._alert_ntfy")
def test_dispatch_alerts_ntfy_skipped_for_none_mode(mock_ntfy):
    dispatch_alerts(
        alert_modes=[AlertMode.NONE],
        title="T",
        body="B",
    )
    mock_ntfy.assert_not_called()
