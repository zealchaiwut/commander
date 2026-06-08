"""Test AC7: Exceptions in _alert_ntfy are caught and logged"""
import sys
import os
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "services" / "sprint_manager"))

from sprint_manager import dispatch_alerts, AlertMode


@patch("sprint_manager.structured_log.error")
@patch("sprint_manager.urllib.request.urlopen")
def test_exception_logged_not_raised(mock_urlopen, mock_log_error):
    """Exception in _alert_ntfy is caught and logged, not re-raised"""
    os.environ["NTFY_TOPIC_URL"] = "https://ntfy.sh/test"

    # Make urlopen raise an exception
    mock_urlopen.side_effect = Exception("Network error")

    # dispatch_alerts should not raise
    dispatch_alerts(
        alert_modes=[AlertMode.NTFY],
        title="Test",
        body="Body"
    )

    # Error should have been logged
    assert mock_log_error.called
    call_args = mock_log_error.call_args
    # Check that the call included the alert mode
    assert "ntfy" in str(call_args).lower() or "alert:ntfy" in str(call_args)


@patch("sprint_manager.structured_log.error")
@patch("sprint_manager.urllib.request.urlopen")
def test_other_alert_modes_unaffected(mock_urlopen, mock_log_error):
    """Exception in ntfy does not interrupt other alert channels"""
    os.environ["NTFY_TOPIC_URL"] = "https://ntfy.sh/test"

    # Make urlopen raise for ntfy
    mock_urlopen.side_effect = Exception("Network error")

    with patch("sprint_manager._alert_dashboard_banner") as mock_banner:
        # dispatch with both modes
        dispatch_alerts(
            alert_modes=[AlertMode.DASHBOARD_BANNER, AlertMode.NTFY],
            title="Test",
            body="Body"
        )

        # Banner should still have been called (ntfy exception doesn't block it)
        assert mock_banner.called
