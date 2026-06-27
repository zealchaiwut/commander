"""Test AC6: _alert_ntfy returns silently if NTFY_TOPIC_URL is unset"""
import sys
import os
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "services" / "sprint_manager"))

from alerts import _alert_ntfy  # noqa: E402


@patch("alerts.urllib.request.urlopen")
def test_silent_return_when_env_unset(mock_urlopen):
    """No HTTP request when NTFY_TOPIC_URL is unset"""
    os.environ.pop("NTFY_TOPIC_URL", None)

    result = _alert_ntfy("Test", "Body")

    assert not mock_urlopen.called
    assert result is None


@patch("alerts.urllib.request.urlopen")
def test_silent_return_when_env_empty_string(mock_urlopen):
    """No HTTP request when NTFY_TOPIC_URL is empty string"""
    os.environ["NTFY_TOPIC_URL"] = ""

    result = _alert_ntfy("Test", "Body")

    assert not mock_urlopen.called
    assert result is None
