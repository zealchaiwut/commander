"""Tests for issue #756: Read dashboard state from local DB, not GitHub.

Each test is anchored to a specific acceptance criterion.

AC-1: Read path (project view / sprint cards / running / finish-rerun preview)
      serves issue data from the DB mirror — zero live GitHub (`gh`) calls on
      render once the mirror is populated.
AC-2: An `issues` mirror table exists (issue_number, title, state, labels JSON,
      updated_at); schema created/migrated on startup if absent.
AC-3: `github_events_sync.sync_issues_mirror` uses If-None-Match / ETag
      conditional requests: 200 -> upsert mirror, 304 -> no-op.
AC-4: Sync interval configurable via SYNC_INTERVAL_SECONDS, default <= 60 s.
AC-5: Rate-limit quota is not consumed on render (no live call -> counter steady).
AC-6: A label edited directly on github.com appears in the mirror within one
      sync (a 200 with new labels upserts the new label set).
AC-7: An unchanged poll returns 304 and consumes zero rate-limit quota (no
      upsert, ETag preserved).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))

import db as _db_module  # noqa: E402
import github_events_sync  # noqa: E402
import github_client  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path):
    """Isolated SQLite DB; yields the patched db module."""
    db_file = tmp_path / "test_756.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


# ── REST-shaped issue payloads (GitHub /repos/{o}/{r}/issues) ─────────────────

def _rest_issue(number, title="t", state="open", labels=None, updated_at="2026-06-10T00:00:00Z"):
    return {
        "number": number,
        "title": title,
        "state": state,
        "labels": [{"name": n, "color": "ededed"} for n in (labels or [])],
        "assignees": [],
        "html_url": f"https://github.com/o/r/issues/{number}",
        "created_at": "2026-06-01T00:00:00Z",
        "updated_at": updated_at,
        "body": "body",
    }


def _fake_resp(status_code, *, etag="\"abc\"", remaining=4999, body=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = {
        "ETag": etag,
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Reset": "0",
    }
    resp.json.return_value = body if body is not None else []
    resp.raise_for_status.return_value = None
    return resp


# ── AC-2: issues mirror table schema ──────────────────────────────────────────

class TestMirrorTableSchema:
    def test_issues_table_exists(self, fresh_db):
        with fresh_db.get_conn() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='issues'"
            ).fetchone()
        assert row is not None, "issues mirror table not created by init_db()"

    def test_issues_table_has_required_columns(self, fresh_db):
        with fresh_db.get_conn() as conn:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(issues)").fetchall()}
        for required in ("issue_number", "title", "state", "labels", "updated_at"):
            assert required in cols, f"issues table missing column {required!r}"

    def test_schema_migrated_when_absent(self, fresh_db):
        """Dropping the table and re-running init_db re-creates it (startup migration)."""
        with fresh_db.get_conn() as conn:
            conn.execute("DROP TABLE issues")
            conn.commit()
        fresh_db.init_db()
        with fresh_db.get_conn() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='issues'"
            ).fetchone()
        assert row is not None, "init_db did not re-create the issues table"

    def test_labels_round_trip_as_json(self, fresh_db):
        fresh_db.upsert_issues("o/r", [_normalised(1, labels=["sprint-57", "SIT"])])
        rows = fresh_db.get_mirrored_issues("o/r")
        assert len(rows) == 1
        names = {l["name"] for l in rows[0]["labels"]}
        assert names == {"sprint-57", "SIT"}


def _normalised(number, title="t", state="open", labels=None, updated_at="2026-06-10T00:00:00Z"):
    """gh-CLI-shaped issue dict as stored by the mirror."""
    return {
        "number": number,
        "title": title,
        "state": state,
        "labels": [{"name": n, "color": "ededed"} for n in (labels or [])],
        "assignees": [],
        "url": f"https://github.com/o/r/issues/{number}",
        "createdAt": "2026-06-01T00:00:00Z",
        "updatedAt": updated_at,
        "body": "body",
    }


# ── AC-3 / AC-7: ETag conditional sync ────────────────────────────────────────

class TestConditionalSync:
    def test_200_upserts_mirror(self, fresh_db):
        body = [_rest_issue(1, labels=["sprint-57"]), _rest_issue(2, labels=["SIT"])]
        with patch.object(github_events_sync, "_get_gh_token", return_value="tok"), \
             patch.object(github_events_sync.httpx, "get",
                          return_value=_fake_resp(200, etag="\"v1\"", body=body)):
            result = github_events_sync.sync_issues_mirror("o/r", db_module=fresh_db)
        assert result["status"] == 200
        assert result["synced"] == 2
        rows = fresh_db.get_mirrored_issues("o/r")
        assert {r["number"] for r in rows} == {1, 2}

    def test_200_stores_etag(self, fresh_db):
        body = [_rest_issue(1)]
        with patch.object(github_events_sync, "_get_gh_token", return_value="tok"), \
             patch.object(github_events_sync.httpx, "get",
                          return_value=_fake_resp(200, etag="\"v9\"", body=body)):
            github_events_sync.sync_issues_mirror("o/r", db_module=fresh_db)
        assert fresh_db.get_sync_etag("issues:o/r") == "\"v9\""

    def test_304_is_noop(self, fresh_db):
        # Seed mirror + etag from a first 200.
        body = [_rest_issue(1, title="orig")]
        with patch.object(github_events_sync, "_get_gh_token", return_value="tok"), \
             patch.object(github_events_sync.httpx, "get",
                          return_value=_fake_resp(200, etag="\"v1\"", body=body)):
            github_events_sync.sync_issues_mirror("o/r", db_module=fresh_db)

        # Second poll returns 304 -> no change to the mirror.
        with patch.object(github_events_sync, "_get_gh_token", return_value="tok"), \
             patch.object(github_events_sync.httpx, "get",
                          return_value=_fake_resp(304, etag="\"v1\"")) as mock_get:
            result = github_events_sync.sync_issues_mirror("o/r", db_module=fresh_db)

        assert result["status"] == 304
        assert result["synced"] == 0
        rows = fresh_db.get_mirrored_issues("o/r")
        assert len(rows) == 1 and rows[0]["title"] == "orig", "304 must not mutate mirror"

    def test_304_sends_if_none_match_with_stored_etag(self, fresh_db):
        fresh_db.set_sync_etag("issues:o/r", "\"stored\"")
        with patch.object(github_events_sync, "_get_gh_token", return_value="tok"), \
             patch.object(github_events_sync.httpx, "get",
                          return_value=_fake_resp(304, etag="\"stored\"")) as mock_get:
            github_events_sync.sync_issues_mirror("o/r", db_module=fresh_db)
        _, kwargs = mock_get.call_args
        headers = kwargs.get("headers", {})
        assert headers.get("If-None-Match") == "\"stored\"", \
            "stored ETag must be sent as If-None-Match on the next poll"

    def test_304_preserves_etag(self, fresh_db):
        fresh_db.set_sync_etag("issues:o/r", "\"keep\"")
        with patch.object(github_events_sync, "_get_gh_token", return_value="tok"), \
             patch.object(github_events_sync.httpx, "get",
                          return_value=_fake_resp(304, etag="\"keep\"")):
            github_events_sync.sync_issues_mirror("o/r", db_module=fresh_db)
        assert fresh_db.get_sync_etag("issues:o/r") == "\"keep\""


# ── AC-6: external label edit reflected after sync ────────────────────────────

class TestExternalEditReconciled:
    def test_label_edit_appears_after_sync(self, fresh_db):
        # First sync: issue has only sprint-57.
        first = [_rest_issue(1, labels=["sprint-57"], updated_at="2026-06-10T00:00:00Z")]
        with patch.object(github_events_sync, "_get_gh_token", return_value="tok"), \
             patch.object(github_events_sync.httpx, "get",
                          return_value=_fake_resp(200, etag="\"v1\"", body=first)):
            github_events_sync.sync_issues_mirror("o/r", db_module=fresh_db)

        # Label edited on github.com -> next 200 carries the new label set.
        second = [_rest_issue(1, labels=["sprint-57", "SIT"], updated_at="2026-06-10T01:00:00Z")]
        with patch.object(github_events_sync, "_get_gh_token", return_value="tok"), \
             patch.object(github_events_sync.httpx, "get",
                          return_value=_fake_resp(200, etag="\"v2\"", body=second)):
            github_events_sync.sync_issues_mirror("o/r", db_module=fresh_db)

        rows = fresh_db.get_mirrored_issues("o/r")
        names = {l["name"] for l in rows[0]["labels"]}
        assert "SIT" in names, "externally added label not reflected in mirror after sync"


# ── AC-4: configurable sync interval, default <= 60 s ─────────────────────────

class TestSyncInterval:
    def test_default_interval_at_most_60(self, monkeypatch):
        monkeypatch.delenv("SYNC_INTERVAL_SECONDS", raising=False)
        assert github_events_sync.get_sync_interval() <= 60

    def test_default_interval_positive(self, monkeypatch):
        monkeypatch.delenv("SYNC_INTERVAL_SECONDS", raising=False)
        assert github_events_sync.get_sync_interval() > 0

    def test_env_overrides_interval(self, monkeypatch):
        monkeypatch.setenv("SYNC_INTERVAL_SECONDS", "30")
        assert github_events_sync.get_sync_interval() == 30

    def test_invalid_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("SYNC_INTERVAL_SECONDS", "not-a-number")
        assert github_events_sync.get_sync_interval() <= 60


# ── AC-3 (loop): periodic lifespan sync loop ──────────────────────────────────

class TestSyncLoop:
    def test_loop_runs_sync_each_iteration(self, monkeypatch):
        monkeypatch.setenv("SYNC_INTERVAL_SECONDS", "1")
        calls = []

        def fake_sync(repo, db_module=None):
            calls.append(repo)
            return {"status": 200, "synced": 0}

        monkeypatch.setattr(github_events_sync, "sync_issues_mirror", fake_sync)

        async def _no_sleep(_):
            return None

        monkeypatch.setattr(github_events_sync.asyncio, "sleep", _no_sleep)

        asyncio.run(
            github_events_sync.run_issues_sync_loop(["o/r"], db_module=None, iterations=3)
        )
        assert calls == ["o/r", "o/r", "o/r"]


# ── AC-1 / AC-5: read path served from DB mirror, no live gh call ─────────────

class TestReadFromMirror:
    def _no_gh(self):
        """Patch the gh subprocess wrappers to fail loudly if a render touches gh."""
        def _boom(*a, **k):
            raise AssertionError("render made a live gh call: %r" % (a,))
        return patch.multiple(github_client, _json=_boom, _run=_boom)

    def test_list_issues_served_from_mirror(self, fresh_db, monkeypatch):
        monkeypatch.setattr(github_client, "_r", lambda r: "o/r")
        fresh_db.upsert_issues("o/r", [
            _normalised(1, labels=["sprint-57"]),
            _normalised(2, labels=["sprint-99"]),
        ])
        github_client.invalidate()
        with self._no_gh():
            issues = github_client.list_issues(57, repo_name="o/r")
        nums = {i["number"] for i in issues}
        assert nums == {1}, "list_issues must filter sprint from the mirror without gh"
        assert issues[0]["column"] == "in-progress" or "column" in issues[0]

    def test_list_all_open_issues_served_from_mirror(self, fresh_db, monkeypatch):
        monkeypatch.setattr(github_client, "_r", lambda r: "o/r")
        fresh_db.upsert_issues("o/r", [
            _normalised(1, state="open"),
            _normalised(2, state="closed"),
        ])
        github_client.invalidate()
        with self._no_gh():
            issues = github_client.list_all_open_issues(repo_name="o/r")
        nums = {i["number"] for i in issues}
        assert nums == {1}, "closed issues must be excluded; no gh call allowed"

    def test_get_issue_served_from_mirror(self, fresh_db, monkeypatch):
        monkeypatch.setattr(github_client, "_r", lambda r: "o/r")
        fresh_db.upsert_issues("o/r", [_normalised(7, title="seven", labels=["SIT"])])
        github_client.invalidate()
        with self._no_gh():
            issue = github_client.get_issue(7, repo_name="o/r")
        assert issue["number"] == 7
        assert issue["title"] == "seven"

    def test_empty_mirror_falls_back_to_gh(self, fresh_db, monkeypatch):
        """When the mirror has no rows, the read path may still use gh (bootstrap)."""
        monkeypatch.setattr(github_client, "_r", lambda r: "o/r")
        github_client.invalidate()
        sentinel = [{"number": 99, "title": "live", "labels": [], "state": "open"}]
        with patch.object(github_client, "_json", return_value=sentinel):
            issues = github_client.list_all_open_issues(repo_name="o/r")
        assert {i["number"] for i in issues} == {99}
