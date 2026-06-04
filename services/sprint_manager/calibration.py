"""Estimator calibration — load historical task records and derive per-size durations.

Calibration source (in priority order):
  1. ESTIMATOR_CALIBRATION_PATH env var (absolute or relative to CWD)
  2. <commander_dir>/calibration.json (default path when commander_dir is known)

Calibration file format (JSON):
  {
    "records": [
      {"size": "S", "actual_minutes": 3},
      {"size": "M", "actual_minutes": 18},
      ...
    ]
  }

A size tier needs at least MIN_SAMPLES records to use calibrated values.
Tiers with insufficient data fall back to SIZE_TO_MINUTES defaults.
"""
from __future__ import annotations

import json
import os
import statistics
import warnings as _warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sizing import SIZE_TO_MINUTES

MIN_SAMPLES = 3  # minimum records per size tier to use calibrated value
VALID_SIZES = list(SIZE_TO_MINUTES.keys())  # ["S", "M", "L", "XL"]


@dataclass
class CalibrationResult:
    effective_minutes: dict[str, int]
    sources: dict[str, str]          # per size: "calibrated" or "default"
    calibration_path: Optional[Path]  # None if no file was loaded
    warnings: list[str] = field(default_factory=list)
    record_count: int = 0


def _median_int(values: list[float]) -> int:
    """Return median of values rounded to nearest int."""
    return round(statistics.median(values))


def _calibration_path_from_env() -> Optional[Path]:
    raw = os.environ.get("ESTIMATOR_CALIBRATION_PATH", "").strip()
    if not raw:
        return None
    p = Path(raw)
    return p if p.is_absolute() else Path.cwd() / p


def load_calibration(commander_dir: Optional[Path] = None) -> CalibrationResult:
    """Load calibration data and return effective per-size minutes with sources.

    If calibration file is absent or unreadable, all sizes fall back to defaults
    and a warning is added to CalibrationResult.warnings.
    """
    warns: list[str] = []

    # Resolve calibration path
    cal_path = _calibration_path_from_env()
    if cal_path is None and commander_dir is not None:
        cal_path = commander_dir / "calibration.json"

    # Attempt to load records
    raw_records: list[dict] = []
    if cal_path is None:
        warns.append("No calibration path configured; using generic defaults.")
    elif not cal_path.exists():
        warns.append(
            f"Calibration file not found: {cal_path}; using generic defaults."
        )
        cal_path = None
    else:
        try:
            data = json.loads(cal_path.read_text())
            raw_records = data.get("records", [])
            if not isinstance(raw_records, list):
                warns.append(
                    f"Calibration file {cal_path} has invalid 'records' field; using generic defaults."
                )
                raw_records = []
        except (json.JSONDecodeError, OSError) as exc:
            warns.append(
                f"Could not read calibration file {cal_path}: {exc}; using generic defaults."
            )
            raw_records = []
            cal_path = None

    # Group actual_minutes by size
    buckets: dict[str, list[float]] = {s: [] for s in VALID_SIZES}
    for rec in raw_records:
        size = rec.get("size", "")
        mins = rec.get("actual_minutes")
        if size in buckets and isinstance(mins, (int, float)) and mins > 0:
            buckets[size].append(float(mins))

    # Build effective mappings
    effective: dict[str, int] = {}
    sources: dict[str, str] = {}
    for size in VALID_SIZES:
        vals = buckets[size]
        if len(vals) >= MIN_SAMPLES:
            effective[size] = _median_int(vals)
            sources[size] = "calibrated"
        else:
            effective[size] = SIZE_TO_MINUTES[size]
            sources[size] = "default"
            if len(vals) > 0:
                warns.append(
                    f"Size {size}: only {len(vals)} record(s) (need {MIN_SAMPLES}); "
                    f"using default {SIZE_TO_MINUTES[size]} min."
                )

    total_records = sum(len(v) for v in buckets.values())
    return CalibrationResult(
        effective_minutes=effective,
        sources=sources,
        calibration_path=cal_path,
        warnings=warns,
        record_count=total_records,
    )


def calibration_prompt_section(result: CalibrationResult) -> str:
    """Return a markdown section injected into the estimator prompt."""
    if result.record_count == 0:
        source_note = "no historical data — all tiers use generic defaults"
    else:
        n_cal = sum(1 for s in result.sources.values() if s == "calibrated")
        source_note = (
            f"{result.record_count} historical records; "
            f"{n_cal}/{len(VALID_SIZES)} size tiers calibrated"
        )

    rows = "\n".join(
        f"| {size} | {result.effective_minutes[size]} | {result.sources[size]} |"
        for size in VALID_SIZES
    )
    return f"""## Calibration Data ({source_note})

Use the minutes values below when estimating. They replace generic defaults.

| Size | Minutes | Source |
|------|---------|--------|
{rows}
"""
