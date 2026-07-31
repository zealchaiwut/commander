"""Tests for issue #760: Bootstrap full sync on first run from empty DB.

Each test is anchored to a specific acceptance criterion.

AC-1: On startup, server detects empty/absent commander.db (or missing
      schema-marker row) and triggers a full GitHub sync automatically.
AC-2: Issues, labels, statuses, and sprint membership populate without any
      manual steps.
AC-3: Sync progress is logged to stdout/server log during bootstrap.
AC-4: After bootstrap completes, server logs the restore notice:
      `history/order/settings start empty — restore from backup if migrating
      (backup restore / restore-db)`.
AC-5: A schema-marker row is written on successful completion of the bootstrap.
AC-6: Second server start detects the marker and skips the full sync,
      proceeding directly to the ETag loop.
AC-7: No crash or unhandled exception on empty or absent DB at startup.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")

import db as _db_module  # noqa: E402
import github_events_sync  # noqa: E402

NOTICE = (
    "history/order/settings start empty — restore from backup if migrating "
    "(backup restore / restore-db)"
)


@pytest.fixture
def fresh_db(tmp_path):
    """Isolated SQLite DB; yields the patched db module."""
    db_file = tmp_path / "test_760.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


@pytest.fixture
def absent_db(tmp_path):
    """db module pointed at a path with NO file on disk (fresh clone)."""
    db_file = tmp_path / "never_created.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    yield _db_module, db_file
    _db_module.DB_PATH = original


def _rest_issue(number, title="t", state="open", labels=None,
                updated_at="2026-06-10T00:00:00Z"):
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


def _fake_resp(status_code, *, etag='"abc"', remaining=4999, body=None):
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


def _patch_200(body):
    """Context managers that make every sync_issues_mirror call return 200 + body."""
    return (
        patch.object(github_events_sync, "_get_gh_token", return_value="tok"),
        patch.object(github_events_sync.httpx, "get",
                     return_value=_fake_resp(200, etag='"v1"', body=body)),
    )


# ── AC-1: empty DB / missing marker triggers a full sync ──────────────────────

class TestBootstrapDetection:
    def test_fresh_db_has_no_marker(self, fresh_db):
        assert fresh_db.is_bootstrap_complete() is False

    def test_bootstrap_runs_sync_for_each_repo_when_marker_absent(self, fresh_db):
        calls = []

        def fake_sync(repo, db_module=None):
            calls.append(repo)
            return {"status": 200, "synced": 0}

        with patch.object(github_events_sync, "sync_issues_mirror", fake_sync):
            result = github_events_sync.bootstrap_full_sync(
                ["o/r1", "o/r2"], db_module=fresh_db
            )
        assert calls == ["o/r1", "o/r2"]
        assert result["bootstrapped"] is True
        assert result["skipped"] is False


# ── AC-2: issues / labels / statuses / sprint membership populate ─────────────

class TestMirrorPopulated:
    def test_bootstrap_populates_issues_and_labels(self, fresh_db):
        body = [
            _rest_issue(1, labels=["sprint-57", "SIT"]),
            _rest_issue(2, labels=["sprint-57", "in-progress"]),
        ]
        token_p, get_p = _patch_200(body)
        with token_p, get_p:
            github_events_sync.bootstrap_full_sync(["o/r"], db_module=fresh_db)

        rows = fresh_db.get_mirrored_issues("o/r")
        assert {r["number"] for r in rows} == {1, 2}
        # labels (and sprint membership via the sprint-57 label) are present
        labels_by_num = {r["number"]: {l["name"] for l in r["labels"]} for r in rows}
        assert "sprint-57" in labels_by_num[1]
        assert "SIT" in labels_by_num[1]
        # statuses preserved
        assert all(r["state"] == "open" for r in rows)


# ── AC-3: progress logged during bootstrap ────────────────────────────────────

class TestProgressLogged:
    def test_progress_lines_printed_to_stdout(self, fresh_db, capsys):
        token_p, get_p = _patch_200([_rest_issue(1)])
        with token_p, get_p:
            github_events_sync.bootstrap_full_sync(["o/r"], db_module=fresh_db)
        out = capsys.readouterr().out
        assert "[bootstrap]" in out
        assert "syncing o/r" in out

    def test_progress_logged_to_logger(self, fresh_db, caplog):
        import logging
        token_p, get_p = _patch_200([_rest_issue(1)])
        with caplog.at_level(logging.INFO, logger=github_events_sync.logger.name):
            with token_p, get_p:
                github_events_sync.bootstrap_full_sync(["o/r"], db_module=fresh_db)
        assert any("[bootstrap]" in r.message for r in caplog.records)


# ── AC-4: restore notice logged after bootstrap ───────────────────────────────

class TestRestoreNotice:
    def test_notice_exact_string_printed(self, fresh_db, capsys):
        token_p, get_p = _patch_200([_rest_issue(1)])
        with token_p, get_p:
            github_events_sync.bootstrap_full_sync(["o/r"], db_module=fresh_db)
        out = capsys.readouterr().out
        assert NOTICE in out

    def test_notice_constant_matches_spec(self):
        assert github_events_sync.BOOTSTRAP_RESTORE_NOTICE == NOTICE


# ── AC-5: schema-marker row written on success ────────────────────────────────

class TestMarkerWritten:
    def test_marker_present_after_successful_bootstrap(self, fresh_db):
        token_p, get_p = _patch_200([_rest_issue(1)])
        with token_p, get_p:
            github_events_sync.bootstrap_full_sync(["o/r"], db_module=fresh_db)
        assert fresh_db.is_bootstrap_complete() is True

    def test_marker_row_in_sync_state_table(self, fresh_db):
        token_p, get_p = _patch_200([_rest_issue(1)])
        with token_p, get_p:
            github_events_sync.bootstrap_full_sync(["o/r"], db_module=fresh_db)
        with fresh_db.get_conn() as conn:
            row = conn.execute(
                "SELECT key FROM sync_state WHERE key = ?",
                (fresh_db.BOOTSTRAP_MARKER_KEY,),
            ).fetchone()
        assert row is not None

    def test_marker_not_written_when_a_repo_fails(self, fresh_db):
        def fake_sync(repo, db_module=None):
            return {"status": 0, "synced": 0, "error": "boom"}

        with patch.object(github_events_sync, "sync_issues_mirror", fake_sync):
            result = github_events_sync.bootstrap_full_sync(["o/r"], db_module=fresh_db)
        assert result["errors"] == 1
        assert result["bootstrapped"] is False
        assert fresh_db.is_bootstrap_complete() is False, \
            "marker must not be written when a repo sync failed"


# ── AC-6: second start detects marker and skips full sync ─────────────────────

class TestSecondStartSkips:
    def test_second_run_skips_sync(self, fresh_db):
        fresh_db.mark_bootstrap_complete()
        calls = []

        def fake_sync(repo, db_module=None):
            calls.append(repo)
            return {"status": 200, "synced": 0}

        with patch.object(github_events_sync, "sync_issues_mirror", fake_sync):
            result = github_events_sync.bootstrap_full_sync(["o/r"], db_module=fresh_db)
        assert calls == [], "no full sync should run when the marker is present"
        assert result["skipped"] is True
        assert result["bootstrapped"] is False

    def test_second_run_emits_no_bootstrap_progress(self, fresh_db, capsys):
        fresh_db.mark_bootstrap_complete()
        with patch.object(github_events_sync, "sync_issues_mirror",
                          lambda *a, **k: {"status": 200, "synced": 0}):
            github_events_sync.bootstrap_full_sync(["o/r"], db_module=fresh_db)
        out = capsys.readouterr().out
        assert "syncing o/r" not in out
        assert NOTICE not in out


# ── AC-7: no crash on empty or absent DB at startup ───────────────────────────

class TestNoCrash:
    def test_absent_db_file_does_not_crash(self, absent_db):
        db_module, db_file = absent_db
        assert not db_file.exists()
        token_p, get_p = _patch_200([_rest_issue(1)])
        with token_p, get_p:
            # must not raise even though the DB file does not exist yet
            result = github_events_sync.bootstrap_full_sync(["o/r"], db_module=db_module)
        assert result["skipped"] is False

    def test_sync_exception_does_not_propagate(self, fresh_db):
        def boom(repo, db_module=None):
            raise RuntimeError("network down")

        with patch.object(github_events_sync, "sync_issues_mirror", boom):
            # one repo raising must be caught — no exception escapes
            result = github_events_sync.bootstrap_full_sync(["o/r"], db_module=fresh_db)
        assert result["errors"] == 1
        assert fresh_db.is_bootstrap_complete() is False

    def test_empty_repo_list_does_not_crash(self, fresh_db):
        result = github_events_sync.bootstrap_full_sync([], db_module=fresh_db)
        # no repos -> zero errors -> marker written, no crash
        assert result["errors"] == 0
        assert result["bootstrapped"] is True
