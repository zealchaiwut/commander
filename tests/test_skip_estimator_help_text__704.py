"""Tests for issue #704: Fix skip_estimator help text referring to documenter.

This follow-up corrects the `--skip-estimator` argparse flag in
`services/sprint_manager/sprint_manager.py`. The acceptance bar:
the estimator flag's help text must accurately describe the estimator
and must NOT refer to the documenter agent.

Not HTTP-testable — verified by invoking the script's --help and
inspecting the rendered argparse output (subprocess, stdlib only).
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


def _skip_estimator_block(help_text: str) -> str:
    idx = help_text.find("--skip-estimator")
    assert idx != -1, "--skip-estimator not present in --help output"
    block = help_text[idx:]
    next_opt = re.search(r"\n\s*--", block[len("--skip-estimator"):])
    if next_opt:
        block = block[: len("--skip-estimator") + next_opt.start()]
    return block


def test_skip_estimator_help_text__flag_is_documented():
    # AC: the --skip-estimator flag is visible with help text (was SUPPRESS before).
    help_text = _help_text()
    assert "--skip-estimator" in help_text
    block = _skip_estimator_block(help_text)
    assert block.strip() != "--skip-estimator", "flag has no help text rendered"


def test_skip_estimator_help_text__describes_estimator_not_documenter():
    # AC: help describes the estimator, not the documenter agent.
    block = _skip_estimator_block(_help_text())
    lowered = block.lower()
    assert "documenter" not in lowered, (
        "estimator flag help text still refers to the documenter: " + block
    )
    assert "estimator" in lowered, (
        "estimator flag help text should describe the estimator: " + block
    )
