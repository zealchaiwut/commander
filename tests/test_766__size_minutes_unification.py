"""Tests for issue #766 — unify size→minutes map, XL default, SQLite calibration.

AC coverage:
  AC1 — exactly one literal size→minutes map (sizing.py); others import it
  AC2 — mis_sizing._minutes_to_size removed; callers use sizing.letter_from_minutes
  AC3 — XL default >= 90 with inline comment citing observed actuals; config inherits
  AC4 — sizing.py and estimator prompt state the full-pipeline wall-clock semantic
  AC5 — DATABASE_URL unset: calibration reads samples from the local SQLite store
  AC6 — fallback to file-only/defaults emits a WARNING with per-tier sample counts
  AC7 — per-project XL override still functions and beats the new default
  AC8 — a seed script exists so past sprints can populate tiers immediately
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))

import sizing
import calibration
import services.sprint_manager.mis_sizing as mis_sizing
import services.sprint_manager.settings_repo as settings_repo
from services.sprint_manager.estimation_config import (
    DEFAULT_ESTIMATION_CFG,
    get_estimation_cfg,
)

SIZING_SRC = (REPO_ROOT / "services" / "sprint_manager" / "sizing.py").read_text()
ESTCFG_SRC = (REPO_ROOT / "services" / "sprint_manager" / "estimation_config.py").read_text()
MISSIZING_SRC = (REPO_ROOT / "services" / "sprint_manager" / "mis_sizing.py").read_text()
PROMPT_SRC = (REPO_ROOT / "apps" / "dashboard" / ".claude" / "agents" / "estimator.md").read_text()

# A literal size→minutes map opens with "S": 5 and ends at "XL": <minutes>.
# Anchoring on "S": 5 distinguishes the minutes map from the tier-ordering map
# (SIZE_TIERS, which maps "S": 1 … "XL": 4).
_LITERAL_MAP_RE = re.compile(r'\{\s*"S"\s*:\s*5\b.*?"XL"\s*:\s*\d+\s*,?\s*\}', re.DOTALL)


# ── AC1: exactly one literal map ──────────────────────────────────────────────

def test_sizing_is_the_one_literal_map():
    assert _LITERAL_MAP_RE.search(SIZING_SRC), "sizing.py must define the literal map"


def test_estimation_config_has_no_literal_map():
    assert not _LITERAL_MAP_RE.search(ESTCFG_SRC), (
        "estimation_config.py must not redeclare the literal size→minutes map"
    )


def test_mis_sizing_has_no_literal_map():
    assert not _LITERAL_MAP_RE.search(MISSIZING_SRC), (
        "mis_sizing.py must not redeclare the literal size→minutes map"
    )


def test_config_defaults_come_from_sizing():
    assert DEFAULT_ESTIMATION_CFG["size_minutes"] == sizing.SIZE_TO_MINUTES


def test_canonical_minutes_comes_from_sizing():
    assert mis_sizing.CANONICAL_MINUTES == sizing.SIZE_TO_MINUTES


# ── AC2: _minutes_to_size removed; callers use letter_from_minutes ─────────────

def test_minutes_to_size_removed():
    assert not hasattr(mis_sizing, "_minutes_to_size"), (
        "mis_sizing._minutes_to_size must be removed in favour of sizing.letter_from_minutes"
    )


def test_build_history_uses_unified_letter_from_minutes(monkeypatch):
    """A ~75-minute ticket is L under the unified XL=90 boundary, not XL."""
    rec = {
        "number": 1,
        "sprint": "s1",
        "estimated_size": "M",
        "coder_started_at": "2026-01-01T00:00:00+00:00",
        "tester_finished_at": "2026-01-01T01:15:00+00:00",  # 75 min
        "labels": ["enhancement", "area-foo"],
    }
    hist = mis_sizing.build_history_from_completed([rec])
    events = hist["events_by_label"]["area-foo"]
    assert events[0]["actual_size"] == sizing.letter_from_minutes(75)
    assert events[0]["actual_size"] == "L"


# ── AC3: XL default >= 90 with comment citing actuals ─────────────────────────

def test_xl_default_raised():
    assert sizing.SIZE_TO_MINUTES["XL"] >= 90


def test_xl_bucket_threshold_raised():
    assert sizing.letter_from_minutes(89) == "L"
    assert sizing.letter_from_minutes(90) == "XL"


def test_config_inherits_raised_xl():
    assert DEFAULT_ESTIMATION_CFG["size_minutes"]["XL"] >= 90


def test_sizing_has_inline_comment_citing_actuals():
    # An inline comment near the XL value referencing observed actuals.
    assert re.search(r"#.*actual", SIZING_SRC, re.IGNORECASE), (
        "sizing.py must carry an inline comment citing observed actuals for XL"
    )


# ── AC4: full-pipeline wall-clock semantic documented ─────────────────────────

def test_sizing_states_pipeline_semantic():
    low = SIZING_SRC.lower()
    assert "pipeline" in low and "wall" in low


def test_prompt_states_pipeline_semantic():
    low = PROMPT_SRC.lower()
    assert "pipeline" in low and "wall" in low


# ── AC5: SQLite calibration source ────────────────────────────────────────────

def _make_local_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE ticket_status (id INTEGER PRIMARY KEY, issue TEXT, "
        "status TEXT, actor TEXT, note TEXT, ts TEXT)"
    )
    conn.execute(
        "CREATE TABLE issues (repo TEXT, issue_number INTEGER, title TEXT, "
        "state TEXT, labels TEXT, updated_at TEXT, raw TEXT)"
    )
    # Three M tickets, each ~12 min in-progress → UAT (full pipeline)
    rows = [
        (10, "2026-01-01T00:00:00", "2026-01-01T00:12:00"),
        (11, "2026-01-01T00:00:00", "2026-01-01T00:14:00"),
        (12, "2026-01-01T00:00:00", "2026-01-01T00:10:00"),
    ]
    for num, start, end in rows:
        conn.execute(
            "INSERT INTO ticket_status (issue, status, actor, ts) VALUES (?,?,?,?)",
            (str(num), "in-progress", "coder", start),
        )
        conn.execute(
            "INSERT INTO ticket_status (issue, status, actor, ts) VALUES (?,?,?,?)",
            (str(num), "UAT", "tester", end),
        )
        conn.execute(
            "INSERT INTO issues (repo, issue_number, labels) VALUES (?,?,?)",
            ("o/r", num, json.dumps([{"name": "size-M"}, {"name": "enhancement"}])),
        )
    conn.commit()
    conn.close()


def test_sqlite_calibration_records_reads_local_store(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db = tmp_path / "commander.db"
    _make_local_db(db)
    recs = calibration.sqlite_calibration_records(db_path=db)
    assert len(recs) == 3
    assert all(r["size"] == "M" for r in recs)
    assert all(r["actual_minutes"] > 0 for r in recs)


def test_sqlite_calibration_missing_db_returns_empty(tmp_path):
    recs = calibration.sqlite_calibration_records(db_path=tmp_path / "nope.db")
    assert recs == []


def test_calibration_uses_sqlite_records_instead_of_empty(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db = tmp_path / "commander.db"
    _make_local_db(db)
    recs = calibration.sqlite_calibration_records(db_path=db)
    result = calibration.load_calibration(db_records=recs)
    assert result.sources["M"] == "calibrated"
    assert result.record_count >= 3


# ── AC6: WARNING with per-tier sample counts on fallback ──────────────────────

def test_fallback_emits_warning_with_per_tier_counts(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        calibration.load_calibration(commander_dir=tmp_path)
    warnings_text = "\n".join(
        r.getMessage() for r in caplog.records if r.levelno == logging.WARNING
    )
    assert warnings_text, "a WARNING log line must be emitted on fallback"
    # per-tier sample counts present, e.g. S=0 M=0 L=0 XL=0
    for size in ("S", "M", "L", "XL"):
        assert re.search(rf"{size}=\d+", warnings_text), f"missing per-tier count for {size}"


def test_no_warning_when_fully_db_calibrated(tmp_path, caplog):
    recs = (
        [{"size": s, "actual_minutes": m} for s in ("S", "M", "L", "XL") for m in (1, 2, 3)]
    )
    with caplog.at_level(logging.WARNING):
        calibration.load_calibration(db_records=recs)
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert not warns, "fully DB-calibrated load must not emit a fallback WARNING"


# ── AC7: per-project XL override beats new default ────────────────────────────

@pytest.fixture()
def settings_db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL,
                project TEXT,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(scope, project, key)
            )
        """))
        conn.commit()
    monkeypatch.setattr(settings_repo, "_session_factory", sessionmaker(bind=engine))
    yield engine


def test_project_xl_override_takes_precedence(settings_db):
    settings_repo.set_setting(
        "project", "estimation",
        {"size_minutes": {"XL": 120}},
        project="proj-xl",
    )
    cfg = get_estimation_cfg(project="proj-xl")
    assert cfg["size_minutes"]["XL"] == 120, "override must beat the global default"
    # other tiers still inherit the unified defaults
    assert cfg["size_minutes"]["M"] == sizing.SIZE_TO_MINUTES["M"]


def test_no_override_uses_new_default(settings_db):
    cfg = get_estimation_cfg(project="proj-none")
    assert cfg["size_minutes"]["XL"] == sizing.SIZE_TO_MINUTES["XL"]


# ── AC8: seed script populates tiers ──────────────────────────────────────────

def test_seed_script_exists():
    assert (REPO_ROOT / "scripts" / "seed_calibration.py").exists()


def test_seed_appends_and_calibration_reflects(tmp_path):
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import seed_calibration

    cal = tmp_path / "calibration.json"
    cal.write_text(json.dumps({"records": [{"size": "S", "actual_minutes": 4}]}))

    n_before = len(json.loads(cal.read_text())["records"])
    seed_calibration.append_records(
        cal, [{"size": "L", "actual_minutes": 33}] * 3
    )
    data = json.loads(cal.read_text())
    assert len(data["records"]) == n_before + 3

    result = calibration.load_calibration(commander_dir=tmp_path)
    assert result.sources["L"] == "calibrated"
    assert result.effective_minutes["L"] == 33
