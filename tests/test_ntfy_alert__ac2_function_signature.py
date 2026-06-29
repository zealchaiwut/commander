"""Test AC2: _alert_ntfy function exists with correct signature"""
import sys
import inspect
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "services" / "sprint_manager"))

from alerts import _alert_ntfy  # noqa: E402


def test_alert_ntfy_exists():
    """_alert_ntfy function is defined"""
    assert callable(_alert_ntfy)


def test_alert_ntfy_signature():
    """_alert_ntfy has correct parameters"""
    sig = inspect.signature(_alert_ntfy)
    params = list(sig.parameters.keys())

    assert "title" in params
    assert "body" in params
    assert "category" in params

    # category should have a default value (Optional)
    assert sig.parameters["category"].default is not inspect.Parameter.empty or \
           str(sig.parameters["category"].annotation).startswith("typing.Optional")
