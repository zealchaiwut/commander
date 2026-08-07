"""Tests for issue #1979: /api/dev-report fields fixed, age_days, and cost.

The #1961 implementation hardcoded fixed=[], age_days=None, and cost=None.
These tests verify the service layer populates all three correctly.

AC1: stale blocked issues carry a real numeric age_days (not None).
AC2: cost is a non-empty string (not None, not "").
AC3: fixed is populated when a blocked issue is resolved between two runs.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import export_hermes_report as ehr  # noqa: E402

_TEST_PROJECTS = [{"repo": "owner/proj", "name": "Proj"}]

# ── Shared DB helpers ─────────────────────────────────────────────────────────

def _make_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE issues (
            repo TEXT NOT NULL DEFAULT '',
            issue_number INTEGER NOT NULL,
            title TEXT,
            state TEXT,
            labels TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT,
            raw TEXT,
            PRIMARY KEY (repo, issue_number)
        )
    """)
    conn.execute("""
        CREATE TABLE sprints (
            label TEXT NOT NULL,
            project TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL DEFAULT 'draft',
            created_at TEXT,
            started_at TEXT,
            ended_at TEXT,
            end_reason TEXT,
            parent_label TEXT,
            PRIMARY KEY (label, project)
        )
    """)
    conn.execute("""
        CREATE TABLE token_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_role TEXT,
            model_name TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            recorded_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS brief_artifacts (
            scope        TEXT NOT NULL,
            project      TEXT NOT NULL DEFAULT '',
            date         TEXT NOT NULL,
            payload      TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            PRIMARY KEY (scope, project, date)
        )
    """)
    conn.commit()
    conn.close()
    return db_path


def _insert_issue(db_path: Path, repo: str, number: int, title: str,
                  labels: list, state: str = "open",
                  updated_at: str = "2026-07-17T08:00:00Z") -> None:
    label_objects = [{"name": lb, "color": "e4e669"} for lb in labels]
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT OR REPLACE INTO issues "
        "(repo, issue_number, title, state, labels, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (repo, number, title, state, json.dumps(label_objects), updated_at),
    )
    conn.commit()
    conn.close()


def _insert_token_usage(db_path: Path, model: str, inp: int, out: int) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO token_usage (model_name, input_tokens, output_tokens, recorded_at) "
        "VALUES (?, ?, ?, ?)",
        (model, inp, out, "2026-07-17T08:00:00"),
    )
    conn.commit()
    conn.close()


# ── AC1: age_days is a real number for stale blocked issues ───────────────────

class TestAgeDaysPopulated:
    """AC1: stale entries have numeric age_days, not None."""

    def test_stale_blocked_issue_has_numeric_age_days(self, tmp_path):
        db_path = _make_db(tmp_path)
        # Updated 7 days ago — well past the 3-day threshold
        old_ts = "2026-07-10T00:00:00Z"
        _insert_issue(db_path, "owner/proj", 1, "Stuck Issue",
                      labels=["blocked"], state="open", updated_at=old_ts)

        now = datetime(2026, 7, 17, 8, 0, 0, tzinfo=timezone.utc)
        projects = [{"repo": "owner/proj", "name": "Proj"}]
        contract = ehr.build_contract(
            str(db_path), now=now, projects_list=projects,
            price_map=None, prev_state={}, stale_blocked_days=3,
        )
        stale = contract["projects"][0]["stale"]
        blocked_stale = [s for s in stale if s["kind"] == "blocked"]
        assert len(blocked_stale) == 1, "Expected one stale blocked entry"

        age = blocked_stale[0]["age_days"]
        assert age is not None, "age_days must not be None"
        assert isinstance(age, (int, float)), f"age_days must be numeric, got {type(age)}"
        assert age > 3, f"Expected age_days > 3, got {age}"

    def test_stale_waiting_signoff_has_numeric_age_days(self, tmp_path):
        db_path = _make_db(tmp_path)
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO sprints (label, project, state, created_at, ended_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("sprint-5", "owner/proj", "ready_to_merge",
             "2026-07-05T00:00:00Z", "2026-07-13T00:00:00Z"),
        )
        conn.commit()
        conn.close()

        now = datetime(2026, 7, 17, 8, 0, 0, tzinfo=timezone.utc)
        projects = [{"repo": "owner/proj", "name": "Proj"}]
        contract = ehr.build_contract(
            str(db_path), now=now, projects_list=projects,
            price_map=None, prev_state={}, stale_waiting_days=2,
        )
        stale = contract["projects"][0]["stale"]
        waiting = [s for s in stale if s["kind"] == "waiting_signoff"]
        assert len(waiting) == 1
        age = waiting[0]["age_days"]
        assert age is not None, "age_days must not be None for waiting_signoff"
        assert isinstance(age, (int, float))
        assert age > 2


# ── AC2: cost is a non-empty string ──────────────────────────────────────────

class TestCostPopulated:
    """AC2: cost field is a non-empty, non-None string."""

    def test_cost_is_string_when_token_usage_present(self, tmp_path):
        db_path = _make_db(tmp_path)
        _insert_token_usage(db_path, "claude-sonnet-4-6", 100_000, 50_000)

        now = datetime(2026, 7, 17, 8, 0, 0, tzinfo=timezone.utc)
        contract = ehr.build_contract(
            str(db_path), now=now, projects_list=[],
            price_map=None, prev_state={},
        )
        cost = contract["cost"]
        assert cost is not None, "cost must not be None"
        assert isinstance(cost, str), f"cost must be a string, got {type(cost)}"
        assert cost != "", "cost must not be empty"

    def test_cost_is_unknown_string_when_no_tokens(self, tmp_path):
        db_path = _make_db(tmp_path)
        now = datetime(2026, 7, 17, 8, 0, 0, tzinfo=timezone.utc)
        contract = ehr.build_contract(
            str(db_path), now=now, projects_list=[],
            price_map=None, prev_state={},
        )
        cost = contract["cost"]
        assert cost is not None, "cost must not be None even with no token data"
        assert isinstance(cost, str)
        assert cost == "unknown"

    def test_cost_dollar_string_with_price_map(self, tmp_path):
        db_path = _make_db(tmp_path)
        _insert_token_usage(db_path, "claude-sonnet-4-6", 1_000_000, 0)
        price_map = {"claude-sonnet-4-6": {"in": 3.0, "out": 15.0}}

        now = datetime(2026, 7, 17, 8, 0, 0, tzinfo=timezone.utc)
        contract = ehr.build_contract(
            str(db_path), now=now, projects_list=[],
            price_map=price_map, prev_state={},
        )
        cost = contract["cost"]
        assert cost is not None
        assert cost.startswith("$"), f"Expected cost to start with '$', got {cost!r}"
        assert contract["cost_source"] == "price_map"


# ── AC3: fixed is populated between consecutive assemble_and_store runs ───────

class TestFixedPopulatedBetweenRuns:
    """AC3: assemble_and_store persists blocked state so fixed works across calls."""

    def _make_fake_db_module(self, db_path: Path):
        """Return a fake db module backed by a real SQLite file + in-memory artifact store."""
        artifacts: dict = {}

        class FakeDb:
            DB_PATH = str(db_path)

            @staticmethod
            def get_brief_artifact(scope, project, date):
                payload = artifacts.get((scope, project or "", date))
                if payload is None:
                    return None
                return {"payload": payload, "generated_at": "2026-01-01T00:00:00Z"}

            @staticmethod
            def set_brief_artifact(scope, project, date, payload, generated_at=None):
                artifacts[(scope, project or "", date)] = payload
                return generated_at or "2026-01-01T00:00:00Z"

        return FakeDb()

    def test_fixed_empty_on_first_run(self, tmp_path, monkeypatch):
        db_path = _make_db(tmp_path)
        _insert_issue(db_path, "owner/proj", 42, "Blocked Bug",
                      labels=["blocked"], state="open",
                      updated_at="2026-07-10T00:00:00Z")

        import routers.dev_report_service as svc
        fake_db = self._make_fake_db_module(db_path)
        monkeypatch.setattr(svc, "_db", lambda: fake_db)

        with patch.object(ehr, "_auto_load_projects", return_value=_TEST_PROJECTS):
            result = svc.assemble_and_store("2026-07-17", db_path=str(db_path))
        projects = result["payload"]["projects"]
        # First run: no prev_state → fixed must be empty
        assert len(projects) == 1, f"Expected 1 project, got {len(projects)}"
        assert projects[0]["fixed"] == [], (
            "fixed must be empty on first run (no previous state)"
        )

    def test_fixed_populated_after_issue_resolved(self, tmp_path, monkeypatch):
        db_path = _make_db(tmp_path)
        _insert_issue(db_path, "owner/proj", 42, "Blocked Bug",
                      labels=["blocked"], state="open",
                      updated_at="2026-07-10T00:00:00Z")

        import routers.dev_report_service as svc
        fake_db = self._make_fake_db_module(db_path)
        monkeypatch.setattr(svc, "_db", lambda: fake_db)

        with patch.object(ehr, "_auto_load_projects", return_value=_TEST_PROJECTS):
            # Run 1: blocked issue present
            svc.assemble_and_store("2026-07-17", db_path=str(db_path))

            # Resolve the issue (remove blocked label)
            conn = sqlite3.connect(str(db_path))
            conn.execute(
                "UPDATE issues SET labels = '[]', state = 'closed' "
                "WHERE repo = 'owner/proj' AND issue_number = 42"
            )
            conn.commit()
            conn.close()

            # Run 2: issue no longer blocked → should appear in fixed
            result2 = svc.assemble_and_store("2026-07-18", db_path=str(db_path))

        projects = result2["payload"]["projects"]
        fixed = projects[0]["fixed"]
        assert len(fixed) == 1, (
            f"Expected 1 fixed item after issue resolved, got: {fixed}"
        )
        assert fixed[0]["issue_number"] == 42
        assert fixed[0]["title"] == "Blocked Bug"

    def test_fixed_empty_when_issue_still_blocked(self, tmp_path, monkeypatch):
        db_path = _make_db(tmp_path)
        _insert_issue(db_path, "owner/proj", 99, "Still Blocked",
                      labels=["blocked"], state="open",
                      updated_at="2026-07-10T00:00:00Z")

        import routers.dev_report_service as svc
        fake_db = self._make_fake_db_module(db_path)
        monkeypatch.setattr(svc, "_db", lambda: fake_db)

        with patch.object(ehr, "_auto_load_projects", return_value=_TEST_PROJECTS):
            svc.assemble_and_store("2026-07-17", db_path=str(db_path))
            result2 = svc.assemble_and_store("2026-07-18", db_path=str(db_path))

        projects = result2["payload"]["projects"]
        assert projects[0]["fixed"] == [], (
            "fixed must be empty when issue is still blocked in run 2"
        )
