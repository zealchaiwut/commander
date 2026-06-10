#!/usr/bin/env python3
"""Seed estimator calibration tiers from past data (issue #766).

Appends calibration records to a project's ``calibration.json`` so historical
sprint data can populate the estimator's per-tier samples immediately, without
waiting for new sprints to accumulate.

Calibration file format (created if absent):
  {"records": [{"size": "M", "actual_minutes": 18}, ...]}

Usage:
  # Append explicit records (repeatable --record SIZE:MINUTES):
  python3 scripts/seed_calibration.py --commander-dir .commander \
      --record M:18 --record L:33 --record L:29

  # Pull full-pipeline samples from a finished sprint's local state file:
  python3 scripts/seed_calibration.py --commander-dir .commander \
      --from-sprint sprint-57

A size tier needs MIN_SAMPLES (3) records before the estimator uses calibrated
values; append at least that many per tier to activate it.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "services" / "sprint_manager"))

VALID_SIZES = {"S", "M", "L", "XL"}


def _read_calibration(path: Path) -> dict:
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("records"), list):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {"records": []}


def append_records(calibration_path: Path, records: list[dict]) -> int:
    """Append *records* to the calibration.json at *calibration_path*.

    Each record must be ``{"size": <S|M|L|XL>, "actual_minutes": <number>}``.
    Returns the new total record count. Creates the file (and parents) if absent.
    """
    valid: list[dict] = []
    for rec in records:
        size = rec.get("size")
        mins = rec.get("actual_minutes")
        if size in VALID_SIZES and isinstance(mins, (int, float)) and mins > 0:
            valid.append({"size": size, "actual_minutes": round(float(mins), 1)})
        else:
            raise ValueError(f"Invalid calibration record: {rec!r}")

    data = _read_calibration(calibration_path)
    data["records"].extend(valid)
    calibration_path.parent.mkdir(parents=True, exist_ok=True)
    calibration_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return len(data["records"])


def _parse_record(spec: str) -> dict:
    if ":" not in spec:
        raise argparse.ArgumentTypeError(f"--record must be SIZE:MINUTES, got {spec!r}")
    size, _, mins = spec.partition(":")
    size = size.strip().upper()
    try:
        minutes = float(mins)
    except ValueError:
        raise argparse.ArgumentTypeError(f"minutes must be a number, got {mins!r}")
    if size not in VALID_SIZES:
        raise argparse.ArgumentTypeError(f"size must be one of {sorted(VALID_SIZES)}")
    return {"size": size, "actual_minutes": minutes}


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed estimator calibration tiers.")
    parser.add_argument(
        "--commander-dir", required=True,
        help="Path to the .commander directory (calibration.json lives here).",
    )
    parser.add_argument(
        "--record", action="append", type=_parse_record, default=[],
        metavar="SIZE:MINUTES", help="Append a record, e.g. M:18. Repeatable.",
    )
    parser.add_argument(
        "--from-sprint", default=None,
        help="Pull full-pipeline samples from <commander-dir>/sprints/<label>-state.json.",
    )
    args = parser.parse_args()

    commander_dir = Path(args.commander_dir)
    cal_path = commander_dir / "calibration.json"

    records: list[dict] = list(args.record)

    if args.from_sprint:
        from calibration import db_calibration_records  # noqa: PLC0415
        estimates_dir = commander_dir / "estimates"
        sprint_recs = db_calibration_records(args.from_sprint, estimates_dir)
        if not sprint_recs:
            print(
                f"Warning: no records found for sprint {args.from_sprint!r} "
                f"(missing state/estimate files?)",
                file=sys.stderr,
            )
        records.extend({"size": r["size"], "actual_minutes": r["actual_minutes"]} for r in sprint_recs)

    if not records:
        print("Error: nothing to seed — pass --record and/or --from-sprint", file=sys.stderr)
        return 1

    total = append_records(cal_path, records)
    print(f"Appended {len(records)} record(s); {cal_path} now has {total} total.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
