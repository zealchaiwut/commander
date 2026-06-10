"""Tester AC-verification for issue #760: Bootstrap full sync on first run from empty DB.

This feature is server-startup behaviour (empty-DB detection, one-time full sync,
schema-marker write, skip-on-second-start). It is not reachable through the live
HTTP API of an already-bootstrapped UAT server, so each acceptance criterion is
verified in-process against the implementation functions using an isolated
temp SQLite DB. No database is mutated outside the temp path; no UAT state is
touched.

One test function per acceptance criterion (AC-1 .. AC-7).
"""
from __future__ import annotations

import logging
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

NOTICE = (
    "history/order/settings start empty — restore from backup if migrating "
    "(backup restore / restore-db)"
)


@pytest.fixture
def fresh_db(tmp_path):
    """Isolated SQLite DB with schema created but no bootstrap marker."""
    db_file = tmp_path / "tester_760.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    _db_module.init_db()
    yield _db_module
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


def _fake_resp(status_code, *, etag='"v1"', remaining=4999, body=None):
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
    return (
        patch.object(github_events_sync, "_get_gh_token", return_value="tok"),
        patch.object(github_events_sync.httpx, "get",
                     return_value=_fake_resp(200, body=body)),
    )


# AC-1: empty DB / missing marker triggers a full GitHub sync automatically.
def test_bootstrap_full_sync__detects_empty_db_and_triggers_full_sync(fresh_db):
    assert fresh_db.is_bootstrap_complete() is False
    synced_repos = []

    def fake_sync(repo, db_module=None):
        synced_repos.append(repo)
        return {"status": 200, "synced": 0}

    with patch.object(github_events_sync, "sync_issues_mirror", fake_sync):
        result = github_events_sync.bootstrap_full_sync(
            ["o/r1", "o/r2"], db_module=fresh_db
        )
    assert synced_repos == ["o/r1", "o/r2"]
    assert result["skipped"] is False and result["bootstrapped"] is True
    # wiring guard: lifespan runs bootstrap before handing off to the ETag loop
    server_src = (DASHBOARD_DIR / "server.py").read_text()
    assert "bootstrap_full_sync" in server_src
    assert (server_src.index("bootstrap_full_sync")
            < server_src.index("run_issues_sync_loop"))


# AC-2: issues, labels, statuses, and sprint membership populate without manual steps.
def test_bootstrap_full_sync__populates_issues_labels_statuses(fresh_db):
    body = [
        _rest_issue(1, labels=["sprint-57", "SIT"]),
        _rest_issue(2, labels=["sprint-57", "in-progress"], state="open"),
    ]
    token_p, get_p = _patch_200(body)
    with token_p, get_p:
        github_events_sync.bootstrap_full_sync(["o/r"], db_module=fresh_db)
    rows = fresh_db.get_mirrored_issues("o/r")
    assert {r["number"] for r in rows} == {1, 2}
    labels_by_num = {r["number"]: {l["name"] for l in r["labels"]} for r in rows}
    assert {"sprint-57", "SIT"} <= labels_by_num[1]      # labels + sprint membership
    assert all(r["state"] == "open" for r in rows)        # statuses preserved


# AC-3: sync progress is logged to stdout/server log during bootstrap.
def test_bootstrap_full_sync__logs_progress(fresh_db, capsys, caplog):
    token_p, get_p = _patch_200([_rest_issue(1)])
    with caplog.at_level(logging.INFO, logger=github_events_sync.logger.name):
        with token_p, get_p:
            github_events_sync.bootstrap_full_sync(["o/r"], db_module=fresh_db)
    out = capsys.readouterr().out
    assert "[bootstrap]" in out and "syncing o/r" in out      # stdout
    assert any("[bootstrap]" in r.message for r in caplog.records)  # logger


# AC-4: after bootstrap completes, server logs the exact restore notice.
def test_bootstrap_full_sync__logs_restore_notice(fresh_db, capsys):
    token_p, get_p = _patch_200([_rest_issue(1)])
    with token_p, get_p:
        github_events_sync.bootstrap_full_sync(["o/r"], db_module=fresh_db)
    assert NOTICE in capsys.readouterr().out
    assert github_events_sync.BOOTSTRAP_RESTORE_NOTICE == NOTICE


# AC-5: a schema-marker row is written on successful completion.
def test_bootstrap_full_sync__writes_schema_marker_on_success(fresh_db):
    token_p, get_p = _patch_200([_rest_issue(1)])
    with token_p, get_p:
        github_events_sync.bootstrap_full_sync(["o/r"], db_module=fresh_db)
    assert fresh_db.is_bootstrap_complete() is True
    with fresh_db.get_conn() as conn:
        row = conn.execute(
            "SELECT key FROM sync_state WHERE key = ?",
            (fresh_db.BOOTSTRAP_MARKER_KEY,),
        ).fetchone()
    assert row is not None


# AC-5 edge: marker not written when a repo sync fails.
def test_bootstrap_full_sync__no_marker_when_repo_fails(fresh_db):
    def failing_sync(repo, db_module=None):
        return {"status": 0, "synced": 0, "error": "boom"}

    with patch.object(github_events_sync, "sync_issues_mirror", failing_sync):
        result = github_events_sync.bootstrap_full_sync(["o/r"], db_module=fresh_db)
    assert result["errors"] == 1 and result["bootstrapped"] is False
    assert fresh_db.is_bootstrap_complete() is False


# AC-6: second start detects the marker and skips the full sync.
def test_bootstrap_full_sync__second_start_skips_sync(fresh_db, capsys):
    fresh_db.mark_bootstrap_complete()
    calls = []

    def fake_sync(repo, db_module=None):
        calls.append(repo)
        return {"status": 200, "synced": 0}

    with patch.object(github_events_sync, "sync_issues_mirror", fake_sync):
        result = github_events_sync.bootstrap_full_sync(["o/r"], db_module=fresh_db)
    assert calls == []                       # no full sync ran
    assert result["skipped"] is True
    out = capsys.readouterr().out
    assert "syncing o/r" not in out and NOTICE not in out   # no bootstrap log lines


# AC-7: no crash or unhandled exception on empty or absent DB at startup.
def test_bootstrap_full_sync__never_raises_on_failure_or_empty(fresh_db, tmp_path):
    # (a) a repo sync raising must be swallowed
    def boom(repo, db_module=None):
        raise RuntimeError("network down")

    with patch.object(github_events_sync, "sync_issues_mirror", boom):
        result = github_events_sync.bootstrap_full_sync(["o/r"], db_module=fresh_db)
    assert result["errors"] == 1 and fresh_db.is_bootstrap_complete() is False

    # (b) absent DB file on disk must not crash
    db_file = tmp_path / "never_created.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    try:
        assert not db_file.exists()
        token_p, get_p = _patch_200([_rest_issue(1)])
        with token_p, get_p:
            res2 = github_events_sync.bootstrap_full_sync(["o/r"], db_module=_db_module)
        assert res2["skipped"] is False
    finally:
        _db_module.DB_PATH = original
