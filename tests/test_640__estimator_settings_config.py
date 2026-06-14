"""Tests for issue #640 — estimator reads per-project config and writes estimated_size.

AC coverage:
  AC(a) — override path: estimation config override produces different minutes value
  AC(b) — no-override path: defaults match legacy hard-coded constants (S=5, M=15, L=30, XL=60)
  AC(c) — estimated_size written to sprint_tickets DB row after estimation
  AC(est-cfg) — get_estimation_cfg returns merged project override
  AC(prompt)  — sprint_estimator prompt text reflects settings values, not hard-coded literals
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))

import services.sprint_manager.settings_repo as settings_repo
import services.sprint_manager.sprint_repo as sprint_repo
from conftest import make_sprint_db
from services.sprint_manager.estimation_config import (
    DEFAULT_ESTIMATION_CFG,
    get_estimation_cfg,
)
from services.sprint_manager.estimate_issue import run_estimator
from services.sprint_manager.sprint_repo import write_estimated_size


# ── shared fixtures ────────────────────────────────────────────────────────────

SEED_ESTIMATION = {
    "size_minutes": {"S": 5, "M": 15, "L": 30, "XL": 60},
    "buffer_pct": 20,
    "thin_ac_buffer_pct": 30,
}


@pytest.fixture()
def settings_db(monkeypatch):
    """In-memory SQLite settings DB seeded with global estimation defaults."""
    engine = create_engine("sqlite:///:memory:")
    make_sprint_db(engine)
    with engine.connect() as conn:
        conn.execute(
            text("INSERT INTO settings (scope, project, key, value) VALUES ('global', NULL, 'estimation', :val)"),
            {"val": json.dumps(SEED_ESTIMATION)},
        )
        conn.commit()
    SessionLocal = sessionmaker(bind=engine)
    monkeypatch.setattr(settings_repo, "_session_factory", SessionLocal)
    yield engine


@pytest.fixture()
def tickets_db(monkeypatch):
    """In-memory SQLite with sprint + sprint_tickets tables."""
    engine = create_engine("sqlite:///:memory:")
    make_sprint_db(engine)
    with engine.connect() as conn:
        conn.execute(text("INSERT INTO sprints (id, label, goal, project) VALUES (1, 'sprint-99', 'test', 'test-proj')"))
        conn.execute(text("INSERT INTO sprint_tickets (sprint_id, issue_number, position) VALUES (1, 101, 0)"))
        conn.commit()
    SessionLocal = sessionmaker(bind=engine)
    monkeypatch.setattr(sprint_repo, "_session_factory", SessionLocal)
    yield engine


def _mock_proc(stdout: str, returncode: int = 0) -> MagicMock:
    p = MagicMock()
    p.returncode = returncode
    p.stdout = stdout
    p.stderr = ""
    return p


# ── AC(est-cfg): get_estimation_cfg ───────────────────────────────────────────

def test_get_estimation_cfg_returns_defaults_when_no_settings(monkeypatch):
    """Falls back to DEFAULT_ESTIMATION_CFG when settings unavailable."""
    import services.sprint_manager.estimation_config as ec
    with patch.object(ec, "_get_setting_safe", return_value={}):
        cfg = get_estimation_cfg()
    assert cfg["size_minutes"] == DEFAULT_ESTIMATION_CFG["size_minutes"]
    assert cfg["buffer_pct"] == 20
    assert cfg["thin_ac_buffer_pct"] == 30


def test_get_estimation_cfg_returns_seeded_global(settings_db):
    """Returns seeded global defaults from DB."""
    cfg = get_estimation_cfg()
    assert cfg["size_minutes"] == {"S": 5, "M": 15, "L": 30, "XL": 60}
    assert cfg["buffer_pct"] == 20


def test_get_estimation_cfg_merges_project_override(settings_db):
    """Project override merges on top of global."""
    settings_repo.set_setting("project", "estimation", {"size_minutes": {"S": 5, "M": 90, "L": 30, "XL": 60}}, project="proj-override")
    cfg = get_estimation_cfg(project="proj-override")
    assert cfg["size_minutes"]["M"] == 90
    assert cfg["buffer_pct"] == 20  # falls back to global


def test_get_estimation_cfg_no_override_returns_global(settings_db):
    """Project with no override returns global values."""
    cfg = get_estimation_cfg(project="proj-no-override")
    assert cfg["size_minutes"]["M"] == 15


# ── AC(b): no-override path matches legacy ────────────────────────────────────

def test_defaults_inherit_canonical_sizing_map():
    """AC(b), updated by #766 — defaults inherit the single sizing.py map.

    XL was raised 60→90 in issue #766 (observed actuals); the config default now
    inherits that value rather than redeclaring the legacy XL=60 constant.
    """
    from sizing import SIZE_TO_MINUTES
    assert DEFAULT_ESTIMATION_CFG["size_minutes"] == SIZE_TO_MINUTES
    assert DEFAULT_ESTIMATION_CFG["size_minutes"]["XL"] == 90
    assert DEFAULT_ESTIMATION_CFG["buffer_pct"] == 20
    assert DEFAULT_ESTIMATION_CFG["thin_ac_buffer_pct"] == 30


def test_run_estimator_no_override_uses_legacy_minutes(settings_db):
    """AC(b) — without project override, M size → 15 min (legacy)."""
    payload = {
        "size": "M",
        "estimated_hours": 0.25,
        "confidence": "high",
        "files_likely_affected": [],
        "depends_on": [],
        "blocks": [],
        "risk_flags": [],
        "summary": "A medium task.",
    }
    issue_data = {"title": "Test", "body": "Body."}

    with patch("services.sprint_manager.estimate_issue.subprocess.run") as mock_run, \
         patch("services.sprint_manager.estimate_issue.load_agent_instructions", return_value=""):
        mock_run.return_value = _mock_proc(json.dumps(payload))
        estimate, error = run_estimator(1, issue_data)

    assert estimate is not None
    assert estimate["minutes"] == 15


# ── AC(a): override path produces different estimate ──────────────────────────

def test_run_estimator_override_changes_minutes(settings_db):
    """AC(a) — M=90 min override makes M-sized ticket cost 90 minutes."""
    settings_repo.set_setting(
        "project", "estimation",
        {"size_minutes": {"S": 5, "M": 90, "L": 30, "XL": 60}},
        project="proj-override",
    )
    payload = {
        "size": "M",
        "estimated_hours": 1.5,
        "confidence": "high",
        "files_likely_affected": [],
        "depends_on": [],
        "blocks": [],
        "risk_flags": [],
        "summary": "A medium task with override.",
    }
    issue_data = {"title": "Test override", "body": "Body."}

    with patch("services.sprint_manager.estimate_issue.subprocess.run") as mock_run, \
         patch("services.sprint_manager.estimate_issue.load_agent_instructions", return_value=""):
        mock_run.return_value = _mock_proc(json.dumps(payload))
        estimate, error = run_estimator(1, issue_data, project="proj-override")

    assert estimate is not None
    assert estimate["minutes"] == 90, f"Expected 90 (override), got {estimate['minutes']}"


def test_run_estimator_override_vs_default_differ(settings_db):
    """AC(a) — override and non-override produce different minute values."""
    settings_repo.set_setting(
        "project", "estimation",
        {"size_minutes": {"S": 5, "M": 90, "L": 30, "XL": 60}},
        project="proj-with-override",
    )
    payload = {"size": "M", "estimated_hours": 1, "confidence": "high",
               "files_likely_affected": [], "depends_on": [], "blocks": [],
               "risk_flags": [], "summary": "Task."}
    issue_data = {"title": "T", "body": "B."}

    with patch("services.sprint_manager.estimate_issue.subprocess.run") as mock_run, \
         patch("services.sprint_manager.estimate_issue.load_agent_instructions", return_value=""):
        mock_run.return_value = _mock_proc(json.dumps(payload))
        est_override, _ = run_estimator(1, issue_data, project="proj-with-override")

    with patch("services.sprint_manager.estimate_issue.subprocess.run") as mock_run, \
         patch("services.sprint_manager.estimate_issue.load_agent_instructions", return_value=""):
        mock_run.return_value = _mock_proc(json.dumps(payload))
        est_default, _ = run_estimator(1, issue_data)

    assert est_override["minutes"] != est_default["minutes"]
    assert est_override["minutes"] == 90
    assert est_default["minutes"] == 15


# ── AC(c): estimated_size written to DB row ────────────────────────────────────

def test_write_estimated_size_sets_column(tickets_db):
    """AC(c) — write_estimated_size updates sprint_tickets row."""
    write_estimated_size(issue_number=101, size="M")
    with tickets_db.connect() as conn:
        row = conn.execute(
            text("SELECT estimated_size FROM sprint_tickets WHERE issue_number = 101")
        ).fetchone()
    assert row is not None
    assert row[0] == "M"


def test_write_estimated_size_noop_on_missing_issue(tickets_db):
    """AC(c) — write_estimated_size is a no-op when issue not in any sprint."""
    write_estimated_size(issue_number=9999, size="XL")  # should not raise


def test_write_estimated_size_updates_across_all_sprints(tickets_db):
    """AC(c) — write updates all matching rows regardless of sprint."""
    with tickets_db.connect() as conn:
        conn.execute(text("INSERT INTO sprints (id, label, goal, project) VALUES (2, 'sprint-100', 'g', 'p')"))
        conn.execute(text("INSERT INTO sprint_tickets (sprint_id, issue_number, position) VALUES (2, 101, 0)"))
        conn.commit()

    write_estimated_size(issue_number=101, size="L")

    with tickets_db.connect() as conn:
        rows = conn.execute(
            text("SELECT estimated_size FROM sprint_tickets WHERE issue_number = 101")
        ).fetchall()
    assert all(r[0] == "L" for r in rows)
    assert len(rows) == 2


def test_run_estimator_writes_estimated_size_to_db(tickets_db, settings_db):
    """AC(c) — full flow: run_estimator persists estimated_size on sprint_tickets."""
    payload = {"size": "L", "estimated_hours": 0.5, "confidence": "medium",
               "files_likely_affected": [], "depends_on": [], "blocks": [],
               "risk_flags": [], "summary": "Large task."}
    issue_data = {"title": "T", "body": "B."}

    with patch("services.sprint_manager.estimate_issue.subprocess.run") as mock_run, \
         patch("services.sprint_manager.estimate_issue.load_agent_instructions", return_value=""), \
         patch("services.sprint_manager.estimate_issue.sprint_repo") as mock_repo:
        mock_run.return_value = _mock_proc(json.dumps(payload))
        mock_repo.write_estimated_size = MagicMock()
        estimate, _ = run_estimator(101, issue_data)

    assert estimate is not None
    mock_repo.write_estimated_size.assert_called_once_with(issue_number=101, size="L")


# ── AC(prompt): sprint_estimator prompt uses settings values ──────────────────

def test_sprint_estimator_prompt_uses_settings_buffer(settings_db):
    """sprint_estimator builds prompt text from settings, not hard-coded literals."""
    import scripts.sprint_estimator as se  # type: ignore[import]

    settings_repo.set_setting(
        "project", "estimation",
        {"buffer_pct": 25, "thin_ac_buffer_pct": 35,
         "size_minutes": {"S": 5, "M": 15, "L": 30, "XL": 60}},
        project="proj-buf",
    )
    from services.sprint_manager.estimation_config import get_estimation_cfg
    cfg = get_estimation_cfg(project="proj-buf")
    prompt = se.build_estimator_prompt(
        sprint_label="sprint-99",
        repo="owner/repo",
        issues_json="[]",
        estimation_cfg=cfg,
    )
    assert "25%" in prompt or "25" in prompt, "Buffer pct from settings must appear in prompt"
    assert "35%" in prompt or "35" in prompt, "Thin-AC buffer from settings must appear in prompt"


def test_sprint_estimator_prompt_uses_settings_size_minutes(settings_db):
    """sprint_estimator size→minutes table in prompt reflects settings values."""
    import scripts.sprint_estimator as se  # type: ignore[import]

    settings_repo.set_setting(
        "project", "estimation",
        {"buffer_pct": 20, "thin_ac_buffer_pct": 30,
         "size_minutes": {"S": 5, "M": 90, "L": 30, "XL": 60}},
        project="proj-sizes",
    )
    from services.sprint_manager.estimation_config import get_estimation_cfg
    cfg = get_estimation_cfg(project="proj-sizes")
    prompt = se.build_estimator_prompt(
        sprint_label="sprint-99",
        repo="owner/repo",
        issues_json="[]",
        estimation_cfg=cfg,
    )
    assert "90" in prompt, "M=90 override must appear in prompt"
