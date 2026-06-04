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


def load_calibration(
    commander_dir: Optional[Path] = None,
    db_records: Optional[list] = None,
) -> CalibrationResult:
    """Load calibration data and return effective per-size minutes with sources.

    db_records: optional list of {size, actual_minutes[, total_tokens]} dicts
        from the persistent DB store (via sprint_repo.build_calibration_records).
        When provided, these are merged with any file records; DB records take
        precedence over file records for tiers that have enough samples.

    If calibration file is absent or unreadable, all sizes fall back to defaults
    and a warning is added to CalibrationResult.warnings.
    """
    warns: list[str] = []

    # Resolve calibration path
    cal_path = _calibration_path_from_env()
    if cal_path is None and commander_dir is not None:
        cal_path = commander_dir / "calibration.json"

    # Attempt to load records from file
    file_records: list[dict] = []
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
            file_records = data.get("records", [])
            if not isinstance(file_records, list):
                warns.append(
                    f"Calibration file {cal_path} has invalid 'records' field; using generic defaults."
                )
                file_records = []
        except (json.JSONDecodeError, OSError) as exc:
            warns.append(
                f"Could not read calibration file {cal_path}: {exc}; using generic defaults."
            )
            file_records = []
            cal_path = None

    # DB records take priority: use DB buckets first; fill remaining from file.
    db_buckets: dict[str, list[float]] = {s: [] for s in VALID_SIZES}
    for rec in (db_records or []):
        size = rec.get("size", "")
        mins = rec.get("actual_minutes")
        if size in db_buckets and isinstance(mins, (int, float)) and mins > 0:
            db_buckets[size].append(float(mins))

    file_buckets: dict[str, list[float]] = {s: [] for s in VALID_SIZES}
    for rec in file_records:
        size = rec.get("size", "")
        mins = rec.get("actual_minutes")
        if size in file_buckets and isinstance(mins, (int, float)) and mins > 0:
            file_buckets[size].append(float(mins))

    # Merge: for each tier, prefer DB samples when DB has enough; else combine.
    raw_records: list[dict] = list(db_records or []) + file_records
    buckets: dict[str, list[float]] = {s: [] for s in VALID_SIZES}
    for size in VALID_SIZES:
        if len(db_buckets[size]) >= MIN_SAMPLES:
            buckets[size] = db_buckets[size]
        else:
            buckets[size] = db_buckets[size] + file_buckets[size]

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


def db_calibration_records(sprint_label: str, estimates_dir: Path) -> list:
    """Return [{size, actual_minutes, total_tokens}] from DB for a past sprint.

    Reads actual_elapsed_seconds and total_tokens from sprint_tickets, then
    resolves each ticket's size from its estimate JSON in estimates_dir.
    Returns only tickets that have both a DB record and an estimate file.
    Silently returns [] if sprint_repo is unavailable or Neon is disabled.
    """
    try:
        import sys
        _sm_dir = Path(__file__).parent
        if str(_sm_dir.parent.parent) not in sys.path:
            sys.path.insert(0, str(_sm_dir.parent.parent))
        from services.sprint_manager import sprint_repo
    except Exception:
        return []

    try:
        rollup = sprint_repo.get_sprint_rollup(sprint_label)
    except Exception:
        return []

    records = []
    for row in rollup["tickets"]:
        num = row["issue_number"]
        elapsed = row["actual_elapsed_seconds"]
        if elapsed is None:
            continue
        est_path = estimates_dir / f"issue-{num}.json"
        if not est_path.exists():
            continue
        try:
            est = json.loads(est_path.read_text())
            size = est.get("size", "")
        except (json.JSONDecodeError, OSError):
            continue
        if size not in VALID_SIZES:
            continue
        rec: dict = {
            "size": size,
            "actual_minutes": round(elapsed / 60, 1),
        }
        if row.get("total_tokens") is not None:
            rec["total_tokens"] = row["total_tokens"]
        records.append(rec)
    return records
