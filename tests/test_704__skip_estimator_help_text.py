"""Tests for issue #704 — fix `--skip-estimator` help text referring to documenter.

Suggested fix (the acceptance bar for this follow-up):
  Change the estimator flag help text to accurately describe what the flag does,
  e.g. "Skip the estimator run (on by default for debugging)." — it must NOT
  describe the documenter agent.
"""

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"


def _help_text() -> str:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_skip_estimator_flag_is_documented():
    """AC: the --skip-estimator flag has visible, accurate help text."""
    help_text = _help_text()
    assert "--skip-estimator" in help_text


def test_skip_estimator_help_describes_estimator_not_documenter():
    """AC: the estimator flag help describes the estimator, not the documenter."""
    help_text = _help_text()

    # Locate the help block for --skip-estimator (from the flag up to the next
    # option marker or blank-line gap).
    idx = help_text.find("--skip-estimator")
    assert idx != -1, "--skip-estimator not present in --help output"
    block = help_text[idx:]
    next_opt = re.search(r"\n\s*--", block[len("--skip-estimator"):])
    if next_opt:
        block = block[: len("--skip-estimator") + next_opt.start()]

    lowered = block.lower()
    assert "documenter" not in lowered, (
        "estimator flag help text still refers to the documenter: " + block
    )
    assert "estimator" in lowered, (
        "estimator flag help text should describe the estimator: " + block
    )
