"""Sprint suite health gate — runs full pytest suite, records metrics.

Usage (from sprint_manager.py — auto-triggered at sprint end):
    from services.sprint_manager.suite_health_gate import run_gate, load_gate_result

Usage (manual CLI):
    python3 scripts/run_suite_health_gate.py --sprint-label sprint-72 --sprints-dir .commander/sprints
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

SUITE_HEALTH_TIMEOUT_DEFAULT: int = int(
    os.environ.get("COMMANDER_SUITE_HEALTH_TIMEOUT", "300")
)


@dataclass
class SuiteHealthResult:
    sprint_label: str
    passed: int
    failed: int
    skipped: int
    duration_seconds: float
    timed_out: bool
    recorded_at: str  # ISO-8601 UTC

    @property
    def status(self) -> str:
        if self.timed_out:
            return "timeout"
        if self.failed > 0:
            return "failing"
        return "passing"

    def to_dict(self) -> dict:
        return {
            "sprint_label":      self.sprint_label,
            "pass":              self.passed,
            "fail":              self.failed,
            "skip":              self.skipped,
            "duration_seconds":  self.duration_seconds,
            "timed_out":         self.timed_out,
            "status":            self.status,
            "recorded_at":       self.recorded_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SuiteHealthResult":
        return cls(
            sprint_label     = data.get("sprint_label", ""),
            passed           = int(data.get("pass", 0)),
            failed           = int(data.get("fail", 0)),
            skipped          = int(data.get("skip", 0)),
            duration_seconds = float(data.get("duration_seconds", 0.0)),
            timed_out        = bool(data.get("timed_out", False)),
            recorded_at      = data.get("recorded_at", ""),
        )


def _health_json_path(sprint_label: str, sprints_dir: Path) -> Path:
    return sprints_dir / f"{sprint_label}-suite-health.json"


def load_gate_result(sprint_label: str, sprints_dir: Path) -> Optional[SuiteHealthResult]:
    """Load a previously saved health result; returns None if not found or corrupt."""
    path = _health_json_path(sprint_label, sprints_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return SuiteHealthResult.from_dict(data)
    except Exception:
        return None


def _parse_pytest_output(output: str) -> tuple[int, int, int]:
    """Parse pytest -q stdout for passed/failed/skipped counts."""
    passed = failed = skipped = 0
    m = re.search(r"(\d+)\s+passed", output)
    if m:
        passed = int(m.group(1))
    m = re.search(r"(\d+)\s+failed", output)
    if m:
        failed = int(m.group(1))
    m = re.search(r"(\d+)\s+skipped", output)
    if m:
        skipped = int(m.group(1))
    return passed, failed, skipped


def _append_to_log(sprint_label: str, result: SuiteHealthResult) -> None:
    """Append health result to the structured event log (best-effort)."""
    try:
        from services.logging import log as structured_log
        structured_log.event(
            "suite_health_gate",
            sprint_label=sprint_label,
            suite_health=result.to_dict(),
        )
    except Exception:
        pass


def run_gate(
    sprint_label: str,
    sprints_dir: Path,
    repo_root: Optional[Path] = None,
    timeout_seconds: int = SUITE_HEALTH_TIMEOUT_DEFAULT,
    *,
    _subprocess_result: Optional[Union[subprocess.CompletedProcess, subprocess.TimeoutExpired]] = None,
) -> SuiteHealthResult:
    """Run the full pytest suite and return a SuiteHealthResult.

    Results are written to ``<sprints_dir>/<sprint_label>-suite-health.json``
    and appended to the structured event log.

    ``_subprocess_result`` is a test-only injection point — pass a
    ``subprocess.CompletedProcess`` or ``subprocess.TimeoutExpired`` to skip
    the real subprocess call.
    """
    root = repo_root or Path.cwd()
    tests_dir = root / "tests"

    now = datetime.now(timezone.utc).isoformat()
    timed_out = False
    passed = failed = skipped = 0
    duration = 0.0

    if _subprocess_result is not None:
        # Test injection: use the provided result
        if isinstance(_subprocess_result, subprocess.TimeoutExpired):
            timed_out = True
            duration = float(timeout_seconds)
        else:
            proc = _subprocess_result
            passed, failed, skipped = _parse_pytest_output(proc.stdout or "")
            duration = 0.0  # duration not captured from injected result
    else:
        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "--tb=no", "-q", str(tests_dir)],
                capture_output=True,
                text=True,
                cwd=str(root),
                timeout=timeout_seconds,
            )
            duration = time.monotonic() - t0
            passed, failed, skipped = _parse_pytest_output(proc.stdout or "")
        except subprocess.TimeoutExpired:
            timed_out = True
            duration = float(timeout_seconds)

    result = SuiteHealthResult(
        sprint_label     = sprint_label,
        passed           = passed,
        failed           = failed,
        skipped          = skipped,
        duration_seconds = round(duration, 2),
        timed_out        = timed_out,
        recorded_at      = now,
    )

    sprints_dir.mkdir(parents=True, exist_ok=True)
    path = _health_json_path(sprint_label, sprints_dir)
    path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")

    _append_to_log(sprint_label, result)

    return result
