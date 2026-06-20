"""TDD tests for issue #1160: Lazy-ingest Sprint History read path.

Acceptance criteria covered:
- AC1: When sprints row has run_ingested_at=NULL and disk artifact exists,
  _record_from_lifecycle triggers ingest before returning data.
- AC2: After ingest, run_ingested_at is set on the sprints row.
- AC3: _enrich_from_state (direct disk read at render time) is no longer
  called from _record_from_lifecycle.
- AC4: Second open of the same sprint does not re-trigger ingest (idempotent).
- AC5: After first ingest, _read_state_file is not called again.
- AC6: No dual read path — _record_from_lifecycle always uses enrichment_from_db_row.
- AC (UAT5): No disk artifact + no run_ingested_at → empty result, no crash.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest.mock import patch

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
    db_file = tmp_path / "test_1160_history.db"
    original = db.DB_PATH
    db.DB_PATH = str(db_file)
    db.init_db()
    yield tmp_path
    db.DB_PATH = original


def _make_state_dict(label, issues=None):
    return {
        "sprint_label": label,
        "wall_clock_secs": 300,
        "issues": issues or [
            {
                "number": 500,
                "title": "ticket 1",
                "status": "done",
                "agent_status": "completed",
                "failure_reason": None,
                "state": "merged",
            }
        ],
    }


def _write_state_file(sprints_dir: Path, label: str, state: dict) -> None:
    sprints_dir.mkdir(parents=True, exist_ok=True)
    (sprints_dir / f"{label}-state.json").write_text(
        json.dumps(state), encoding="utf-8"
    )


# ── AC1: lazy-ingest triggered when run_ingested_at is NULL ──────────────────


class TestLazyIngestTriggeredOnNullRunIngestedAt:
    """When run_ingested_at is NULL and a disk artifact exists,
    _record_from_lifecycle must trigger ingest before returning data (AC1).
    """

    def test_ingest_called_when_run_ingested_at_null(self, tmp_path):
        """sprints row with run_ingested_at=NULL + disk file → ingest is called."""
        svc = _load_svc()
        label = "sprint-80"
        sprints_dir = tmp_path / "sprints"
        _write_state_file(sprints_dir, label, _make_state_dict(label))

        db.record_sprint_start(label, project=_PROJECT)
        row = db.get_sprint(label)
        assert not row.get("run_ingested_at"), "Precondition: run_ingested_at must be NULL"

        ingest_calls = []
        original_ingest = db.ingest_sprint_run_artifact

        def _spy(*args, **kwargs):
            ingest_calls.append((args, kwargs))
            return original_ingest(*args, **kwargs)

        with patch.object(db, "ingest_sprint_run_artifact", side_effect=_spy):
            svc._record_from_lifecycle(row, sprints_dir)

        assert len(ingest_calls) == 1, (
            f"ingest_sprint_run_artifact must be called exactly once for lazy-ingest; "
            f"called {len(ingest_calls)} times"
        )


# ── AC2: run_ingested_at populated after ingest ───────────────────────────────


class TestRunIngestedAtPopulatedAfterIngest:
    """After lazy-ingest, run_ingested_at is set on the sprints row (AC2)."""

    def test_run_ingested_at_set_after_lazy_ingest(self, tmp_path):
        """After _record_from_lifecycle with NULL ingest, run_ingested_at is populated."""
        svc = _load_svc()
        label = "sprint-81"
        sprints_dir = tmp_path / "sprints"
        _write_state_file(sprints_dir, label, _make_state_dict(label))

        db.record_sprint_start(label, project=_PROJECT)
        row = db.get_sprint(label)
        assert not row.get("run_ingested_at")

        svc._record_from_lifecycle(row, sprints_dir)

        refreshed = db.get_sprint(label)
        assert refreshed and refreshed.get("run_ingested_at"), (
            "run_ingested_at must be set on the sprints row after lazy-ingest"
        )


# ── AC3: _enrich_from_state not called at render time ────────────────────────


class TestEnrichFromStateNotCalledAtRenderTime:
    """_enrich_from_state (direct disk read) must not be called from
    _record_from_lifecycle for any row (AC3).
    """

    def test_enrich_from_state_not_called_with_disk_file(self, tmp_path):
        """Disk file present, run_ingested_at=NULL → lazy-ingest path, no _enrich_from_state."""
        svc = _load_svc()
        label = "sprint-82"
        sprints_dir = tmp_path / "sprints"
        _write_state_file(sprints_dir, label, _make_state_dict(label))

        db.record_sprint_start(label, project=_PROJECT)
        row = db.get_sprint(label)

        called = []
        with patch.object(
            svc, "_enrich_from_state",
            side_effect=lambda *a, **kw: called.append(a) or {},
        ):
            svc._record_from_lifecycle(row, sprints_dir)

        assert called == [], (
            "_enrich_from_state must not be called at render time (disk read removed); "
            f"was called with: {called}"
        )

    def test_enrich_from_state_not_called_when_already_ingested(self, tmp_path):
        """run_ingested_at already set → DB path only, no _enrich_from_state."""
        svc = _load_svc()
        label = "sprint-82c"
        sprints_dir = tmp_path / "sprints_ingested"
        sprints_dir.mkdir()

        db.record_sprint_start(label, project=_PROJECT)
        db.ingest_sprint_run_artifact(label, _make_state_dict(label), project=_PROJECT)
        row = db.get_sprint(label)
        assert row.get("run_ingested_at"), "Precondition: run_ingested_at must be set"

        called = []
        with patch.object(
            svc, "_enrich_from_state",
            side_effect=lambda *a, **kw: called.append(a) or {},
        ):
            svc._record_from_lifecycle(row, sprints_dir)

        assert called == [], (
            "_enrich_from_state must not be called when run_ingested_at is already set; "
            f"was called with: {called}"
        )

    def test_enrich_from_state_not_called_when_no_disk_file(self, tmp_path):
        """No disk file, run_ingested_at=NULL → empty enrichment, no _enrich_from_state."""
        svc = _load_svc()
        label = "sprint-82b"
        sprints_dir = tmp_path / "empty_sprints"
        sprints_dir.mkdir()

        db.record_sprint_start(label, project=_PROJECT)
        row = db.get_sprint(label)

        called = []
        with patch.object(
            svc, "_enrich_from_state",
            side_effect=lambda *a, **kw: called.append(a) or {},
        ):
            svc._record_from_lifecycle(row, sprints_dir)

        assert called == [], (
            "_enrich_from_state must not be called even when no disk file; "
            f"was called with: {called}"
        )


# ── AC4: ingest is idempotent (second open does not re-ingest) ────────────────


class TestIngestIdempotent:
    """Second open of the same sprint must not re-trigger ingest (AC4)."""

    def test_second_call_does_not_reingest(self, tmp_path):
        """Calling _record_from_lifecycle twice triggers ingest exactly once."""
        svc = _load_svc()
        label = "sprint-83"
        sprints_dir = tmp_path / "sprints"
        _write_state_file(sprints_dir, label, _make_state_dict(label))

        db.record_sprint_start(label, project=_PROJECT)
        row = db.get_sprint(label)

        ingest_calls = []
        original_ingest = db.ingest_sprint_run_artifact

        def _spy(*args, **kwargs):
            ingest_calls.append((args, kwargs))
            return original_ingest(*args, **kwargs)

        with patch.object(db, "ingest_sprint_run_artifact", side_effect=_spy):
            # First call
            svc._record_from_lifecycle(row, sprints_dir)
            # Second call — re-fetch the row (as the server would after first access)
            row = db.get_sprint(label) or row
            svc._record_from_lifecycle(row, sprints_dir)

        assert len(ingest_calls) == 1, (
            f"ingest_sprint_run_artifact must be called exactly once (idempotent); "
            f"called {len(ingest_calls)} times"
        )


# ── AC5: subsequent reads return from SQLite only ─────────────────────────────


class TestSubsequentReadsFromSqliteOnly:
    """After first ingest, _read_state_file must not be called on subsequent reads (AC5)."""

    def test_no_disk_read_after_ingest(self, tmp_path):
        """After first lazy-ingest, second call does not call _read_state_file."""
        svc = _load_svc()
        label = "sprint-84"
        sprints_dir = tmp_path / "sprints"
        _write_state_file(sprints_dir, label, _make_state_dict(label))

        db.record_sprint_start(label, project=_PROJECT)
        row = db.get_sprint(label)

        # First call triggers lazy-ingest
        svc._record_from_lifecycle(row, sprints_dir)

        # Second call — row now has run_ingested_at set; no disk read expected
        row = db.get_sprint(label) or row
        disk_calls = []
        with patch.object(
            svc, "_read_state_file",
            side_effect=lambda *a, **kw: disk_calls.append(a) or None,
        ):
            svc._record_from_lifecycle(row, sprints_dir)

        assert disk_calls == [], (
            "_read_state_file must not be called after run_ingested_at is set; "
            f"called with: {disk_calls}"
        )


# ── AC5: data populated from DB after ingest ─────────────────────────────────


class TestDataFromDbAfterIngest:
    """Issues returned after lazy-ingest come from the DB row, not disk (AC5)."""

    def test_issues_populated_from_db_after_lazy_ingest(self, tmp_path):
        """After lazy-ingest, the returned record contains the expected issues."""
        svc = _load_svc()
        label = "sprint-85"
        sprints_dir = tmp_path / "sprints"
        state = _make_state_dict(
            label,
            issues=[
                {
                    "number": 501,
                    "title": "Lazy ticket",
                    "status": "done",
                    "agent_status": "completed",
                    "failure_reason": None,
                    "state": "merged",
                }
            ],
        )
        _write_state_file(sprints_dir, label, state)

        db.record_sprint_start(label, project=_PROJECT)
        row = db.get_sprint(label)

        result = svc._record_from_lifecycle(row, sprints_dir)

        assert isinstance(result.get("issues"), list), "issues must be a list"
        assert len(result["issues"]) >= 1, (
            "issues must be populated from lazy-ingest → DB path"
        )


# ── AC (UAT5): graceful when no disk artifact and no run_ingested_at ─────────


class TestGracefulNoDiskNoIngest:
    """No disk artifact and run_ingested_at=NULL → empty result, no crash (UAT5)."""

    def test_no_crash_when_no_disk_artifact_no_ingest(self, tmp_path):
        """sprints row with no disk file and no run_ingested_at → returns record."""
        svc = _load_svc()
        label = "sprint-86"
        sprints_dir = tmp_path / "empty_sprints"
        sprints_dir.mkdir()

        db.record_sprint_start(label, project=_PROJECT)
        row = db.get_sprint(label)

        # Must not raise
        result = svc._record_from_lifecycle(row, sprints_dir)

        assert isinstance(result, dict), "Must return a dict even with no data"
        assert result.get("label") == label, "label must be in the result"
        assert isinstance(result.get("issues"), list), "issues must be a list (empty OK)"
