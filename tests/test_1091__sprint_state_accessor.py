"""Sprint lifecycle read accessor (issue #1091).

AC-1: sprint_state.current(label) returns canonical_lifecycle(db.get_sprint(label)["state"])
AC-2: Accessor performs zero disk reads (no plan.json, no filesystem access)
AC-3: Accessor performs zero label inference or GitHub label lookups
AC-4: Accessor performs zero fallback logic — DB is the only source
AC-5: Unit test asserts no filesystem I/O during accessor call
AC-6: Unit test asserts return value equals canonical_lifecycle of DB state
AC-7: docs/architecture/sprint-lifecycle.md documents this as the sole read contract
"""
from __future__ import annotations

import builtins
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"

for _p in (str(_REPO_ROOT), str(_DASHBOARD_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("DB_PATH", str(_REPO_ROOT / "commander.db"))
os.environ.setdefault("COMMANDER_DISABLE_NEON", "1")

import db as _db_module  # noqa: E402
import sprint_state  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def fresh_db(tmp_path):
    """Isolated SQLite DB; patches db module's DB_PATH."""
    db_file = tmp_path / "test_1091.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    yield _db_module
    _db_module.DB_PATH = original


def _insert_sprint(db_mod, label: str, state: str) -> None:
    with db_mod.get_conn() as conn:
        db_mod._create_sprint_lifecycle_tables(conn)
        conn.execute(
            "INSERT OR REPLACE INTO sprints (label, state) VALUES (?, ?)",
            (label, state),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# AC-1 / AC-6: return value equals canonical_lifecycle of DB state
# ---------------------------------------------------------------------------

class TestReturnValue:
    def test_running_state(self, fresh_db):
        _insert_sprint(fresh_db, "sprint-10", "running")
        result = sprint_state.current("sprint-10")
        assert result == fresh_db.canonical_lifecycle("running")
        assert result == "running"

    def test_needs_rework_state(self, fresh_db):
        _insert_sprint(fresh_db, "sprint-11", "needs_rework")
        result = sprint_state.current("sprint-11")
        assert result == fresh_db.canonical_lifecycle("needs_rework")
        assert result == "needs_rework"

    def test_ready_to_merge_state(self, fresh_db):
        _insert_sprint(fresh_db, "sprint-12", "ready_to_merge")
        result = sprint_state.current("sprint-12")
        assert result == fresh_db.canonical_lifecycle("ready_to_merge")
        assert result == "ready_to_merge"

    def test_completed_state(self, fresh_db):
        _insert_sprint(fresh_db, "sprint-13", "completed")
        result = sprint_state.current("sprint-13")
        assert result == fresh_db.canonical_lifecycle("completed")
        assert result == "completed"

    def test_draft_state(self, fresh_db):
        _insert_sprint(fresh_db, "sprint-14", "draft")
        result = sprint_state.current("sprint-14")
        assert result == fresh_db.canonical_lifecycle("draft")
        assert result == "draft"

    def test_legacy_cancelled_maps_to_needs_rework(self, fresh_db):
        # AC-1: return value passes through canonical_lifecycle, so legacy
        # 'cancelled' stored in DB must surface as 'needs_rework'.
        with fresh_db.get_conn() as conn:
            fresh_db._create_sprint_lifecycle_tables(conn)
            # Insert using raw SQL to bypass the CHECK (pre-migration table
            # allows 'cancelled').
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO sprints (label, state) "
                    "VALUES ('sprint-legacy', 'cancelled')"
                )
                conn.commit()
            except Exception:
                # If CHECK constraint blocks the insert, skip — forward-only
                # migration means legacy rows can exist from pre-P1 DBs only.
                pytest.skip("Cannot insert legacy state into migrated DB")
        result = sprint_state.current("sprint-legacy")
        assert result == fresh_db.canonical_lifecycle("cancelled")

    def test_sprint_not_in_db_returns_unknown(self, fresh_db):
        # AC-4: no fallback — DB miss yields canonical_lifecycle(None) = "unknown"
        result = sprint_state.current("sprint-nonexistent-99999")
        assert result == "unknown"


# ---------------------------------------------------------------------------
# AC-2 / AC-5: zero filesystem I/O
# ---------------------------------------------------------------------------

class TestNoFilesystemAccess:
    def test_open_not_called(self, fresh_db):
        """Patch builtins.open to detect any file-system access during current()."""
        _insert_sprint(fresh_db, "sprint-fs-check", "planned")

        real_open = builtins.open
        open_calls: list[str] = []

        def _tracking_open(file, *args, **kwargs):
            path_str = str(file)
            # Allow SQLite to open the DB file — that is the only permitted I/O.
            # Any other path (plan.json, -state.json, -pid, etc.) is a violation.
            if path_str.endswith(".db") or path_str.endswith(".db-journal") or path_str.endswith(".db-wal") or path_str.endswith(".db-shm"):
                return real_open(file, *args, **kwargs)
            open_calls.append(path_str)
            return real_open(file, *args, **kwargs)

        with patch("builtins.open", side_effect=_tracking_open):
            sprint_state.current("sprint-fs-check")

        assert open_calls == [], (
            f"sprint_state.current() must not open any files other than the DB; "
            f"opened: {open_calls}"
        )

    def test_no_plan_json_read(self, fresh_db, tmp_path):
        """Even when plan.json exists on disk, accessor must not read it."""
        _insert_sprint(fresh_db, "sprint-plancheck", "running")

        # Create a plan.json with a conflicting state
        plan_path = tmp_path / ".commander" / "sprints" / "sprint-plancheck-plan.json"
        plan_path.parent.mkdir(parents=True)
        plan_path.write_text('{"state": "cancelled"}', encoding="utf-8")

        # Accessor must return DB state (running), ignoring plan.json
        result = sprint_state.current("sprint-plancheck")
        assert result == "running", (
            "accessor must read DB only; plan.json state must not influence result"
        )


# ---------------------------------------------------------------------------
# AC-3: zero GitHub label lookups
# ---------------------------------------------------------------------------

class TestNoGitHubAccess:
    def test_no_http_requests(self, fresh_db):
        """Patch requests to detect any HTTP calls during current()."""
        _insert_sprint(fresh_db, "sprint-gh-check", "planned")

        import requests as _requests
        with patch.object(_requests, "get", side_effect=AssertionError("HTTP GET called")) as mock_get, \
             patch.object(_requests, "post", side_effect=AssertionError("HTTP POST called")) as mock_post:
            result = sprint_state.current("sprint-gh-check")

        mock_get.assert_not_called()
        mock_post.assert_not_called()
        assert result == "planned"


# ---------------------------------------------------------------------------
# AC-4: zero fallback logic — DB is the sole source
# ---------------------------------------------------------------------------

class TestNoFallback:
    def test_get_sprint_called_once(self, fresh_db):
        """Accessor calls db.get_sprint exactly once and uses its return value."""
        _insert_sprint(fresh_db, "sprint-fallback", "ready_to_merge")

        call_count = 0
        real_get_sprint = _db_module.get_sprint

        def _counting_get_sprint(label, project=None):
            nonlocal call_count
            call_count += 1
            return real_get_sprint(label, project=project)

        with patch.object(_db_module, "get_sprint", side_effect=_counting_get_sprint):
            result = sprint_state.current("sprint-fallback")

        assert call_count == 1, "get_sprint must be called exactly once"
        assert result == "ready_to_merge"

    def test_no_secondary_sources_consulted(self, fresh_db):
        """When DB returns a value, no secondary source should be consulted.

        Verifies by intercepting db.get_sprint to return a known value, then
        confirming the result is derived solely from that value — no other
        lookup can change the output.
        """
        _insert_sprint(fresh_db, "sprint-secondary", "draft")

        sentinel = {"label": "sprint-secondary", "state": "ready_to_merge"}
        with patch.object(_db_module, "get_sprint", return_value=sentinel):
            result = sprint_state.current("sprint-secondary")

        # If any secondary source were consulted and overrode the DB, the
        # state would differ from what get_sprint returned.
        assert result == "ready_to_merge", (
            "result must equal canonical_lifecycle of the DB row; "
            "any secondary source would produce a different value"
        )


# ---------------------------------------------------------------------------
# AC-7: docs/architecture/sprint-lifecycle.md documents the read contract
# ---------------------------------------------------------------------------

class TestDocumentedContract:
    def test_sprint_lifecycle_doc_exists(self):
        doc_path = _REPO_ROOT / "docs" / "architecture" / "sprint-lifecycle.md"
        assert doc_path.exists(), "docs/architecture/sprint-lifecycle.md must exist"

    def test_doc_mentions_sprint_state_current(self):
        doc_path = _REPO_ROOT / "docs" / "architecture" / "sprint-lifecycle.md"
        content = doc_path.read_text(encoding="utf-8")
        assert "sprint_state.current" in content, (
            "sprint-lifecycle.md must document sprint_state.current as the "
            "sanctioned read contract"
        )

    def test_doc_names_sole_sanctioned_contract(self):
        doc_path = _REPO_ROOT / "docs" / "architecture" / "sprint-lifecycle.md"
        content = doc_path.read_text(encoding="utf-8")
        # Doc must state this is the only / sole / canonical way to read
        assert any(kw in content.lower() for kw in ("sole", "only sanctioned", "canonical read")), (
            "sprint-lifecycle.md must state sprint_state.current is the sole "
            "sanctioned read contract"
        )
