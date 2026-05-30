"""Test AC5: .env.example includes commented NTFY_TOPIC_URL example"""
from pathlib import Path


def test_env_example_includes_ntfy_topic_url():
    """NTFY_TOPIC_URL is documented in .env.example"""
    env_example = Path(__file__).parent.parent / "apps" / "dashboard" / ".env.example"

    content = env_example.read_text()

    # Should have a commented NTFY_TOPIC_URL with example format
    assert "NTFY_TOPIC_URL" in content
    assert "https://ntfy.sh" in content


def test_env_example_includes_commander_alert_modes():
    """COMMANDER_ALERT_MODES is documented in .env.example"""
    env_example = Path(__file__).parent.parent / "apps" / "dashboard" / ".env.example"

    content = env_example.read_text()

    # Should have documentation for COMMANDER_ALERT_MODES
    assert "COMMANDER_ALERT_MODES" in content
    assert "ntfy" in content.lower()
