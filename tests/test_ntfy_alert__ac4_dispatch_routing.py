"""Test AC4: dispatch_alerts routes AlertMode.NTFY to _alert_ntfy"""
import sys
import os
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "services" / "sprint_manager"))

from sprint_manager import dispatch_alerts, AlertMode


@patch("sprint_manager._alert_ntfy")
def test_dispatch_routes_ntfy_mode(mock_alert_ntfy):
    """dispatch_alerts calls _alert_ntfy when NTFY mode is present"""
    os.environ["NTFY_TOPIC_URL"] = "https://ntfy.sh/test"

    dispatch_alerts(
        alert_modes=[AlertMode.NTFY],
        title="Test Title",
        body="Test Body",
        category="failure"
    )

    # _alert_ntfy should have been called
    assert mock_alert_ntfy.called
    call_args = mock_alert_ntfy.call_args
    assert call_args[0][0] == "Test Title"
    assert call_args[0][1] == "Test Body"
    assert call_args[1].get("category") == "failure" or call_args[0][2] == "failure"


@patch("sprint_manager._alert_ntfy")
@patch("sprint_manager._alert_dashboard_banner")
def test_dispatch_with_multiple_modes(mock_banner, mock_ntfy):
    """dispatch_alerts calls both banner and ntfy when both modes present"""
    os.environ["NTFY_TOPIC_URL"] = "https://ntfy.sh/test"

    dispatch_alerts(
        alert_modes=[AlertMode.DASHBOARD_BANNER, AlertMode.NTFY],
        title="Test",
        body="Body"
    )

    assert mock_banner.called
    assert mock_ntfy.called
