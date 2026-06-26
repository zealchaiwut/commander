"""Test AC3: Priority mapping for failure/needs-rework"""
import sys
import os
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "services" / "sprint_manager"))

from alerts import _alert_ntfy  # noqa: E402


@patch("alerts.urllib.request.urlopen")
def test_priority_high_for_failure(mock_urlopen):
    """failure category maps to priority 4 (high)"""
    os.environ["NTFY_TOPIC_URL"] = "https://ntfy.sh/test"

    _alert_ntfy("Test", "Body", category="failure")

    # Check that urlopen was called
    assert mock_urlopen.called
    req = mock_urlopen.call_args[0][0]
    assert req.headers.get("priority") == "4" or req.headers.get("Priority") == "4"


@patch("alerts.urllib.request.urlopen")
def test_priority_high_for_needs_rework(mock_urlopen):
    """needs-rework category maps to priority 4 (high)"""
    os.environ["NTFY_TOPIC_URL"] = "https://ntfy.sh/test"

    _alert_ntfy("Test", "Body", category="needs-rework")

    assert mock_urlopen.called
    req = mock_urlopen.call_args[0][0]
    assert req.headers.get("priority") == "4" or req.headers.get("Priority") == "4"


@patch("alerts.urllib.request.urlopen")
def test_priority_default_for_other_categories(mock_urlopen):
    """Other categories map to priority 3 (default)"""
    os.environ["NTFY_TOPIC_URL"] = "https://ntfy.sh/test"

    _alert_ntfy("Test", "Body", category="completion")

    assert mock_urlopen.called
    req = mock_urlopen.call_args[0][0]
    assert req.headers.get("priority") == "3" or req.headers.get("Priority") == "3"


@patch("alerts.urllib.request.urlopen")
def test_priority_default_when_no_category(mock_urlopen):
    """No category defaults to priority 3"""
    os.environ["NTFY_TOPIC_URL"] = "https://ntfy.sh/test"

    _alert_ntfy("Test", "Body")

    assert mock_urlopen.called
    req = mock_urlopen.call_args[0][0]
    assert req.headers.get("priority") == "3" or req.headers.get("Priority") == "3"
