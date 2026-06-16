"""P2: sprint_manager reads pipeline_mode from settings when yaml is false."""
from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SM = _ROOT / "services" / "sprint_manager" / "sprint_manager.py"


def test_run_sprint_reads_settings_pipeline_mode():
    src = _SM.read_text(encoding="utf-8")
    fn = src[src.index("def run_sprint("):]
    fn = fn[: fn.index("\ndef ", 1)]
    assert "settings_repo" in fn
    assert "_project_pipeline" in fn
