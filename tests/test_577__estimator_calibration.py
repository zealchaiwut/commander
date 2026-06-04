"""Tests for estimator calibration (issue #577).

Covers:
  - Calibrated path: per-size medians derived from historical data
  - Fallback to default: insufficient records for a tier
  - Empty history: zero records, all defaults
  - Missing calibration file: warning emitted, all defaults
  - Mixed coverage: some tiers calibrated, others default
  - Prompt section injection
  - calibration_sources field in run_estimator output
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Make services/sprint_manager importable
_SM = Path(__file__).parent.parent / "services" / "sprint_manager"
if str(_SM) not in sys.path:
    sys.path.insert(0, str(_SM))

from calibration import (
    MIN_SAMPLES,
    CalibrationResult,
    calibration_prompt_section,
    load_calibration,
)
from sizing import SIZE_TO_MINUTES


# ── fixtures ──────────────────────────────────────────────────────────────────

def _write_calibration(tmp_path: Path, records: list[dict]) -> Path:
    p = tmp_path / "calibration.json"
    p.write_text(json.dumps({"records": records}))
    return p


def _make_records(size: str, minutes_list: list[float]) -> list[dict]:
    return [{"size": size, "actual_minutes": m} for m in minutes_list]


# ── calibrated path ───────────────────────────────────────────────────────────

def test_calibrated_path_uses_median(tmp_path):
    records = (
        _make_records("S", [3, 4, 5])        # median = 4
        + _make_records("M", [10, 14, 18])   # median = 14
        + _make_records("L", [25, 30, 35])   # median = 30
        + _make_records("XL", [50, 60, 70])  # median = 60
    )
    p = _write_calibration(tmp_path, records)

    result = load_calibration(tmp_path)

    assert result.calibration_path == p
    assert result.sources["S"] == "calibrated"
    assert result.sources["M"] == "calibrated"
    assert result.sources["L"] == "calibrated"
    assert result.sources["XL"] == "calibrated"
    assert result.effective_minutes["S"] == 4
    assert result.effective_minutes["M"] == 14
    assert result.effective_minutes["L"] == 30
    assert result.effective_minutes["XL"] == 60
    assert result.record_count == 12
    assert result.warnings == []


def test_calibrated_even_count_uses_median_average(tmp_path):
    # 4 S records: [2, 4, 6, 8] → median = (4+6)/2 = 5 → rounded 5
    records = _make_records("S", [2, 4, 6, 8]) + _make_records("M", [10, 12, 14])
    _write_calibration(tmp_path, records)

    result = load_calibration(tmp_path)
    assert result.sources["S"] == "calibrated"
    assert result.effective_minutes["S"] == 5


# ── fallback to default ───────────────────────────────────────────────────────

def test_fallback_when_insufficient_records(tmp_path):
    # Only 2 S records (below MIN_SAMPLES=3), others not present
    records = _make_records("S", [3, 5])
    _write_calibration(tmp_path, records)

    result = load_calibration(tmp_path)

    assert result.sources["S"] == "default"
    assert result.effective_minutes["S"] == SIZE_TO_MINUTES["S"]
    # Should have a warning about insufficient data
    assert any("S" in w and str(2) in w for w in result.warnings)


def test_fallback_all_defaults_when_size_absent(tmp_path):
    # Only M records, no S/L/XL
    records = _make_records("M", [10, 15, 20])
    _write_calibration(tmp_path, records)

    result = load_calibration(tmp_path)

    assert result.sources["M"] == "calibrated"
    assert result.sources["S"] == "default"
    assert result.sources["L"] == "default"
    assert result.sources["XL"] == "default"
    assert result.effective_minutes["S"] == SIZE_TO_MINUTES["S"]
    assert result.effective_minutes["L"] == SIZE_TO_MINUTES["L"]
    assert result.effective_minutes["XL"] == SIZE_TO_MINUTES["XL"]


# ── empty history ─────────────────────────────────────────────────────────────

def test_empty_records_all_defaults(tmp_path):
    _write_calibration(tmp_path, [])

    result = load_calibration(tmp_path)

    for size in SIZE_TO_MINUTES:
        assert result.sources[size] == "default"
        assert result.effective_minutes[size] == SIZE_TO_MINUTES[size]
    assert result.record_count == 0


def test_no_commander_dir_all_defaults():
    result = load_calibration(commander_dir=None)

    for size in SIZE_TO_MINUTES:
        assert result.sources[size] == "default"
        assert result.effective_minutes[size] == SIZE_TO_MINUTES[size]
    assert result.calibration_path is None
    assert any("No calibration path" in w for w in result.warnings)


# ── missing / unreadable file ──────────────────────────────────────────────────

def test_missing_calibration_file_warns_and_uses_defaults(tmp_path):
    # calibration.json does not exist in tmp_path
    result = load_calibration(commander_dir=tmp_path)

    assert result.calibration_path is None
    for size in SIZE_TO_MINUTES:
        assert result.sources[size] == "default"
        assert result.effective_minutes[size] == SIZE_TO_MINUTES[size]
    assert any("not found" in w for w in result.warnings)


def test_env_var_nonexistent_path_warns(tmp_path, monkeypatch):
    nonexistent = tmp_path / "does_not_exist.json"
    monkeypatch.setenv("ESTIMATOR_CALIBRATION_PATH", str(nonexistent))

    result = load_calibration(commander_dir=tmp_path)

    assert result.calibration_path is None
    assert any("not found" in w for w in result.warnings)
    for size in SIZE_TO_MINUTES:
        assert result.sources[size] == "default"


def test_invalid_json_in_calibration_file_warns(tmp_path):
    bad = tmp_path / "calibration.json"
    bad.write_text("{not valid json")

    result = load_calibration(commander_dir=tmp_path)

    assert result.calibration_path is None
    assert any("Could not read" in w for w in result.warnings)
    for size in SIZE_TO_MINUTES:
        assert result.sources[size] == "default"


# ── env var path override ──────────────────────────────────────────────────────

def test_env_var_overrides_default_path(tmp_path, monkeypatch):
    custom = tmp_path / "custom_cal.json"
    custom.write_text(json.dumps({
        "records": _make_records("M", [10, 12, 14]),
    }))
    monkeypatch.setenv("ESTIMATOR_CALIBRATION_PATH", str(custom))

    result = load_calibration(commander_dir=tmp_path)

    assert result.calibration_path == custom
    assert result.sources["M"] == "calibrated"


# ── prompt section ─────────────────────────────────────────────────────────────

def test_prompt_section_contains_all_sizes(tmp_path):
    records = (
        _make_records("S", [3, 4, 5])
        + _make_records("M", [10, 14, 18])
        + _make_records("L", [25, 30, 35])
        + _make_records("XL", [50, 60, 70])
    )
    _write_calibration(tmp_path, records)
    result = load_calibration(tmp_path)

    section = calibration_prompt_section(result)

    for size in SIZE_TO_MINUTES:
        assert size in section
    assert "calibrated" in section
    assert "Calibration Data" in section


def test_prompt_section_no_data_shows_defaults(tmp_path):
    result = load_calibration(commander_dir=None)
    section = calibration_prompt_section(result)

    assert "no historical data" in section
    assert "default" in section


# ── calibration_sources in run_estimator output ────────────────────────────────

def test_run_estimator_attaches_calibration_sources(tmp_path):
    """run_estimator should add calibration_sources to the parsed dict."""
    import estimate_issue as ei

    records = (
        _make_records("S", [3, 4, 5])
        + _make_records("M", [10, 14, 18])
        + _make_records("L", [25, 30, 35])
        + _make_records("XL", [50, 60, 70])
    )
    _write_calibration(tmp_path, records)
    calibration = load_calibration(tmp_path)

    fake_output = json.dumps({
        "size": "M",
        "minutes": 14,
        "estimated_hours": 0.23,
        "confidence": "high",
        "files_likely_affected": [],
        "risk_flags": [],
        "summary": "test",
    })

    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.stdout = fake_output

    with patch("estimate_issue.subprocess.run", return_value=fake_proc):
        result, err = ei.run_estimator(42, {"title": "Test", "body": "body"}, calibration=calibration)

    assert err is None
    assert result is not None
    assert "calibration_sources" in result
    assert result["calibration_sources"]["M"] == "calibrated"
    assert result["calibration_sources"]["S"] == "calibrated"


def test_run_estimator_no_calibration_no_field(tmp_path):
    """Without calibration, calibration_sources should not appear in output."""
    import estimate_issue as ei

    fake_output = json.dumps({
        "size": "S",
        "minutes": 5,
        "estimated_hours": 0.08,
        "confidence": "high",
        "files_likely_affected": [],
        "risk_flags": [],
        "summary": "trivial",
    })

    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.stdout = fake_output

    with patch("estimate_issue.subprocess.run", return_value=fake_proc):
        result, err = ei.run_estimator(7, {"title": "Test", "body": "body"}, calibration=None)

    assert err is None
    assert result is not None
    assert "calibration_sources" not in result
