"""Tests for scripts/export_hermes_report.py (issue #1959).

Each test class is anchored to a specific acceptance criterion.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import export_hermes_report as ehr  # noqa: E402


# ── DB fixtures ───────────────────────────────────────────────────────────────

def _make_db(tmp_path: Path) -> Path:
    """Create a minimal SQLite DB with the required tables."""
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
    label_objects = [{"name": l, "color": "e4e669"} for l in labels]
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT OR REPLACE INTO issues (repo, issue_number, title, state, labels, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (repo, number, title, state, json.dumps(label_objects), updated_at),
    )
    conn.commit()
    conn.close()


def _insert_sprint(db_path: Path, project: str, label: str, state: str,
                   created_at: str = "2026-07-10T00:00:00Z",
                   ended_at: str | None = None,
                   started_at: str | None = None) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT OR REPLACE INTO sprints "
        "(label, project, state, created_at, started_at, ended_at) VALUES (?, ?, ?, ?, ?, ?)",
        (label, project, state, created_at, started_at, ended_at),
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


# ── AC1: Bangkok date even when TZ=UTC ───────────────────────────────────────

class TestBangkokDate:
    """AC1: for_date uses Asia/Bangkok, not UTC date."""

    def test_bkk_date_differs_from_utc_near_midnight(self):
        # 17:01 UTC on July 17 = 00:01 July 18 Bangkok
        utc_midnight_bkk = datetime(2026, 7, 17, 17, 1, 0, tzinfo=timezone.utc)
        result = ehr._bkk_date(utc_midnight_bkk)
        assert result == "2026-07-18", (
            f"Expected 2026-07-18 (BKK date), got {result!r} (UTC date would be 2026-07-17)"
        )

    def test_bkk_date_matches_utc_in_morning(self):
        # 08:00 UTC = 15:00 Bangkok — same calendar day
        utc_morning = datetime(2026, 7, 17, 8, 0, 0, tzinfo=timezone.utc)
        result = ehr._bkk_date(utc_morning)
        assert result == "2026-07-17"

    def test_build_contract_for_date_uses_bkk(self, tmp_path):
        db_path = _make_db(tmp_path)
        # Fix now to 17:30 UTC → 00:30 next day BKK
        fixed_now = datetime(2026, 7, 17, 17, 30, 0, tzinfo=timezone.utc)
        contract = ehr.build_contract(
            str(db_path), now=fixed_now, projects_list=[], price_map=None, prev_state={}
        )
        assert contract["for_date"] == "2026-07-18"


# ── AC2: All top-level keys present ──────────────────────────────────────────

class TestTopLevelKeys:
    """AC2: contract contains all required top-level keys."""

    REQUIRED_KEYS = {
        "for_date", "generated_at", "window_start", "window_end",
        "projects", "cost", "cost_source",
        "completed", "needs_review", "dead_letter",
    }

    def test_all_top_level_keys_present(self, tmp_path):
        db_path = _make_db(tmp_path)
        now = datetime(2026, 7, 17, 8, 0, 0, tzinfo=timezone.utc)
        contract = ehr.build_contract(
            str(db_path), now=now, projects_list=[], price_map=None, prev_state={}
        )
        missing = self.REQUIRED_KEYS - set(contract.keys())
        assert not missing, f"Missing top-level keys: {missing}"

    def test_for_date_is_string(self, tmp_path):
        db_path = _make_db(tmp_path)
        now = datetime(2026, 7, 17, 8, 0, 0, tzinfo=timezone.utc)
        contract = ehr.build_contract(
            str(db_path), now=now, projects_list=[], price_map=None, prev_state={}
        )
        assert isinstance(contract["for_date"], str)
        assert len(contract["for_date"]) == 10  # YYYY-MM-DD

    def test_projects_is_list(self, tmp_path):
        db_path = _make_db(tmp_path)
        now = datetime(2026, 7, 17, 8, 0, 0, tzinfo=timezone.utc)
        contract = ehr.build_contract(
            str(db_path), now=now, projects_list=[], price_map=None, prev_state={}
        )
        assert isinstance(contract["projects"], list)

    def test_legacy_rollup_keys_are_lists(self, tmp_path):
        db_path = _make_db(tmp_path)
        now = datetime(2026, 7, 17, 8, 0, 0, tzinfo=timezone.utc)
        contract = ehr.build_contract(
            str(db_path), now=now, projects_list=[], price_map=None, prev_state={}
        )
        assert isinstance(contract["completed"], list)
        assert isinstance(contract["needs_review"], list)
        assert isinstance(contract["dead_letter"], list)


# ── AC3: Per-project entry keys ───────────────────────────────────────────────

class TestProjectEntryKeys:
    """AC3: each project entry has required keys with correct shapes."""

    REQUIRED_PROJECT_KEYS = {
        "name", "status", "in_progress", "shipped", "fixed",
        "stale", "waiting", "counts",
    }

    STATUS_VALUES = {"shipped", "in_progress", "blocked", "waiting_signoff", "idle"}

    def test_project_entry_has_all_keys(self, tmp_path):
        db_path = _make_db(tmp_path)
        now = datetime(2026, 7, 17, 8, 0, 0, tzinfo=timezone.utc)
        projects = [{"repo": "owner/proj", "name": "Proj"}]
        contract = ehr.build_contract(
            str(db_path), now=now, projects_list=projects,
            price_map=None, prev_state={}
        )
        assert len(contract["projects"]) == 1
        entry = contract["projects"][0]
        missing = self.REQUIRED_PROJECT_KEYS - set(entry.keys())
        assert not missing, f"Missing project entry keys: {missing}"

    def test_status_is_valid_value(self, tmp_path):
        db_path = _make_db(tmp_path)
        now = datetime(2026, 7, 17, 8, 0, 0, tzinfo=timezone.utc)
        projects = [{"repo": "owner/proj", "name": "Proj"}]
        contract = ehr.build_contract(
            str(db_path), now=now, projects_list=projects,
            price_map=None, prev_state={}
        )
        entry = contract["projects"][0]
        assert entry["status"] in self.STATUS_VALUES

    def test_in_progress_is_none_when_no_running_sprint(self, tmp_path):
        db_path = _make_db(tmp_path)
        now = datetime(2026, 7, 17, 8, 0, 0, tzinfo=timezone.utc)
        projects = [{"repo": "owner/proj", "name": "Proj"}]
        contract = ehr.build_contract(
            str(db_path), now=now, projects_list=projects,
            price_map=None, prev_state={}
        )
        assert contract["projects"][0]["in_progress"] is None

    def test_in_progress_is_object_when_sprint_running(self, tmp_path):
        db_path = _make_db(tmp_path)
        _insert_sprint(db_path, "owner/proj", "sprint-5", "running",
                       started_at="2026-07-16T10:00:00Z")
        now = datetime(2026, 7, 17, 8, 0, 0, tzinfo=timezone.utc)
        projects = [{"repo": "owner/proj", "name": "Proj"}]
        contract = ehr.build_contract(
            str(db_path), now=now, projects_list=projects,
            price_map=None, prev_state={}
        )
        ip = contract["projects"][0]["in_progress"]
        assert ip is not None
        assert ip["sprint_label"] == "sprint-5"

    def test_counts_is_dict(self, tmp_path):
        db_path = _make_db(tmp_path)
        now = datetime(2026, 7, 17, 8, 0, 0, tzinfo=timezone.utc)
        projects = [{"repo": "owner/proj", "name": "Proj"}]
        contract = ehr.build_contract(
            str(db_path), now=now, projects_list=projects,
            price_map=None, prev_state={}
        )
        counts = contract["projects"][0]["counts"]
        assert isinstance(counts, dict)
        assert "shipped" in counts
        assert "blocked" in counts

    def test_status_in_progress_when_sprint_running(self, tmp_path):
        db_path = _make_db(tmp_path)
        _insert_sprint(db_path, "owner/proj", "sprint-5", "running",
                       started_at="2026-07-16T10:00:00Z")
        now = datetime(2026, 7, 17, 8, 0, 0, tzinfo=timezone.utc)
        projects = [{"repo": "owner/proj", "name": "Proj"}]
        contract = ehr.build_contract(
            str(db_path), now=now, projects_list=projects,
            price_map=None, prev_state={}
        )
        assert contract["projects"][0]["status"] == "in_progress"

    def test_status_idle_when_nothing(self, tmp_path):
        db_path = _make_db(tmp_path)
        now = datetime(2026, 7, 17, 8, 0, 0, tzinfo=timezone.utc)
        projects = [{"repo": "owner/proj", "name": "Proj"}]
        contract = ehr.build_contract(
            str(db_path), now=now, projects_list=projects,
            price_map=None, prev_state={}
        )
        assert contract["projects"][0]["status"] == "idle"


# ── AC4: Legacy rollup keys derived from same DB query ───────────────────────

class TestLegacyRollupKeys:
    """AC4: completed/needs_review/dead_letter are flat 'project: desc' strings."""

    def test_completed_contains_project_name(self, tmp_path):
        db_path = _make_db(tmp_path)
        _insert_issue(db_path, "owner/myproj", 10, "Ticket A",
                      labels=["done"], state="closed")
        now = datetime(2026, 7, 17, 8, 0, 0, tzinfo=timezone.utc)
        projects = [{"repo": "owner/myproj", "name": "My Project"}]
        contract = ehr.build_contract(
            str(db_path), now=now, projects_list=projects,
            price_map=None, prev_state={}
        )
        assert any("My Project" in s for s in contract["completed"]), (
            f"Expected 'My Project' in completed strings: {contract['completed']}"
        )

    def test_needs_review_contains_project_name(self, tmp_path):
        db_path = _make_db(tmp_path)
        _insert_issue(db_path, "owner/myproj", 11, "Needs Sign-off",
                      labels=["uat"], state="open")
        now = datetime(2026, 7, 17, 8, 0, 0, tzinfo=timezone.utc)
        projects = [{"repo": "owner/myproj", "name": "My Project"}]
        contract = ehr.build_contract(
            str(db_path), now=now, projects_list=projects,
            price_map=None, prev_state={}
        )
        assert any("My Project" in s for s in contract["needs_review"]), (
            f"Expected 'My Project' in needs_review strings: {contract['needs_review']}"
        )

    def test_dead_letter_contains_project_name(self, tmp_path):
        db_path = _make_db(tmp_path)
        old_ts = "2026-07-10T00:00:00Z"  # 7 days old, >3 stale threshold
        _insert_issue(db_path, "owner/myproj", 12, "Blocked Issue",
                      labels=["blocked"], state="open", updated_at=old_ts)
        now = datetime(2026, 7, 17, 8, 0, 0, tzinfo=timezone.utc)
        projects = [{"repo": "owner/myproj", "name": "My Project"}]
        contract = ehr.build_contract(
            str(db_path), now=now, projects_list=projects,
            price_map=None, prev_state={}, stale_blocked_days=3
        )
        assert any("My Project" in s for s in contract["dead_letter"]), (
            f"Expected 'My Project' in dead_letter strings: {contract['dead_letter']}"
        )

    def test_empty_rollup_when_no_matching_data(self, tmp_path):
        db_path = _make_db(tmp_path)
        now = datetime(2026, 7, 17, 8, 0, 0, tzinfo=timezone.utc)
        projects = [{"repo": "owner/myproj", "name": "My Project"}]
        contract = ehr.build_contract(
            str(db_path), now=now, projects_list=projects,
            price_map=None, prev_state={}
        )
        assert contract["completed"] == []
        assert contract["needs_review"] == []
        assert contract["dead_letter"] == []


# ── AC5: Stale detection ──────────────────────────────────────────────────────

class TestStaleDetection:
    """AC5: three stale kinds classified by CLI thresholds."""

    def test_blocked_kind_detected_when_old_enough(self, tmp_path):
        db_path = _make_db(tmp_path)
        old_ts = "2026-07-10T00:00:00Z"  # 7 days before now
        _insert_issue(db_path, "owner/proj", 1, "Stuck Issue",
                      labels=["blocked"], state="open", updated_at=old_ts)
        now = datetime(2026, 7, 17, 8, 0, 0, tzinfo=timezone.utc)
        projects = [{"repo": "owner/proj", "name": "Proj"}]
        contract = ehr.build_contract(
            str(db_path), now=now, projects_list=projects,
            price_map=None, prev_state={}, stale_blocked_days=3
        )
        stale = contract["projects"][0]["stale"]
        blocked = [s for s in stale if s["kind"] == "blocked"]
        assert len(blocked) == 1
        assert blocked[0]["issue_number"] == 1
        assert blocked[0]["age_days"] > 3

    def test_blocked_kind_not_detected_when_recent(self, tmp_path):
        db_path = _make_db(tmp_path)
        recent_ts = "2026-07-16T23:00:00Z"  # less than 3 days ago
        _insert_issue(db_path, "owner/proj", 2, "Recent Rework",
                      labels=["rework"], state="open", updated_at=recent_ts)
        now = datetime(2026, 7, 17, 8, 0, 0, tzinfo=timezone.utc)
        projects = [{"repo": "owner/proj", "name": "Proj"}]
        contract = ehr.build_contract(
            str(db_path), now=now, projects_list=projects,
            price_map=None, prev_state={}, stale_blocked_days=3
        )
        stale = contract["projects"][0]["stale"]
        blocked = [s for s in stale if s["kind"] == "blocked"]
        assert len(blocked) == 0

    def test_waiting_signoff_kind_detected(self, tmp_path):
        db_path = _make_db(tmp_path)
        old_ended = "2026-07-13T00:00:00Z"  # 4 days old
        _insert_sprint(db_path, "owner/proj", "sprint-10", "ready_to_merge",
                       ended_at=old_ended)
        now = datetime(2026, 7, 17, 8, 0, 0, tzinfo=timezone.utc)
        projects = [{"repo": "owner/proj", "name": "Proj"}]
        contract = ehr.build_contract(
            str(db_path), now=now, projects_list=projects,
            price_map=None, prev_state={}, stale_waiting_days=2
        )
        stale = contract["projects"][0]["stale"]
        waiting = [s for s in stale if s["kind"] == "waiting_signoff"]
        assert len(waiting) == 1
        assert waiting[0]["sprint_label"] == "sprint-10"
        assert waiting[0]["age_days"] > 2

    def test_waiting_signoff_not_detected_when_recent(self, tmp_path):
        db_path = _make_db(tmp_path)
        recent_ended = "2026-07-16T20:00:00Z"  # less than 2 days old
        _insert_sprint(db_path, "owner/proj", "sprint-11", "ready_to_merge",
                       ended_at=recent_ended)
        now = datetime(2026, 7, 17, 8, 0, 0, tzinfo=timezone.utc)
        projects = [{"repo": "owner/proj", "name": "Proj"}]
        contract = ehr.build_contract(
            str(db_path), now=now, projects_list=projects,
            price_map=None, prev_state={}, stale_waiting_days=2
        )
        stale = contract["projects"][0]["stale"]
        waiting = [s for s in stale if s["kind"] == "waiting_signoff"]
        assert len(waiting) == 0

    def test_backlog_kind_detected(self, tmp_path):
        db_path = _make_db(tmp_path)
        old_created = "2026-07-05T00:00:00Z"  # 12 days old
        _insert_sprint(db_path, "owner/proj", "sprint-20", "draft",
                       created_at=old_created)
        now = datetime(2026, 7, 17, 8, 0, 0, tzinfo=timezone.utc)
        projects = [{"repo": "owner/proj", "name": "Proj"}]
        contract = ehr.build_contract(
            str(db_path), now=now, projects_list=projects,
            price_map=None, prev_state={}, stale_backlog_days=7
        )
        stale = contract["projects"][0]["stale"]
        backlog = [s for s in stale if s["kind"] == "backlog"]
        assert len(backlog) == 1
        assert backlog[0]["sprint_label"] == "sprint-20"
        assert backlog[0]["age_days"] > 7

    def test_backlog_kind_not_detected_when_recent(self, tmp_path):
        db_path = _make_db(tmp_path)
        recent_created = "2026-07-15T00:00:00Z"  # 2 days old, <7 threshold
        _insert_sprint(db_path, "owner/proj", "sprint-21", "draft",
                       created_at=recent_created)
        now = datetime(2026, 7, 17, 8, 0, 0, tzinfo=timezone.utc)
        projects = [{"repo": "owner/proj", "name": "Proj"}]
        contract = ehr.build_contract(
            str(db_path), now=now, projects_list=projects,
            price_map=None, prev_state={}, stale_backlog_days=7
        )
        stale = contract["projects"][0]["stale"]
        backlog = [s for s in stale if s["kind"] == "backlog"]
        assert len(backlog) == 0

    def test_threshold_changes_stale_count(self, tmp_path):
        db_path = _make_db(tmp_path)
        old_ts = "2026-07-10T00:00:00Z"  # 7 days old
        _insert_issue(db_path, "owner/proj", 3, "Old Issue",
                      labels=["blocked"], state="open", updated_at=old_ts)
        now = datetime(2026, 7, 17, 8, 0, 0, tzinfo=timezone.utc)
        projects = [{"repo": "owner/proj", "name": "Proj"}]

        # With threshold=3: stale
        c_low = ehr.build_contract(
            str(db_path), now=now, projects_list=projects,
            price_map=None, prev_state={}, stale_blocked_days=3
        )
        assert len([s for s in c_low["projects"][0]["stale"]
                    if s["kind"] == "blocked"]) == 1

        # With threshold=10: not stale (7 < 10)
        c_high = ehr.build_contract(
            str(db_path), now=now, projects_list=projects,
            price_map=None, prev_state={}, stale_blocked_days=10
        )
        assert len([s for s in c_high["projects"][0]["stale"]
                    if s["kind"] == "blocked"]) == 0


# ── AC7: Two-run fixed-detection cycle ───────────────────────────────────────

class TestFixedDetection:
    """AC7: a blocked issue present in run 1 disappears in run 2 → appears in fixed."""

    def test_two_run_fixed_detection_cycle(self, tmp_path):
        db_path = _make_db(tmp_path)
        repo = "owner/proj"
        state_path = tmp_path / ".commander_export_state.json"

        # Seed a blocked issue
        _insert_issue(db_path, repo, 42, "Old Blocker",
                      labels=["blocked"], state="open",
                      updated_at="2026-07-10T00:00:00Z")

        now = datetime(2026, 7, 17, 8, 0, 0, tzinfo=timezone.utc)
        projects = [{"repo": repo, "name": "Proj"}]

        # ── Run 1: blocked issue present ──────────────────────────────────────
        prev_state_1 = ehr._load_state(state_path)
        contract1 = ehr.build_contract(
            str(db_path), now=now, projects_list=projects,
            price_map=None, prev_state=prev_state_1, stale_blocked_days=3
        )
        # fixed should be empty on first run (nothing previously tracked)
        assert contract1["projects"][0]["fixed"] == []
        # Save state from run 1
        ehr._save_state_atomic(state_path, contract1["_new_state"])

        # ── Resolve the issue (remove blocked label) ──────────────────────────
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "UPDATE issues SET labels = '[]', state = 'closed' WHERE repo = ? AND issue_number = ?",
            (repo, 42),
        )
        conn.commit()
        conn.close()

        # ── Run 2: blocked issue gone → should appear in fixed ────────────────
        prev_state_2 = ehr._load_state(state_path)
        contract2 = ehr.build_contract(
            str(db_path), now=now, projects_list=projects,
            price_map=None, prev_state=prev_state_2, stale_blocked_days=3
        )
        fixed = contract2["projects"][0]["fixed"]
        assert len(fixed) == 1, f"Expected 1 fixed item, got: {fixed}"
        assert fixed[0]["issue_number"] == 42
        assert fixed[0]["title"] == "Old Blocker"

    def test_issue_still_blocked_does_not_appear_in_fixed(self, tmp_path):
        db_path = _make_db(tmp_path)
        repo = "owner/proj"
        state_path = tmp_path / ".commander_export_state.json"

        _insert_issue(db_path, repo, 99, "Still Blocked",
                      labels=["blocked"], state="open",
                      updated_at="2026-07-10T00:00:00Z")

        now = datetime(2026, 7, 17, 8, 0, 0, tzinfo=timezone.utc)
        projects = [{"repo": repo, "name": "Proj"}]

        # Run 1
        prev1 = ehr._load_state(state_path)
        c1 = ehr.build_contract(
            str(db_path), now=now, projects_list=projects,
            price_map=None, prev_state=prev1, stale_blocked_days=3
        )
        ehr._save_state_atomic(state_path, c1["_new_state"])

        # Run 2 — issue still blocked
        prev2 = ehr._load_state(state_path)
        c2 = ehr.build_contract(
            str(db_path), now=now, projects_list=projects,
            price_map=None, prev_state=prev2, stale_blocked_days=3
        )
        assert c2["projects"][0]["fixed"] == []


# ── AC8: Cost field ───────────────────────────────────────────────────────────

class TestCostField:
    """AC8: cost populates from price_map, token count, or 'unknown'."""

    def test_cost_from_price_map(self, tmp_path):
        db_path = _make_db(tmp_path)
        _insert_token_usage(db_path, "claude-sonnet-4-5", 1_000_000, 0)
        price_map = {"claude-sonnet-4-5": {"in": 3.0, "out": 15.0}}
        now = datetime(2026, 7, 17, 8, 0, 0, tzinfo=timezone.utc)
        contract = ehr.build_contract(
            str(db_path), now=now, projects_list=[], price_map=price_map, prev_state={}
        )
        assert contract["cost_source"] == "price_map"
        assert contract["cost"].startswith("$")
        # 1M input tokens × $3/M = $3.00
        assert contract["cost"] == "$3.00"

    def test_cost_fallback_token_count(self, tmp_path):
        db_path = _make_db(tmp_path)
        _insert_token_usage(db_path, "claude-haiku", 500, 250)
        now = datetime(2026, 7, 17, 8, 0, 0, tzinfo=timezone.utc)
        contract = ehr.build_contract(
            str(db_path), now=now, projects_list=[], price_map=None, prev_state={}
        )
        assert contract["cost_source"] == "token_count"
        assert "750 tokens" in contract["cost"]

    def test_cost_unknown_when_no_tokens(self, tmp_path):
        db_path = _make_db(tmp_path)
        now = datetime(2026, 7, 17, 8, 0, 0, tzinfo=timezone.utc)
        contract = ehr.build_contract(
            str(db_path), now=now, projects_list=[], price_map=None, prev_state={}
        )
        assert contract["cost"] == "unknown"
        assert contract["cost_source"] == "unknown"

    def test_price_map_with_model_prefix_fallback(self, tmp_path):
        db_path = _make_db(tmp_path)
        # Model stored as "claude-sonnet-4-5-20251022" in token_usage
        _insert_token_usage(db_path, "claude-sonnet-4-5-20251022", 1_000_000, 0)
        # price_map keyed by prefix without date
        price_map = {"claude-sonnet-4-5": {"in": 3.0, "out": 15.0}}
        now = datetime(2026, 7, 17, 8, 0, 0, tzinfo=timezone.utc)
        contract = ehr.build_contract(
            str(db_path), now=now, projects_list=[], price_map=price_map, prev_state={}
        )
        assert contract["cost_source"] == "price_map"


# ── AC9: Atomic write ─────────────────────────────────────────────────────────

class TestAtomicWrite:
    """AC9: output written via tmp file + os.replace; parent dirs created."""

    def test_write_creates_file(self, tmp_path):
        out = tmp_path / "report.json"
        payload = {"for_date": "2026-07-17", "projects": []}
        ehr._write_atomic(out, payload)
        assert out.exists()
        assert json.loads(out.read_text())["for_date"] == "2026-07-17"

    def test_no_tmp_file_after_successful_write(self, tmp_path):
        out = tmp_path / "report.json"
        ehr._write_atomic(out, {"x": 1})
        assert not out.with_suffix(".tmp").exists()

    def test_write_creates_parent_dirs(self, tmp_path):
        out = tmp_path / "nested" / "dir" / "report.json"
        ehr._write_atomic(out, {"x": 1})
        assert out.exists()

    def test_successive_writes_overwrite(self, tmp_path):
        out = tmp_path / "report.json"
        ehr._write_atomic(out, {"run": 1})
        ehr._write_atomic(out, {"run": 2})
        assert json.loads(out.read_text())["run"] == 2


# ── AC10: brief_artifacts persistence ────────────────────────────────────────

class TestBriefArtifactsPersistence:
    """AC10: payload persisted to brief_artifacts under scope 'dev_report'."""

    def test_artifact_written_to_brief_artifacts(self, tmp_path):
        db_path = _make_db(tmp_path)
        now = datetime(2026, 7, 17, 8, 0, 0, tzinfo=timezone.utc)
        payload = {"for_date": "2026-07-17", "projects": []}
        generated_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")

        conn = sqlite3.connect(str(db_path))
        ehr._persist_brief_artifact(conn, "2026-07-17", payload, generated_at)
        conn.close()

        conn2 = sqlite3.connect(str(db_path))
        row = conn2.execute(
            "SELECT scope, project, date, payload FROM brief_artifacts "
            "WHERE scope = 'dev_report'"
        ).fetchone()
        conn2.close()

        assert row is not None
        assert row[0] == "dev_report"
        assert row[2] == "2026-07-17"
        stored = json.loads(row[3])
        assert stored["for_date"] == "2026-07-17"

    def test_main_writes_artifact_to_db_on_non_dry_run(self, tmp_path, monkeypatch):
        db_path = _make_db(tmp_path)
        out = tmp_path / "report.json"
        monkeypatch.setenv("DB_PATH", str(db_path))
        state_path = out.parent / ehr._STATE_FILENAME

        ehr.run(
            db_path=str(db_path),
            output_path=out,
            state_path=state_path,
            dry_run=False,
            projects_list=[],
            price_map=None,
            stale_blocked_days=3,
            stale_waiting_days=2,
            stale_backlog_days=7,
        )

        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT payload FROM brief_artifacts WHERE scope = 'dev_report'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert json.loads(row[0])["projects"] == []

    def test_main_dry_run_does_not_write_artifact(self, tmp_path, monkeypatch):
        db_path = _make_db(tmp_path)
        out = tmp_path / "report.json"
        state_path = out.parent / ehr._STATE_FILENAME

        ehr.run(
            db_path=str(db_path),
            output_path=out,
            state_path=state_path,
            dry_run=True,
            projects_list=[],
            price_map=None,
            stale_blocked_days=3,
            stale_waiting_days=2,
            stale_backlog_days=7,
        )

        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT payload FROM brief_artifacts WHERE scope = 'dev_report'"
        ).fetchone()
        conn.close()
        assert row is None
        assert not out.exists()


# ── AC11: Unhandled exception handling ───────────────────────────────────────

class TestExceptionHandling:
    """AC11: on exception, exit 0, leave existing file untouched, log stderr."""

    def test_run_with_bad_db_leaves_existing_file(self, tmp_path, capsys):
        out = tmp_path / "report.json"
        out.write_text(json.dumps({"for_date": "existing"}))
        state_path = out.parent / ehr._STATE_FILENAME

        # Non-existent DB path causes failure
        ehr.run(
            db_path=str(tmp_path / "nonexistent.db"),
            output_path=out,
            state_path=state_path,
            dry_run=False,
            projects_list=[],
            price_map=None,
            stale_blocked_days=3,
            stale_waiting_days=2,
            stale_backlog_days=7,
        )

        # Existing file must be untouched
        assert out.exists()
        assert json.loads(out.read_text())["for_date"] == "existing"

    def test_run_with_bad_db_logs_to_stderr(self, tmp_path, capsys):
        out = tmp_path / "report.json"
        state_path = out.parent / ehr._STATE_FILENAME

        ehr.run(
            db_path=str(tmp_path / "nonexistent.db"),
            output_path=out,
            state_path=state_path,
            dry_run=False,
            projects_list=[],
            price_map=None,
            stale_blocked_days=3,
            stale_waiting_days=2,
            stale_backlog_days=7,
        )

        captured = capsys.readouterr()
        assert captured.err, "Expected error output on stderr"


# ── AC12: Output path resolution ─────────────────────────────────────────────

class TestOutputPathResolution:
    """AC12: --output → $COMMANDER_REPORT_PATH → $HERMES_HOME/contracts/... → ~/.hermes/..."""

    def test_explicit_flag_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv("COMMANDER_REPORT_PATH", str(tmp_path / "env_path.json"))
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
        result = ehr._resolve_output_path(str(tmp_path / "flag.json"))
        assert result == tmp_path / "flag.json"

    def test_commander_report_path_env_used_when_no_flag(self, tmp_path, monkeypatch):
        monkeypatch.setenv("COMMANDER_REPORT_PATH", str(tmp_path / "env_path.json"))
        monkeypatch.delenv("HERMES_HOME", raising=False)
        result = ehr._resolve_output_path(None)
        assert result == tmp_path / "env_path.json"

    def test_hermes_home_used_when_no_commander_path(self, tmp_path, monkeypatch):
        monkeypatch.delenv("COMMANDER_REPORT_PATH", raising=False)
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
        result = ehr._resolve_output_path(None)
        assert result == tmp_path / "hermes" / "contracts" / "commander_report.latest.json"

    def test_home_fallback_when_no_env(self, monkeypatch):
        monkeypatch.delenv("COMMANDER_REPORT_PATH", raising=False)
        monkeypatch.delenv("HERMES_HOME", raising=False)
        result = ehr._resolve_output_path(None)
        assert result == Path.home() / ".hermes" / "contracts" / "commander_report.latest.json"


# ── AC13: --db-path defaults to $DB_PATH ─────────────────────────────────────

class TestDbPathResolution:
    """AC13: --db-path flag takes priority; falls back to $DB_PATH env var."""

    def test_explicit_flag_wins(self, monkeypatch):
        monkeypatch.setenv("DB_PATH", "/env/db.db")
        result = ehr._resolve_db_path("/flag/db.db")
        assert result == "/flag/db.db"

    def test_env_var_used_when_no_flag(self, monkeypatch):
        monkeypatch.setenv("DB_PATH", "/env/commander.db")
        result = ehr._resolve_db_path(None)
        assert result == "/env/commander.db"

    def test_none_returned_when_nothing_set(self, monkeypatch):
        monkeypatch.delenv("DB_PATH", raising=False)
        result = ehr._resolve_db_path(None)
        assert result is None


# ── AC14: Induced failure test ────────────────────────────────────────────────

class TestInducedFailure:
    """AC14: non-existent --db-path → exit 0, writes nothing, stderr error."""

    def test_nonexistent_db_exits_zero(self, tmp_path, capsys):
        out = tmp_path / "report.json"
        state_path = out.parent / ehr._STATE_FILENAME

        ehr.run(
            db_path=str(tmp_path / "does_not_exist.db"),
            output_path=out,
            state_path=state_path,
            dry_run=False,
            projects_list=[],
            price_map=None,
            stale_blocked_days=3,
            stale_waiting_days=2,
            stale_backlog_days=7,
        )
        # No exception raised = exit 0 equivalent when called from main()

    def test_nonexistent_db_writes_nothing(self, tmp_path, capsys):
        out = tmp_path / "report.json"
        state_path = out.parent / ehr._STATE_FILENAME

        ehr.run(
            db_path=str(tmp_path / "does_not_exist.db"),
            output_path=out,
            state_path=state_path,
            dry_run=False,
            projects_list=[],
            price_map=None,
            stale_blocked_days=3,
            stale_waiting_days=2,
            stale_backlog_days=7,
        )
        assert not out.exists()

    def test_nonexistent_db_prints_to_stderr(self, tmp_path, capsys):
        out = tmp_path / "report.json"
        state_path = out.parent / ehr._STATE_FILENAME

        ehr.run(
            db_path=str(tmp_path / "does_not_exist.db"),
            output_path=out,
            state_path=state_path,
            dry_run=False,
            projects_list=[],
            price_map=None,
            stale_blocked_days=3,
            stale_waiting_days=2,
            stale_backlog_days=7,
        )
        captured = capsys.readouterr()
        assert captured.err, "Expected error message on stderr for non-existent DB"
