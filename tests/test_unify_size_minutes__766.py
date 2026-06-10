"""Tester AC verification for issue #766: Unify size-minutes map, fix XL default,
SQLite calibration fallback.

This is a backend-logic ticket (sizing.py / estimation_config.py / mis_sizing.py /
calibration.py / seed_calibration.py). There are no HTTP endpoints or UI, so these
tests import the modules directly rather than hitting a running server. One test
function per acceptance criterion.

Imports limited to stdlib + pytest per tester conventions.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import sizing
import calibration
import services.sprint_manager.mis_sizing as mis_sizing
import services.sprint_manager.settings_repo as settings_repo
from services.sprint_manager.estimation_config import (
    DEFAULT_ESTIMATION_CFG,
    get_estimation_cfg,
)
import seed_calibration

SM_DIR = REPO_ROOT / "services" / "sprint_manager"
SIZING_SRC = (SM_DIR / "sizing.py").read_text()
ESTIMATOR_MD = (REPO_ROOT / "apps" / "dashboard" / ".claude" / "agents" / "estimator.md").read_text()


# --- AC1: exactly one literal size->minutes map; others import it ---

def test_unify_size_minutes__single_literal_map(client=None):
    # AC: Exactly one literal size->minutes map exists (grep-proven); estimation_config
    # defaults and mis_sizing.CANONICAL_MINUTES import from sizing.py.
    # mis_sizing reuses the exact object from sizing (identity, not a copy).
    assert mis_sizing.CANONICAL_MINUTES is sizing.SIZE_TO_MINUTES
    # estimation_config default inherits the same values.
    assert DEFAULT_ESTIMATION_CFG["size_minutes"] == sizing.SIZE_TO_MINUTES

    # grep-proof: only sizing.py declares the literal `SIZE_TO_MINUTES: dict... = {`.
    pattern = re.compile(r"^SIZE_TO_MINUTES\s*:\s*dict.*=\s*\{", re.MULTILINE)
    declarers = []
    for py in REPO_ROOT.rglob("*.py"):
        parts = set(py.parts)
        if "venv" in parts or "tests" in parts or py.name.startswith("test_"):
            continue
        if pattern.search(py.read_text(errors="ignore")):
            declarers.append(py.relative_to(REPO_ROOT).as_posix())
    assert declarers == ["services/sprint_manager/sizing.py"], declarers


# --- AC2: _minutes_to_size duplication removed; callers use letter_from_minutes ---

def test_unify_size_minutes__minutes_to_size_removed():
    # AC: _minutes_to_size duplication in mis_sizing removed; all callers use
    # sizing.letter_from_minutes.
    assert not hasattr(mis_sizing, "_minutes_to_size")
    mis_src = (SM_DIR / "mis_sizing.py").read_text()
    assert "_minutes_to_size" not in mis_src
    # mis_sizing imports + uses the canonical reverse lookup.
    assert "letter_from_minutes" in mis_src
    assert callable(sizing.letter_from_minutes)
    # sanity: reverse lookup buckets line up with the canonical map.
    assert sizing.letter_from_minutes(0) == "S"
    assert sizing.letter_from_minutes(90) == "XL"


# --- AC3: XL default raised >= 90 with inline comment; config inherits ---

def test_unify_size_minutes__xl_default_raised():
    # AC: XL default raised in sizing.py (>= 90 min) with inline comment citing
    # observed actuals; estimation_config inherits the new value.
    assert sizing.SIZE_TO_MINUTES["XL"] >= 90
    assert DEFAULT_ESTIMATION_CFG["size_minutes"]["XL"] >= 90
    # inline comment near XL cites observed actuals.
    assert re.search(r"XL.*(60.*90|raised).*", SIZING_SRC, re.IGNORECASE)
    assert "observed" in SIZING_SRC.lower()


# --- AC4: semantic documented in sizing.py and estimator prompt ---

def test_unify_size_minutes__pipeline_semantic_documented():
    # AC: sizing.py and the estimator prompt both state the semantic: a size's
    # minutes = full pipeline (coder + tester) wall-clock time for the ticket.
    for src in (SIZING_SRC.lower(), ESTIMATOR_MD.lower()):
        assert "full pipeline" in src or "full-pipeline" in src
        assert "wall-clock" in src or "wall clock" in src
        assert "coder" in src and "tester" in src


# --- AC5: DATABASE_URL unset -> calibration reads from local SQLite ---

def test_unify_size_minutes__sqlite_calibration_fallback(tmp_path, monkeypatch):
    # AC: With DATABASE_URL unset, calibration reads samples from the local SQLite
    # store instead of returning [].
    db = tmp_path / "dashboard.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE ticket_status (issue TEXT, status TEXT, ts TEXT)")
    conn.execute("CREATE TABLE issues (issue_number TEXT, labels TEXT)")
    # Issue 900: size-M, in-progress -> UAT spanning ~20 minutes.
    conn.execute(
        "INSERT INTO issues VALUES (?, ?)",
        ("900", json.dumps([{"name": "size-M"}, {"name": "enhancement"}])),
    )
    conn.execute("INSERT INTO ticket_status VALUES ('900','in-progress','2026-06-01T10:00:00+00:00')")
    conn.execute("INSERT INTO ticket_status VALUES ('900','UAT','2026-06-01T10:20:00+00:00')")
    conn.commit()
    conn.close()

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DB_PATH", str(db))

    records = calibration.sqlite_calibration_records()
    assert records, "expected non-empty SQLite-sourced calibration records"
    rec = records[0]
    assert rec["size"] == "M"
    assert rec["actual_minutes"] == pytest.approx(20.0, abs=0.5)


# --- AC6: fallback emits WARNING with per-tier sample counts ---

def test_unify_size_minutes__fallback_warns_per_tier(tmp_path, caplog):
    # AC: When calibration falls back to file-only or defaults, a WARNING-level log
    # line is emitted listing per-tier sample counts.
    with caplog.at_level("WARNING", logger="commander.calibration"):
        result = calibration.load_calibration(
            commander_dir=tmp_path,  # no calibration.json -> defaults
            db_records=[{"size": "M", "actual_minutes": 18}],  # < MIN_SAMPLES
        )
    warnings_text = "\n".join(r.getMessage() for r in caplog.records)
    assert "Calibration fallback" in warnings_text
    # per-tier counts present for every size tier.
    for tier in ("S=", "M=", "L=", "XL="):
        assert tier in warnings_text, warnings_text
    # M with only 1 sample still falls back to default.
    assert result.sources["M"] == "default"


# --- AC7: per-project XL override beats the new default ---

def test_unify_size_minutes__project_xl_override(monkeypatch):
    # AC: Per-project XL override via settings still functions and takes precedence
    # over the new default.
    def fake_get_setting(key, project=None):
        if key == "estimation" and project == "demo":
            return {"size_minutes": {"XL": 120}}
        return {}

    monkeypatch.setattr(settings_repo, "get_setting", fake_get_setting)
    cfg = get_estimation_cfg(project="demo")
    assert cfg["size_minutes"]["XL"] == 120  # override wins over default 90
    # untouched tiers still inherit canonical defaults.
    assert cfg["size_minutes"]["S"] == sizing.SIZE_TO_MINUTES["S"]


# --- AC8: seed script exists and populates tiers ---

def test_unify_size_minutes__seed_script_populates(tmp_path):
    # AC: A seed script exists so past sprint summaries can populate tiers immediately.
    cal_path = tmp_path / "calibration.json"
    total = seed_calibration.append_records(
        cal_path,
        [{"size": "M", "actual_minutes": 18},
         {"size": "M", "actual_minutes": 16},
         {"size": "M", "actual_minutes": 20}],
    )
    assert total == 3
    data = json.loads(cal_path.read_text())
    assert len(data["records"]) == 3
    # appending is additive, not replacing.
    total2 = seed_calibration.append_records(cal_path, [{"size": "L", "actual_minutes": 31}])
    assert total2 == 4
    # the 3 seeded M samples now activate M calibration (>= MIN_SAMPLES).
    result = calibration.load_calibration(commander_dir=tmp_path)
    assert result.sources["M"] == "calibrated"
