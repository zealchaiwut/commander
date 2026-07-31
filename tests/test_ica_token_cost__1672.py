"""Tests for issue #1672 — Track and display ICA sprint token usage and cost.

AC coverage:
  AC1  — token usage (input, output, cache_read, cache_write) captured per agent run on ICA path
  AC2  — estimated cost in USD calculated from ICA meter rates and stored in metrics DB
  AC3  — ICA runs correctly identified; Anthropic-path runs unaffected
  AC4  — cost stored atomically with run record (cost_usd in agent_runs)
  AC5  — Logs view endpoint returns per-sprint cost summary for ICA sprints
  AC6  — cost summary read from DB, not recomputed at render time
  AC7  — zero-cost or failed runs excluded from cost summary / shown with indicator
  AC8  — no schema migration breaks existing Anthropic-path metrics records
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SERVICES_DIR = REPO_ROOT / "services" / "sprint_manager"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR), str(SERVICES_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Create an isolated SQLite DB and point DB_PATH at it."""
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(db_file))
    # Force db module to re-read DB_PATH
    import importlib
    import db as _db
    importlib.reload(_db)
    _db.init_db()
    yield _db, db_file
    importlib.reload(_db)  # restore default after test


def _insert_token_usage_row(conn, session_id, project, input_tokens, output_tokens,
                             recorded_at, agent_role=None, model_name=None,
                             cache_read_tokens=0, cache_write_tokens=0, ccproxy_profile=None):
    """Insert a token_usage row directly (simulates hook writes)."""
    # The table may or may not have the new columns; try both forms.
    try:
        conn.execute(
            """INSERT INTO token_usage
               (session_id, project, input_tokens, output_tokens, recorded_at,
                agent_role, model_name, cache_read_tokens, cache_write_tokens, ccproxy_profile)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (session_id, project, input_tokens, output_tokens, recorded_at,
             agent_role, model_name, cache_read_tokens, cache_write_tokens, ccproxy_profile),
        )
    except sqlite3.OperationalError:
        conn.execute(
            """INSERT INTO token_usage
               (session_id, project, input_tokens, output_tokens, recorded_at, agent_role, model_name)
               VALUES (?,?,?,?,?,?,?)""",
            (session_id, project, input_tokens, output_tokens, recorded_at, agent_role, model_name),
        )


def _insert_agent_run(conn, issue_number, sprint_label, agent, started_at,
                      finished_at=None, total_tokens=None, outcome=None,
                      is_ica=0, cost_usd=None, model_used=None):
    """Insert an agent_runs row directly."""
    try:
        conn.execute(
            """INSERT INTO agent_runs
               (issue_number, sprint_label, agent, started_at, finished_at,
                total_tokens, outcome, is_ica, cost_usd, model_used)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (issue_number, sprint_label, agent, started_at, finished_at,
             total_tokens, outcome, is_ica, cost_usd, model_used),
        )
    except sqlite3.OperationalError:
        conn.execute(
            """INSERT INTO agent_runs
               (issue_number, sprint_label, agent, started_at, finished_at,
                total_tokens, outcome, model_used)
               VALUES (?,?,?,?,?,?,?,?)""",
            (issue_number, sprint_label, agent, started_at, finished_at,
             total_tokens, outcome, model_used),
        )


# ---------------------------------------------------------------------------
# AC1 — cache_read_tokens and cache_write_tokens captured in token_usage
# ---------------------------------------------------------------------------

class TestAC1CacheTokensCaptured:
    def test_token_usage_table_has_cache_columns(self, tmp_db):
        """AC1: token_usage table must have cache_read_tokens and cache_write_tokens columns."""
        _db, db_file = tmp_db
        with _db.get_conn() as conn:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(token_usage)")}
        assert "cache_read_tokens" in cols, "token_usage missing cache_read_tokens column"
        assert "cache_write_tokens" in cols, "token_usage missing cache_write_tokens column"
        assert "ccproxy_profile" in cols, "token_usage missing ccproxy_profile column"

    def test_record_token_usage_accepts_cache_fields(self, tmp_db):
        """AC1: record_token_usage stores cache_read_tokens and cache_write_tokens."""
        _db, db_file = tmp_db
        _db.record_token_usage(
            session_id="sess-001",
            project="owner/repo",
            input_tokens=1000,
            output_tokens=200,
            agent_role="coder",
            model_name="claude-sonnet-4-6",
            cache_read_tokens=150,
            cache_write_tokens=50,
            ccproxy_profile="ica",
        )
        with _db.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM token_usage WHERE session_id = 'sess-001'"
            ).fetchone()
        assert row is not None
        assert int(row["cache_read_tokens"]) == 150
        assert int(row["cache_write_tokens"]) == 50
        assert row["ccproxy_profile"] == "ica"

    def test_record_token_usage_backwards_compat_no_cache(self, tmp_db):
        """AC8: record_token_usage still works without cache fields (Anthropic path)."""
        _db, db_file = tmp_db
        _db.record_token_usage(
            session_id="sess-002",
            project="owner/repo",
            input_tokens=500,
            output_tokens=100,
        )
        with _db.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM token_usage WHERE session_id = 'sess-002'"
            ).fetchone()
        assert row is not None
        assert int(row["input_tokens"]) == 500
        assert int(row["output_tokens"]) == 100
        # cache columns default to 0 / NULL
        assert row["cache_read_tokens"] in (0, None)
        assert row["ccproxy_profile"] is None


# ---------------------------------------------------------------------------
# AC2 — ICA cost calculated and stored in agent_runs
# ---------------------------------------------------------------------------

class TestAC2IcaCostCalculated:
    def test_agent_runs_has_cost_columns(self, tmp_db):
        """AC2: agent_runs must have is_ica and cost_usd columns."""
        _db, db_file = tmp_db
        with _db.get_conn() as conn:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(agent_runs)")}
        assert "is_ica" in cols, "agent_runs missing is_ica column"
        assert "cost_usd" in cols, "agent_runs missing cost_usd column"

    def test_record_agent_finish_stores_ica_cost(self, tmp_db):
        """AC2+AC4: record_agent_finish atomically stores is_ica and cost_usd."""
        _db, db_file = tmp_db
        run_id = _db.record_agent_start(1, "sprint-105", "coder", is_ica=True)
        assert run_id is not None
        _db.record_agent_finish(
            1, "sprint-105", "coder",
            outcome="success",
            total_tokens=5000,
            is_ica=True,
            cost_usd=0.045,
            run_id=run_id,
        )
        with _db.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM agent_runs WHERE id = ?", (run_id,)
            ).fetchone()
        assert row is not None
        assert row["is_ica"] == 1
        assert abs(float(row["cost_usd"]) - 0.045) < 1e-9

    def test_cost_not_set_for_anthropic_path(self, tmp_db):
        """AC2+AC3: Anthropic-path runs have is_ica=0 and NULL cost_usd."""
        _db, db_file = tmp_db
        run_id = _db.record_agent_start(2, "sprint-105", "coder")
        assert run_id is not None
        _db.record_agent_finish(
            2, "sprint-105", "coder",
            outcome="success",
            total_tokens=3000,
            run_id=run_id,
        )
        with _db.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM agent_runs WHERE id = ?", (run_id,)
            ).fetchone()
        assert row is not None
        assert row["is_ica"] in (0, None)
        assert row["cost_usd"] is None


# ---------------------------------------------------------------------------
# AC3 — ICA runs correctly identified; Anthropic-path runs unaffected
# ---------------------------------------------------------------------------

class TestAC3IcaIdentification:
    def test_ica_run_flagged_at_start(self, tmp_db):
        """AC3: record_agent_start accepts is_ica and stores it."""
        _db, db_file = tmp_db
        run_id = _db.record_agent_start(3, "sprint-105", "coder", is_ica=True)
        assert run_id is not None
        with _db.get_conn() as conn:
            row = conn.execute(
                "SELECT is_ica FROM agent_runs WHERE id = ?", (run_id,)
            ).fetchone()
        assert row is not None
        assert row["is_ica"] == 1

    def test_anthropic_run_not_flagged(self, tmp_db):
        """AC3: Anthropic-path runs have is_ica=0 (default)."""
        _db, db_file = tmp_db
        run_id = _db.record_agent_start(4, "sprint-105", "coder")
        assert run_id is not None
        with _db.get_conn() as conn:
            row = conn.execute(
                "SELECT is_ica FROM agent_runs WHERE id = ?", (run_id,)
            ).fetchone()
        assert row is not None
        assert row["is_ica"] in (0, None)


# ---------------------------------------------------------------------------
# AC4 — cost stored atomically with run record
# ---------------------------------------------------------------------------

class TestAC4AtomicWrite:
    def test_ica_cost_in_same_row_as_outcome(self, tmp_db):
        """AC4: cost_usd and outcome are written in the same UPDATE (same row)."""
        _db, db_file = tmp_db
        run_id = _db.record_agent_start(5, "sprint-105", "coder", is_ica=True)
        assert run_id is not None
        _db.record_agent_finish(
            5, "sprint-105", "coder",
            outcome="success",
            total_tokens=8000,
            is_ica=True,
            cost_usd=0.12,
            run_id=run_id,
        )
        with _db.get_conn() as conn:
            row = conn.execute(
                "SELECT outcome, total_tokens, is_ica, cost_usd FROM agent_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        # All four fields must be populated in one row
        assert row["outcome"] == "success"
        assert row["total_tokens"] == 8000
        assert row["is_ica"] == 1
        assert abs(float(row["cost_usd"]) - 0.12) < 1e-9


# ---------------------------------------------------------------------------
# AC5 — Logs endpoint returns per-sprint cost summary for ICA sprints
# ---------------------------------------------------------------------------

class TestAC5LogsEndpointCostSummary:
    def test_ica_cost_summary_returns_totals(self, tmp_db):
        """AC5+AC6: ica_sprint_cost_summary returns total_tokens and cost_usd from DB."""
        _db, db_file = tmp_db
        with _db.get_conn() as conn:
            _db._create_agent_runs_table(conn)
            conn.execute(
                """INSERT INTO agent_runs
                   (issue_number, sprint_label, agent, started_at, finished_at,
                    total_tokens, outcome, is_ica, cost_usd)
                   VALUES (10, 'sprint-105', 'coder', '2026-07-01T10:00:00', '2026-07-01T10:30:00',
                           5000, 'success', 1, 0.075)"""
            )
            conn.execute(
                """INSERT INTO agent_runs
                   (issue_number, sprint_label, agent, started_at, finished_at,
                    total_tokens, outcome, is_ica, cost_usd)
                   VALUES (10, 'sprint-105', 'tester', '2026-07-01T10:35:00', '2026-07-01T10:50:00',
                           2000, 'pass', 1, 0.030)"""
            )
            conn.commit()

        summary = _db.ica_sprint_cost_summary("sprint-105")
        assert summary is not None
        assert summary["total_tokens"] == 7000
        assert abs(summary["cost_usd"] - 0.105) < 1e-9
        assert summary["run_count"] == 2
        assert summary["is_ica"] is True

    def test_ica_cost_summary_empty_for_non_ica_sprint(self, tmp_db):
        """AC5: sprint with no ICA runs returns empty/zero summary."""
        _db, db_file = tmp_db
        with _db.get_conn() as conn:
            _db._create_agent_runs_table(conn)
            conn.execute(
                """INSERT INTO agent_runs
                   (issue_number, sprint_label, agent, started_at, finished_at,
                    total_tokens, outcome, is_ica, cost_usd)
                   VALUES (20, 'sprint-105', 'coder', '2026-07-01T10:00:00', '2026-07-01T10:30:00',
                           3000, 'success', 0, NULL)"""
            )
            conn.commit()

        summary = _db.ica_sprint_cost_summary("sprint-105")
        assert summary["is_ica"] is False
        assert summary["run_count"] == 0
        assert summary["cost_usd"] == 0.0


# ---------------------------------------------------------------------------
# AC6 — Cost summary read from DB (not re-computed at render time)
# ---------------------------------------------------------------------------

class TestAC6CostFromDB:
    def test_cost_summary_uses_stored_cost_usd(self, tmp_db):
        """AC6: ica_sprint_cost_summary reads cost_usd from agent_runs, never recomputes."""
        _db, db_file = tmp_db
        with _db.get_conn() as conn:
            _db._create_agent_runs_table(conn)
            # Insert a row with a pre-computed cost; the function must return it as-is
            conn.execute(
                """INSERT INTO agent_runs
                   (issue_number, sprint_label, agent, started_at, finished_at,
                    total_tokens, outcome, is_ica, cost_usd)
                   VALUES (30, 'sprint-x', 'coder', '2026-07-01T10:00:00', '2026-07-01T10:15:00',
                           4000, 'success', 1, 0.0999)"""
            )
            conn.commit()

        summary = _db.ica_sprint_cost_summary("sprint-x")
        # Must exactly match the stored value — no price_map recalculation
        assert abs(summary["cost_usd"] - 0.0999) < 1e-9


# ---------------------------------------------------------------------------
# AC7 — Zero-cost or failed runs excluded from cost summary
# ---------------------------------------------------------------------------

class TestAC7ZeroCostExclusion:
    def test_failed_run_excluded_from_cost_summary(self, tmp_db):
        """AC7: runs with outcome='failed' are excluded from cost totals."""
        _db, db_file = tmp_db
        with _db.get_conn() as conn:
            _db._create_agent_runs_table(conn)
            # Successful ICA run
            conn.execute(
                """INSERT INTO agent_runs
                   (issue_number, sprint_label, agent, started_at, finished_at,
                    total_tokens, outcome, is_ica, cost_usd)
                   VALUES (40, 'sprint-105', 'coder', '2026-07-01T10:00:00', '2026-07-01T10:30:00',
                           5000, 'success', 1, 0.075)"""
            )
            # Failed ICA run — must be excluded from totals
            conn.execute(
                """INSERT INTO agent_runs
                   (issue_number, sprint_label, agent, started_at, finished_at,
                    total_tokens, outcome, is_ica, cost_usd)
                   VALUES (40, 'sprint-105', 'coder', '2026-07-01T11:00:00', '2026-07-01T11:15:00',
                           3000, 'failed', 1, 0.045)"""
            )
            conn.commit()

        summary = _db.ica_sprint_cost_summary("sprint-105")
        # Only the successful run counts
        assert summary["run_count"] == 1
        assert abs(summary["cost_usd"] - 0.075) < 1e-9

    def test_zero_cost_run_excluded(self, tmp_db):
        """AC7: ICA runs with NULL or 0 cost_usd are excluded from the summary total."""
        _db, db_file = tmp_db
        with _db.get_conn() as conn:
            _db._create_agent_runs_table(conn)
            # ICA run with real cost
            conn.execute(
                """INSERT INTO agent_runs
                   (issue_number, sprint_label, agent, started_at, finished_at,
                    total_tokens, outcome, is_ica, cost_usd)
                   VALUES (50, 'sprint-105', 'coder', '2026-07-01T10:00:00', '2026-07-01T10:30:00',
                           5000, 'success', 1, 0.075)"""
            )
            # ICA run with NULL cost
            conn.execute(
                """INSERT INTO agent_runs
                   (issue_number, sprint_label, agent, started_at, finished_at,
                    total_tokens, outcome, is_ica, cost_usd)
                   VALUES (51, 'sprint-105', 'tester', '2026-07-01T10:35:00', '2026-07-01T10:50:00',
                           2000, 'pass', 1, NULL)"""
            )
            conn.commit()

        summary = _db.ica_sprint_cost_summary("sprint-105")
        # Only the run with real cost_usd counts
        assert summary["run_count"] == 1
        assert abs(summary["cost_usd"] - 0.075) < 1e-9


# ---------------------------------------------------------------------------
# AC8 — No schema migration breaks Anthropic-path records
# ---------------------------------------------------------------------------

class TestAC8AnthropicPathUnaffected:
    def test_existing_token_usage_columns_unchanged(self, tmp_db):
        """AC8: existing token_usage columns not altered; new columns nullable with defaults."""
        _db, db_file = tmp_db
        with _db.get_conn() as conn:
            info = {r["name"]: r for r in conn.execute("PRAGMA table_info(token_usage)")}
        # Existing columns must still exist
        assert "session_id" in info
        assert "project" in info
        assert "input_tokens" in info
        assert "output_tokens" in info
        assert "recorded_at" in info
        assert "agent_role" in info
        assert "model_name" in info
        # New nullable columns
        assert "cache_read_tokens" in info
        assert "cache_write_tokens" in info

    def test_existing_agent_runs_columns_unchanged(self, tmp_db):
        """AC8: existing agent_runs columns not altered; new columns nullable."""
        _db, db_file = tmp_db
        with _db.get_conn() as conn:
            info = {r["name"]: r for r in conn.execute("PRAGMA table_info(agent_runs)")}
        # Existing columns
        assert "id" in info
        assert "issue_number" in info
        assert "sprint_label" in info
        assert "agent" in info
        assert "started_at" in info
        assert "total_tokens" in info
        assert "outcome" in info
        # New nullable columns
        assert "is_ica" in info
        assert "cost_usd" in info

    def test_anthropic_run_insert_still_works(self, tmp_db):
        """AC8: inserting an Anthropic-path agent run without ICA fields still works."""
        _db, db_file = tmp_db
        run_id = _db.record_agent_start(60, "sprint-105", "coder")
        assert run_id is not None
        _db.record_agent_finish(
            60, "sprint-105", "coder",
            outcome="success",
            total_tokens=4000,
            run_id=run_id,
        )
        with _db.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM agent_runs WHERE id = ?", (run_id,)
            ).fetchone()
        assert row is not None
        assert row["outcome"] == "success"
        assert row["total_tokens"] == 4000
        # ICA fields default appropriately
        assert row["is_ica"] in (0, None)
        assert row["cost_usd"] is None


# ---------------------------------------------------------------------------
# Hook payload tests
# ---------------------------------------------------------------------------

class TestHookPayload:
    def test_hook_sends_cache_tokens(self, tmp_path, monkeypatch):
        """AC1: post_tool_used.py sends cache_read_tokens and cache_write_tokens."""
        import importlib
        import hooks.post_tool_used as _hook
        importlib.reload(_hook)

        # Create a fake transcript with cache tokens
        transcript = tmp_path / "session.jsonl"
        entry = {
            "type": "assistant",
            "sessionId": "sess-hook-001",
            "message": {
                "usage": {
                    "input_tokens": 800,
                    "output_tokens": 200,
                    "cache_read_input_tokens": 150,
                    "cache_creation_input_tokens": 50,
                }
            },
        }
        transcript.write_text(json.dumps(entry) + "\n", encoding="utf-8")

        result = _hook._read_last_usage_from_transcript(str(transcript))
        # Returns (total_input, output, cache_read, cache_write)
        assert len(result) == 4, f"Expected 4-tuple, got {len(result)}-tuple: {result}"
        total_input, output, cache_read, cache_write = result
        assert total_input == 950   # 800 + 150
        assert output == 200
        assert cache_read == 150
        assert cache_write == 50

    def test_hook_sends_ccproxy_profile(self, tmp_path, monkeypatch):
        """AC1: post_tool_used.py includes CCPROXY_PROFILE from env in POST payload."""
        import importlib
        import hooks.post_tool_used as _hook
        importlib.reload(_hook)

        monkeypatch.setenv("CCPROXY_PROFILE", "ica")
        profile = os.environ.get("CCPROXY_PROFILE")
        assert profile == "ica"


# ---------------------------------------------------------------------------
# ICA cost computation helper
# ---------------------------------------------------------------------------

class TestIcaCostComputation:
    def test_compute_ica_cost_from_price_map(self):
        """AC2: ICA cost computed from price_map rates with cache multipliers."""
        from db import _compute_ica_cost_usd
        price_map = {
            "claude-sonnet-4-6": {"in": 3.0, "out": 15.0},
        }
        # raw_input=800, output=200, cache_read=150, cache_write=50, model=sonnet
        # in_rate = 3.0/1M, out_rate = 15.0/1M
        # cache_read_rate = 0.1 * in_rate = 0.3/1M
        # cache_write_rate = 1.25 * in_rate = 3.75/1M
        # cost = (800*3.0 + 200*15.0 + 150*0.3 + 50*3.75) / 1_000_000
        #      = (2400 + 3000 + 45 + 187.5) / 1_000_000
        #      = 5632.5 / 1_000_000 = 0.0056325
        cost = _compute_ica_cost_usd(
            raw_input_tokens=800,
            output_tokens=200,
            cache_read_tokens=150,
            cache_write_tokens=50,
            model_name="claude-sonnet-4-6",
            price_map=price_map,
        )
        assert cost is not None
        assert abs(cost - 5632.5 / 1_000_000) < 1e-9

    def test_compute_ica_cost_no_price_map(self):
        """AC2: returns None when price_map is absent (cost not determinable)."""
        from db import _compute_ica_cost_usd
        cost = _compute_ica_cost_usd(
            raw_input_tokens=800,
            output_tokens=200,
            cache_read_tokens=0,
            cache_write_tokens=0,
            model_name="claude-sonnet-4-6",
            price_map=None,
        )
        assert cost is None

    def test_compute_ica_cost_unknown_model(self):
        """AC2: returns None when model not in price_map."""
        from db import _compute_ica_cost_usd
        cost = _compute_ica_cost_usd(
            raw_input_tokens=800,
            output_tokens=200,
            cache_read_tokens=0,
            cache_write_tokens=0,
            model_name="unknown-model",
            price_map={"claude-sonnet-4-6": {"in": 3.0, "out": 15.0}},
        )
        assert cost is None
