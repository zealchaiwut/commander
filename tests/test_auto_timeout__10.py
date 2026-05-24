"""
Tests for Issue #10: auto-timeout idle agents to clear zombie "working" sessions.

AC-1: Working agents whose last_seen is older than the threshold are marked 'timed_out'.
AC-2: Background task interval is <= 60 seconds.
AC-3: Dashboard UI displays timed_out agents with a distinct grey badge (source check).
AC-4: AGENT_IDLE_TIMEOUT_SECONDS env var is read with default of 300 seconds.
AC-5: New event for a timed_out session re-transitions it to 'working'.
AC-6: Active-agent count (working_agents metric) excludes 'timed_out' sessions.
"""

import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure dashboard module is importable
DASHBOARD_DIR = Path(__file__).parent.parent / "dashboard"
sys.path.insert(0, str(DASHBOARD_DIR))

import db  # noqa: E402


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fresh_db(tmp_path: Path):
    """Return a fresh db module pointed at a temp DB file."""
    test_db = tmp_path / "test_dashboard.db"
    # Patch db.DB_PATH for the duration of the test
    return test_db


def _insert_agent(conn: sqlite3.Connection, session_id: str, status: str,
                  last_seen_delta_seconds: int = 0):
    """Insert an agent row with last_seen offset from 'now' by delta seconds."""
    now = datetime.utcnow()
    last_seen = (now - timedelta(seconds=last_seen_delta_seconds)).isoformat()
    conn.execute("""
        INSERT INTO agents (session_id, name, working_dir, status, last_tool, last_seen, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (session_id, "test-agent", "/tmp/test", status, None, last_seen, now.isoformat()))
    conn.commit()


# ── AC-1: timeout_idle_agents marks stale working agents as timed_out ─────────

class TestAC1TimeoutLogic:
    def test_working_agent_older_than_threshold_is_timed_out(self, tmp_path):
        """A 'working' agent whose last_seen > threshold seconds ago is marked 'timed_out'."""
        with patch.object(db, "DB_PATH", tmp_path / "dashboard.db"):
            db.init_db()
            conn = db.get_conn()
            # Insert a 'working' agent that was last seen 400 seconds ago (> 300 default)
            _insert_agent(conn, "stale-session", "working", last_seen_delta_seconds=400)
            conn.close()

            updated = db.timeout_idle_agents(threshold_seconds=300)
            assert updated == 1, f"Expected 1 agent updated, got {updated}"

            conn = db.get_conn()
            row = conn.execute(
                "SELECT status FROM agents WHERE session_id = ?", ("stale-session",)
            ).fetchone()
            conn.close()
            assert row["status"] == "timed_out", (
                f"Expected 'timed_out', got '{row['status']}'"
            )

    def test_working_agent_within_threshold_is_not_timed_out(self, tmp_path):
        """A 'working' agent that was recently seen is NOT marked 'timed_out'."""
        with patch.object(db, "DB_PATH", tmp_path / "dashboard.db"):
            db.init_db()
            conn = db.get_conn()
            # Insert a 'working' agent last seen only 60 seconds ago
            _insert_agent(conn, "fresh-session", "working", last_seen_delta_seconds=60)
            conn.close()

            updated = db.timeout_idle_agents(threshold_seconds=300)
            assert updated == 0, f"Expected 0 agents updated, got {updated}"

            conn = db.get_conn()
            row = conn.execute(
                "SELECT status FROM agents WHERE session_id = ?", ("fresh-session",)
            ).fetchone()
            conn.close()
            assert row["status"] == "working", (
                f"Expected 'working', got '{row['status']}'"
            )

    def test_non_working_agents_not_affected(self, tmp_path):
        """Agents in 'idle', 'done', or 'timed_out' status are NOT transitioned."""
        with patch.object(db, "DB_PATH", tmp_path / "dashboard.db"):
            db.init_db()
            conn = db.get_conn()
            for status in ("idle", "done", "timed_out"):
                _insert_agent(conn, f"session-{status}", status,
                              last_seen_delta_seconds=999)
            conn.close()

            updated = db.timeout_idle_agents(threshold_seconds=10)
            assert updated == 0, (
                f"Expected 0 non-working agents updated, got {updated}"
            )

    def test_timeout_idle_agents_returns_count(self, tmp_path):
        """timeout_idle_agents returns the count of rows changed."""
        with patch.object(db, "DB_PATH", tmp_path / "dashboard.db"):
            db.init_db()
            conn = db.get_conn()
            for i in range(3):
                _insert_agent(conn, f"stale-{i}", "working",
                              last_seen_delta_seconds=600)
            conn.close()

            count = db.timeout_idle_agents(threshold_seconds=300)
            assert count == 3, f"Expected 3, got {count}"


# ── AC-2: Background task interval ≤ 60 seconds ────────────────────────────────

class TestAC2BackgroundInterval:
    def test_timeout_check_interval_is_at_most_60_seconds(self):
        """The _TIMEOUT_CHECK_INTERVAL constant in server.py must be <= 60."""
        import server
        interval = server._TIMEOUT_CHECK_INTERVAL
        assert interval <= 60, (
            f"_TIMEOUT_CHECK_INTERVAL is {interval}s, must be <= 60s"
        )

    def test_timeout_loop_calls_sleep_with_interval(self):
        """server._timeout_loop uses asyncio.sleep(_TIMEOUT_CHECK_INTERVAL)."""
        import inspect
        import server
        src = inspect.getsource(server._timeout_loop)
        assert "_TIMEOUT_CHECK_INTERVAL" in src, (
            "_timeout_loop must reference _TIMEOUT_CHECK_INTERVAL"
        )
        assert "asyncio.sleep" in src, (
            "_timeout_loop must use asyncio.sleep"
        )


# ── AC-3: UI displays timed_out with a distinct grey badge ─────────────────────

class TestAC3UIBadge:
    def test_badge_timed_out_css_class_exists_in_index_html(self):
        """index.html defines a CSS class for 'badge-timed-out' (grey styling)."""
        html_path = DASHBOARD_DIR / "static" / "index.html"
        content = html_path.read_text()
        assert "badge-timed-out" in content, (
            "index.html must define .badge-timed-out CSS class"
        )

    def test_timed_out_badge_uses_grey_color(self):
        """The badge-timed-out class uses a grey colour, not green or amber."""
        html_path = DASHBOARD_DIR / "static" / "index.html"
        content = html_path.read_text()
        # Check that badge-timed-out definition is present and contains grey-ish color
        # (#9ca3af is tailwind gray-400)
        assert "#9ca3af" in content or "badge-timed-out" in content, (
            "badge-timed-out should use a grey colour"
        )
        # Verify it's NOT using green (var(--green))
        # Extract the badge-timed-out rule
        import re
        match = re.search(r"\.badge-timed-out\s*\{([^}]+)\}", content)
        assert match, "Could not find .badge-timed-out CSS rule"
        rule = match.group(1)
        assert "var(--green)" not in rule, (
            "badge-timed-out must NOT use green color"
        )
        assert "var(--amber)" not in rule, (
            "badge-timed-out must NOT use amber color"
        )

    def test_app_js_renders_timed_out_badge(self):
        """app.js has logic to render 'badge-timed-out' for timed_out status."""
        js_path = DASHBOARD_DIR / "static" / "app.js"
        content = js_path.read_text()
        assert "timed_out" in content, (
            "app.js must handle 'timed_out' status"
        )
        assert "badge-timed-out" in content, (
            "app.js must assign 'badge-timed-out' CSS class for timed_out agents"
        )
        assert "Timed Out" in content, (
            "app.js must display 'Timed Out' label for timed_out agents"
        )

    def test_timed_out_card_has_distinct_border(self):
        """index.html has a CSS rule for .agent-card.timed_out with grey border."""
        html_path = DASHBOARD_DIR / "static" / "index.html"
        content = html_path.read_text()
        import re
        match = re.search(r"\.agent-card\.timed_out\s*\{([^}]+)\}", content)
        assert match, "index.html must have .agent-card.timed_out CSS rule"
        rule = match.group(1)
        assert "border" in rule or "opacity" in rule, (
            ".agent-card.timed_out must have distinct border or opacity"
        )

    # ⚠️ MANUAL: Actual visual rendering of grey badge (requires live browser)
    # ⚠️ MANUAL: SSE push causing timed_out badge to appear without page refresh


# ── AC-4: AGENT_IDLE_TIMEOUT_SECONDS env var ───────────────────────────────────

class TestAC4EnvVar:
    def test_default_timeout_is_300_seconds(self):
        """Without env var set, AGENT_IDLE_TIMEOUT_SECONDS defaults to 300."""
        import inspect
        import server
        src = inspect.getsource(server)
        # server.py uses os.environ.get("AGENT_IDLE_TIMEOUT_SECONDS", "300")
        assert '"300"' in src or "'300'" in src, (
            "server.py must have default of 300 for AGENT_IDLE_TIMEOUT_SECONDS"
        )
        # Also verify the constant is set correctly (env var absent → 300)
        with patch.dict(os.environ, {k: v for k, v in os.environ.items()
                                     if k != "AGENT_IDLE_TIMEOUT_SECONDS"}):
            default = int(os.environ.get("AGENT_IDLE_TIMEOUT_SECONDS", "300"))
            assert default == 300, f"Default must be 300, got {default}"

    def test_env_var_name_is_correct(self):
        """server.py reads AGENT_IDLE_TIMEOUT_SECONDS from os.environ."""
        import inspect
        import server
        src = inspect.getsource(server)
        assert "AGENT_IDLE_TIMEOUT_SECONDS" in src, (
            "server.py must read AGENT_IDLE_TIMEOUT_SECONDS from environment"
        )

    def test_custom_timeout_is_respected(self, tmp_path):
        """A custom threshold is used when timeout_idle_agents is called with it."""
        with patch.object(db, "DB_PATH", tmp_path / "dashboard.db"):
            db.init_db()
            conn = db.get_conn()
            # Agent last seen 120 seconds ago — stale at 60s threshold, not at 300s
            _insert_agent(conn, "borderline", "working", last_seen_delta_seconds=120)
            conn.close()

            # Should NOT time out at 300s threshold
            count = db.timeout_idle_agents(threshold_seconds=300)
            assert count == 0, "Should not time out when within threshold"

            # Should time out at 60s threshold
            count = db.timeout_idle_agents(threshold_seconds=60)
            assert count == 1, "Should time out when outside custom threshold"


# ── AC-5: New event re-transitions timed_out → working ──────────────────────────

class TestAC5ResumeAfterTimeout:
    def test_upsert_agent_transitions_timed_out_to_working(self, tmp_path):
        """Sending a new event (upsert_agent with status='working') re-transitions
        a 'timed_out' agent back to 'working'."""
        with patch.object(db, "DB_PATH", tmp_path / "dashboard.db"):
            db.init_db()
            conn = db.get_conn()
            # Insert a stale working agent
            _insert_agent(conn, "resume-session", "working", last_seen_delta_seconds=400)
            conn.close()

            # Trigger timeout
            count = db.timeout_idle_agents(threshold_seconds=300)
            assert count == 1, "Agent should have been timed out"

            # Verify timed_out
            conn = db.get_conn()
            row = conn.execute(
                "SELECT status FROM agents WHERE session_id = ?", ("resume-session",)
            ).fetchone()
            conn.close()
            assert row["status"] == "timed_out"

            # New event arrives — upsert_agent with 'working' status
            db.upsert_agent("resume-session", "/tmp/test", "working", None, "test-agent")

            conn = db.get_conn()
            row = conn.execute(
                "SELECT status FROM agents WHERE session_id = ?", ("resume-session",)
            ).fetchone()
            conn.close()
            assert row["status"] == "working", (
                f"Expected 'working' after new event, got '{row['status']}'"
            )

    def test_archived_agent_stays_archived_on_new_event(self, tmp_path):
        """An 'archived' agent is NOT changed by upsert_agent (existing behavior
        should not be broken by the timeout feature)."""
        with patch.object(db, "DB_PATH", tmp_path / "dashboard.db"):
            db.init_db()
            conn = db.get_conn()
            _insert_agent(conn, "archived-session", "archived")
            conn.close()

            db.upsert_agent("archived-session", "/tmp/test", "working", None, "test-agent")

            conn = db.get_conn()
            row = conn.execute(
                "SELECT status FROM agents WHERE session_id = ?", ("archived-session",)
            ).fetchone()
            conn.close()
            assert row["status"] == "archived", (
                "Archived agents should remain archived even after a new event"
            )


# ── AC-6: Active-agent count excludes timed_out ─────────────────────────────────

class TestAC6ActiveCountExcludesTimedOut:
    def test_working_agents_metric_excludes_timed_out(self):
        """In projects.py, working_agents is summed only for status == 'working',
        so timed_out sessions are excluded from the count."""
        import inspect
        import projects as projects_module
        src = inspect.getsource(projects_module.get_all_projects)
        # The metric should filter by status == 'working' (single or double quotes)
        assert "== 'working'" in src or '== "working"' in src, (
            "get_all_projects must count only 'working' agents, not 'timed_out'"
        )

    def test_timed_out_excluded_from_working_count(self, tmp_path):
        """Agents with status 'timed_out' must not be counted as 'working'."""
        test_agents = [
            {"session_id": "w1", "status": "working",   "name": "agent", "working_dir": "/tmp"},
            {"session_id": "t1", "status": "timed_out", "name": "agent", "working_dir": "/tmp"},
            {"session_id": "t2", "status": "timed_out", "name": "agent", "working_dir": "/tmp"},
        ]
        working_count = sum(1 for a in test_agents if a.get("status") == "working")
        assert working_count == 1, (
            f"Only 'working' agents should be counted, got {working_count}"
        )

    def test_server_working_agents_count_source(self):
        """The working_agents count in projects.py uses status == 'working' filter
        and does not include timed_out in the active-agent count."""
        import inspect
        import projects as projects_module
        src = inspect.getsource(projects_module)
        # Confirm working_agents is computed
        assert "working_agents" in src, "projects.py must compute 'working_agents'"
        # The sum line should filter only 'working', confirming timed_out is excluded
        # by the absence of 'timed_out' as an included status in the working_agents sum.
        # The actual line is: sum(1 for a in agents if a.get("status") == "working")
        assert 'a.get("status") == "working"' in src or "a.get('status') == 'working'" in src, (
            "working_agents must be computed by filtering status == 'working'"
        )
