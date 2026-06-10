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
import logging
import os
import sqlite3
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sizing import SIZE_TO_MINUTES

logger = logging.getLogger("commander.calibration")

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
        from a past sprint's local state files (via db_calibration_records).
        When provided, these are merged with any file records; these records take
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

    # AC#6 (issue #766): silence is impossible. Whenever the result is not fully
    # DB-calibrated — i.e. it falls back to file records or to generic defaults
    # for ANY tier — emit a single WARNING listing per-tier sample counts so the
    # operator can see exactly why calibration was thin.
    fully_db_calibrated = all(
        len(db_buckets[size]) >= MIN_SAMPLES for size in VALID_SIZES
    )
    if not fully_db_calibrated:
        per_tier = " ".join(f"{size}={len(buckets[size])}" for size in VALID_SIZES)
        default_tiers = [size for size in VALID_SIZES if sources[size] == "default"]
        logger.warning(
            "Calibration fallback (file-only or defaults): per-tier sample counts "
            "%s; tiers using default: %s",
            per_tier,
            ", ".join(default_tiers) if default_tiers else "none",
        )

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


def _elapsed_minutes(start: Optional[str], end: Optional[str]) -> Optional[float]:
    """Minutes between two ISO timestamps, or None if either is missing/unparseable."""
    if not start or not end:
        return None
    from datetime import datetime
    try:
        s = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        e = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return (e - s).total_seconds() / 60.0


def db_calibration_records(
    sprint_label: str,
    estimates_dir: Path,
    include_agents: bool = False,
) -> list:
    """Return [{size, actual_minutes}] for a past sprint, read from local files.

    Issue #758 removed the Neon dependency — Neon is an optional export target,
    not a live data source. Reads the local sprint state JSON
    (<sprints>/<sprint_label>-state.json) for per-ticket coder+tester elapsed
    time, then resolves each ticket's size from its estimate JSON in
    estimates_dir. Returns only `done` tickets that have both timing and an
    estimate file. Silently returns [] if the state file is missing.

    Per-agent records (issue #764): when ``include_agents`` is True, the result
    additionally carries one ``{size, actual_minutes, agent}`` record per agent
    (coder, tester) alongside the existing blended record (which omits the
    ``agent`` key). The default (``include_agents=False``) is unchanged, so
    existing consumers that omit ``agent`` keep working without modification —
    they continue to receive blended-only records and never see the per-agent
    rows, so calibration medians are not double-counted.
    """
    sprints_dir = estimates_dir.parent / "sprints"
    state_path = sprints_dir / f"{sprint_label}-state.json"
    if not state_path.exists():
        return []
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    records = []
    for issue in state.get("issues", []):
        if issue.get("status") != "done":
            continue
        num = issue.get("number")
        if num is None:
            continue
        coder_min = _elapsed_minutes(issue.get("coder_started_at"), issue.get("coder_finished_at"))
        tester_min = _elapsed_minutes(issue.get("tester_started_at"), issue.get("tester_finished_at"))
        if coder_min is None and tester_min is None:
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
            "actual_minutes": round((coder_min or 0.0) + (tester_min or 0.0), 1),
        }
        records.append(rec)
        if include_agents:
            if coder_min is not None:
                records.append(
                    {"size": size, "actual_minutes": round(coder_min, 1), "agent": "coder"}
                )
            if tester_min is not None:
                records.append(
                    {"size": size, "actual_minutes": round(tester_min, 1), "agent": "tester"}
                )
    return records


def _size_from_labels(labels_json: Optional[str]) -> Optional[str]:
    """Parse a size-<X> label out of an issues-mirror JSON labels blob."""
    if not labels_json:
        return None
    try:
        labels = json.loads(labels_json)
    except (json.JSONDecodeError, TypeError):
        return None
    for lbl in labels:
        name = lbl.get("name") if isinstance(lbl, dict) else lbl
        if isinstance(name, str) and name.lower().startswith("size-"):
            size = name.split("-", 1)[1].upper()
            if size in VALID_SIZES:
                return size
    return None


def sqlite_calibration_records(db_path: Optional[Path] = None) -> list:
    """Read calibration samples from the local SQLite store (issue #766).

    This is the Neon-independent calibration source: when ``DATABASE_URL`` is
    unset the Neon-backed sprint metrics are unavailable, but the dashboard's
    local SQLite store (``DB_PATH``) still has the data we need. Per-issue
    full-pipeline wall-clock minutes are derived from the ``ticket_status``
    transition log (first ``in-progress`` → first ``UAT``) and each issue's size
    is resolved from the ``issues`` mirror's ``size-<X>`` label.

    Returns ``[{size, actual_minutes}]``. Returns ``[]`` (never raises) when the
    DB file or its tables are absent, so callers fall through to file/defaults.
    """
    if db_path is None:
        raw = os.environ.get("DB_PATH", "").strip()
        if not raw:
            return []
        db_path = Path(raw)
    if not Path(db_path).exists():
        return []

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.OperationalError:
        return []

    try:
        try:
            status_rows = conn.execute(
                "SELECT issue, status, ts FROM ticket_status"
            ).fetchall()
            issue_rows = conn.execute(
                "SELECT issue_number, labels FROM issues"
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    finally:
        conn.close()

    sizes: dict[str, str] = {}
    for row in issue_rows:
        size = _size_from_labels(row["labels"])
        if size:
            sizes[str(row["issue_number"])] = size

    starts: dict[str, str] = {}
    ends: dict[str, str] = {}
    for row in status_rows:
        issue = str(row["issue"])
        status = (row["status"] or "").lower()
        ts = row["ts"]
        if not ts:
            continue
        if status == "in-progress":
            if issue not in starts or ts < starts[issue]:
                starts[issue] = ts
        elif status == "uat":
            if issue not in ends or ts < ends[issue]:
                ends[issue] = ts

    records: list = []
    for issue, start in starts.items():
        end = ends.get(issue)
        size = sizes.get(issue)
        if end is None or size is None:
            continue
        minutes = _elapsed_minutes(start, end)
        if minutes is None or minutes <= 0:
            continue
        records.append({"size": size, "actual_minutes": round(minutes, 1)})
    return records
