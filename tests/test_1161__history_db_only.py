"""TDD tests for issue #1161: Remove disk-read fallbacks — history reader.

Acceptance criteria covered:
- AC4: File-only sprints (no DB row, only disk state file) must NOT appear in
  GET /api/sprints/history responses.
- AC4: _read_state_file is not called in _fill_missing_links for DB-backed rows.
- AC6 (regression): History endpoint returns data for DB-backed ingested rows
  without needing a disk state file.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"
_ROUTERS_DIR = _DASHBOARD_DIR / "routers"

for _p in (str(_REPO_ROOT), str(_DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import db  # noqa: E402

_PROJECT = "owner/repo"


def _load_svc():
    """Load sprint_history_service with a stub routers package."""
    if "routers" not in sys.modules:
        stub = types.ModuleType("routers")
        stub.__path__ = [str(_ROUTERS_DIR)]
        sys.modules["routers"] = stub
    mod_name = "routers.sprint_history_service"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, _ROUTERS_DIR / "sprint_history_service.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path):
    db_file = tmp_path / "test_1161_history.db"
    original = db.DB_PATH
    db.DB_PATH = str(db_file)
    db.init_db()
    yield tmp_path
    db.DB_PATH = original


def _make_state_dict(label, issues=None):
    return {
        "sprint_label": label,
        "wall_clock_secs": 240,
        "issues": issues or [
            {"number": 400, "title": "t1", "status": "done", "agent_status": "completed",
             "failure_reason": None, "state": "merged"},
        ],
    }


# ── AC4: file-only sprints excluded ──────────────────────────────────────────

class TestHistoryFileOnlySprintsExcluded:
    """Sprints that only exist on disk (no DB row) must not appear in history.

    Before the fix, _record_from_files feeds disk-only sprints into the history
    response.  After the fix, history is sourced exclusively from DB rows.
    These tests are RED before the fix and GREEN after.
    """

    def test_file_only_sprint_not_in_history(self, tmp_path):
        """Disk state file with no DB row → sprint absent from history results."""
        svc = _load_svc()

        label = "sprint-70"
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        state = _make_state_dict(label)
        (sprints_dir / f"{label}-state.json").write_text(
            json.dumps(state), encoding="utf-8"
        )

        result = svc.get_sprint_history(sprints_dir=sprints_dir, project=_PROJECT)
        labels = [r.get("label") for r in result.get("sprints", [])]

        assert label not in labels, (
            f"File-only sprint {label!r} must NOT appear in history (disk reads removed); "
            f"found in {labels}"
        )

    def test_db_backed_sprint_still_in_history(self, tmp_path):
        """DB row with run_ingested_at → sprint IS in history (regression)."""
        svc = _load_svc()

        label = "sprint-71"
        db.record_sprint_start(label, project=_PROJECT)
        state = _make_state_dict(label)
        db.ingest_sprint_run_artifact(label, state, project=_PROJECT)
        db.record_sprint_finish(label)

        sprints_dir = tmp_path / "sprints_empty"
        sprints_dir.mkdir()

        result = svc.get_sprint_history(sprints_dir=sprints_dir, project=_PROJECT)
        labels = [r.get("label") for r in result.get("sprints", [])]

        assert label in labels, (
            f"DB-backed sprint {label!r} must appear in history; "
            f"got labels={labels}"
        )


# ── AC4: no disk read in _fill_missing_links for DB-backed rows ──────────────

class TestHistoryFillLinksNoDiskRead:
    """_fill_missing_links must not call _read_state_file / _enrich_from_state.

    After the fix, PR numbers and summary URLs are sourced from DB columns and
    the events table — not from on-disk state files.
    These tests are RED before the fix and GREEN after.
    """

    def test_fill_missing_links_does_not_call_enrich_from_state(self, tmp_path):
        """_fill_missing_links must not call _enrich_from_state for any record."""
        svc = _load_svc()

        label = "sprint-72"
        sprints_dir = tmp_path / "sprints_fill"
        sprints_dir.mkdir()

        records = [
            {
                "label": label,
                "project": _PROJECT,
                "pr_number": None,
                "summary_issue_url": None,
                "summary_issue_num": None,
                "summary_path": None,
                "issues": [],
            }
        ]

        called = []

        def _no_disk(*args, **kwargs):
            called.append(args)
            return {}

        with patch.object(svc, "_enrich_from_state", side_effect=_no_disk):
            svc._fill_missing_links(records, sprints_dir)

        assert called == [], (
            "_fill_missing_links must not call _enrich_from_state after disk reads removed; "
            f"called with: {called}"
        )


# ── AC4: no disk read in _finalize_records for ingested rows ─────────────────

class TestHistoryFinalizeNoDiskRead:
    """_finalize_records must not call _read_state_file for any record.

    After the fix, post_sprint data comes exclusively from the DB row's
    post_sprint_json column — not from reading a disk state file.
    """

    def test_finalize_records_does_not_call_read_state_file(self, tmp_path):
        """_finalize_records must not call _read_state_file for any record."""
        svc = _load_svc()

        label = "sprint-73"
        sprints_dir = tmp_path / "sprints_fin"
        sprints_dir.mkdir()

        records = [
            {
                "label": label,
                "project": _PROJECT,
                "lifecycle_state": "completed",
                "has_rerun_child": False,
                "post_sprint": None,
                "issues": [],
                "failed_tickets": [],
                "failure_reason": None,
                "plan_status": None,
                "end_reason": None,
            }
        ]

        called = []

        def _no_disk(*args, **kwargs):
            called.append(args)
            return None

        with patch.object(svc, "_read_state_file", side_effect=_no_disk), \
             patch.object(svc, "_fill_missing_links", return_value=None):
            svc._finalize_records(records, sprints_dir)

        assert called == [], (
            "_finalize_records must not call _read_state_file after disk reads removed; "
            f"called with: {called}"
        )


# ── AC6 regression: history returns data for ingested rows ───────────────────

class TestHistoryDbPathRegression:
    """History endpoint returns correct data for DB-backed ingested rows (AC6).

    These tests confirm the DB path works correctly after the disk fallback
    is removed.
    """

    def test_history_returns_issues_from_db_row(self, tmp_path):
        """DB row with issues_json populated → issues appear in history row."""
        svc = _load_svc()

        label = "sprint-74"
        db.record_sprint_start(label, project=_PROJECT)
        state = _make_state_dict(label)
        db.ingest_sprint_run_artifact(label, state, project=_PROJECT)
        db.record_sprint_finish(label)

        # No disk files — purely from DB
        sprints_dir = tmp_path / "sprints_rg"
        sprints_dir.mkdir()

        result = svc.get_sprint_history(sprints_dir=sprints_dir, project=_PROJECT)
        rows = result.get("sprints", [])
        row = next((r for r in rows if r.get("label") == label), None)

        assert row is not None, f"Sprint {label!r} must appear in history"
        assert isinstance(row.get("issues"), list), "issues must be a list"
        assert len(row["issues"]) >= 1, "issues list must be populated from DB"

    def test_history_works_without_disk_file_for_ingested_sprint(self, tmp_path):
        """No disk state file, DB has run_ingested_at → history row present.

        Proves the history endpoint no longer needs disk files for ingested rows.
        """
        svc = _load_svc()

        label = "sprint-75"
        db.record_sprint_start(label, project=_PROJECT)
        state = _make_state_dict(label)
        db.ingest_sprint_run_artifact(label, state, project=_PROJECT)
        db.record_sprint_finish(label)

        sprints_dir = tmp_path / "empty_sprints"
        sprints_dir.mkdir()

        result = svc.get_sprint_history(sprints_dir=sprints_dir, project=_PROJECT)
        labels = [r.get("label") for r in result.get("sprints", [])]
        assert label in labels, (
            f"Ingested sprint {label!r} must appear in history without disk files; "
            f"got {labels}"
        )
