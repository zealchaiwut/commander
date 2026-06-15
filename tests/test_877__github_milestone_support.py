"""Tests for issue #877: Add GitHub milestone support to backend and issues mirror.

Each test is anchored to a specific acceptance criterion.

AC-1: GET /projects/:repo/milestones returns all milestones from the mirror,
      falling back to the GitHub API before the first sync.
AC-2: POST /projects/:repo/milestones creates a milestone on GitHub (title
      required; description and due_on optional) and returns the created object.
AC-3: PATCH /projects/:repo/milestones/:number edits title, description, due
      date, or state of an existing milestone.
AC-4: DELETE /projects/:repo/milestones/:number (or PATCH state=closed) closes
      a milestone on GitHub.
AC-5: The issues sync job captures milestone.number and milestone.title from the
      REST issues payload and stores them in the mirror.
AC-6: GET /projects/:repo/issues (mirror) returns a milestone field on each
      issue without issuing GitHub API calls post-sync.
AC-7: GET /projects/:repo/milestones reads from the mirror after sync completes,
      using zero GitHub API quota.
AC-8: All write operations (create, edit, close) reflect on GitHub within the
      same request cycle (the GitHub REST endpoint is hit with the right verb).
AC-9: A milestone created via the API endpoint targets the correct repository
      on GitHub (POST URL is /repos/<owner>/<repo>/milestones).
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

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))

import db as _db_module  # noqa: E402
import github_events_sync  # noqa: E402
import github_milestones  # noqa: E402
from routers import milestones_service  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path):
    """Isolated SQLite DB; yields the patched db module."""
    db_file = tmp_path / "test_877.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


# ── REST-shaped payloads (GitHub /repos/{o}/{r}/milestones) ───────────────────

def _rest_milestone(number, title="v1.0", state="open", description="desc",
                    due_on="2026-07-31T00:00:00Z"):
    return {
        "number": number,
        "title": title,
        "state": state,
        "description": description,
        "due_on": due_on,
        "html_url": f"https://github.com/o/r/milestone/{number}",
        "open_issues": 0,
        "closed_issues": 0,
    }


def _rest_issue(number, title="t", state="open", milestone=None,
                updated_at="2026-06-10T00:00:00Z"):
    return {
        "number": number,
        "title": title,
        "state": state,
        "labels": [],
        "assignees": [],
        "html_url": f"https://github.com/o/r/issues/{number}",
        "created_at": "2026-06-01T00:00:00Z",
        "updated_at": updated_at,
        "body": "body",
        "milestone": milestone,
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


# ── AC-2: milestones mirror table schema ──────────────────────────────────────

class TestMirrorTableSchema:
    def test_milestones_table_exists(self, fresh_db):
        with fresh_db.get_conn() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='milestones'"
            ).fetchone()
        assert row is not None, "milestones mirror table not created by init_db()"

    def test_milestones_table_has_required_columns(self, fresh_db):
        with fresh_db.get_conn() as conn:
            cols = {r["name"]
                    for r in conn.execute("PRAGMA table_info(milestones)").fetchall()}
        for required in ("repo", "number", "title", "description", "state", "due_on"):
            assert required in cols, f"milestones table missing column {required!r}"

    def test_upsert_and_get_round_trip(self, fresh_db):
        fresh_db.upsert_milestones("o/r", [
            github_milestones._normalise_milestone(_rest_milestone(1, title="A")),
            github_milestones._normalise_milestone(_rest_milestone(2, title="B")),
        ])
        rows = fresh_db.get_mirrored_milestones("o/r")
        assert {m["number"] for m in rows} == {1, 2}
        assert {m["title"] for m in rows} == {"A", "B"}

    def test_get_single_milestone(self, fresh_db):
        fresh_db.upsert_milestones("o/r", [
            github_milestones._normalise_milestone(_rest_milestone(7, title="seven")),
        ])
        m = fresh_db.get_mirrored_milestone("o/r", 7)
        assert m is not None and m["number"] == 7 and m["title"] == "seven"


# ── AC-7 / AC-3 (sync): ETag conditional milestones sync ──────────────────────

class TestConditionalSync:
    def test_200_upserts_mirror(self, fresh_db):
        body = [_rest_milestone(1), _rest_milestone(2)]
        with patch.object(github_milestones, "_get_gh_token", return_value="tok"), \
             patch.object(github_milestones.httpx, "get",
                          return_value=_fake_resp(200, etag='"v1"', body=body)):
            result = github_milestones.sync_milestones_mirror("o/r", db_module=fresh_db)
        assert result["status"] == 200
        assert result["synced"] == 2
        rows = fresh_db.get_mirrored_milestones("o/r")
        assert {m["number"] for m in rows} == {1, 2}

    def test_200_stores_etag(self, fresh_db):
        body = [_rest_milestone(1)]
        with patch.object(github_milestones, "_get_gh_token", return_value="tok"), \
             patch.object(github_milestones.httpx, "get",
                          return_value=_fake_resp(200, etag='"v9"', body=body)):
            github_milestones.sync_milestones_mirror("o/r", db_module=fresh_db)
        assert fresh_db.get_sync_etag("milestones:o/r") == '"v9"'

    def test_304_is_noop(self, fresh_db):
        body = [_rest_milestone(1, title="orig")]
        with patch.object(github_milestones, "_get_gh_token", return_value="tok"), \
             patch.object(github_milestones.httpx, "get",
                          return_value=_fake_resp(200, etag='"v1"', body=body)):
            github_milestones.sync_milestones_mirror("o/r", db_module=fresh_db)
        with patch.object(github_milestones, "_get_gh_token", return_value="tok"), \
             patch.object(github_milestones.httpx, "get",
                          return_value=_fake_resp(304, etag='"v1"')):
            result = github_milestones.sync_milestones_mirror("o/r", db_module=fresh_db)
        assert result["status"] == 304
        rows = fresh_db.get_mirrored_milestones("o/r")
        assert len(rows) == 1 and rows[0]["title"] == "orig"

    def test_304_sends_if_none_match(self, fresh_db):
        fresh_db.set_sync_etag("milestones:o/r", '"stored"')
        with patch.object(github_milestones, "_get_gh_token", return_value="tok"), \
             patch.object(github_milestones.httpx, "get",
                          return_value=_fake_resp(304, etag='"stored"')) as mock_get:
            github_milestones.sync_milestones_mirror("o/r", db_module=fresh_db)
        _, kwargs = mock_get.call_args
        assert kwargs.get("headers", {}).get("If-None-Match") == '"stored"'


# ── AC-7: list reads from mirror after sync, zero GitHub quota ────────────────

class TestListFromMirror:
    def _no_http(self):
        def _boom(*a, **k):
            raise AssertionError("list made a live GitHub call: %r" % (a,))
        return patch.object(github_milestones.httpx, "get", _boom)

    def test_list_served_from_mirror_no_http(self, fresh_db, monkeypatch):
        monkeypatch.setattr(milestones_service, "_resolve_repo", lambda slug: "o/r")
        fresh_db.upsert_milestones("o/r", [
            github_milestones._normalise_milestone(_rest_milestone(1, title="A")),
        ])
        with self._no_http():
            # Pass state param to get a list (not a selector dict)
            ms = milestones_service.list_milestones("r", state="open")
        assert {m["number"] for m in ms} == {1}

    # ── AC-1: fallback to live GitHub API before first sync ───────────────────
    def test_empty_mirror_falls_back_to_live(self, fresh_db, monkeypatch):
        monkeypatch.setattr(milestones_service, "_resolve_repo", lambda slug: "o/r")
        body = [_rest_milestone(99, title="live")]
        with patch.object(github_milestones, "_get_gh_token", return_value="tok"), \
             patch.object(github_milestones.httpx, "get",
                          return_value=_fake_resp(200, body=body)):
            # Pass state param to get a list (not a selector dict)
            ms = milestones_service.list_milestones("r", state="open")
        assert {m["number"] for m in ms} == {99}


# ── AC-2 / AC-8 / AC-9: create milestone on GitHub ────────────────────────────

class TestCreateMilestone:
    def test_create_posts_to_github_and_returns_object(self, fresh_db, monkeypatch):
        created = _rest_milestone(5, title="v1.0 Launch", due_on="2026-07-31T00:00:00Z")
        resp = _fake_resp(201, body=created)
        with patch.object(github_milestones, "_get_gh_token", return_value="tok"), \
             patch.object(github_milestones.httpx, "post", return_value=resp) as mock_post:
            out = github_milestones.create_milestone(
                "o/r", title="v1.0 Launch", due_on="2026-07-31T00:00:00Z",
                db_module=fresh_db)
        # AC-9: correct repo URL
        url = mock_post.call_args[0][0]
        assert url == "https://api.github.com/repos/o/r/milestones"
        # AC-2: title sent, returned object carries number/title/due_on
        sent = mock_post.call_args.kwargs["json"]
        assert sent["title"] == "v1.0 Launch"
        assert out["number"] == 5 and out["title"] == "v1.0 Launch"
        assert out["due_on"] == "2026-07-31T00:00:00Z"

    def test_create_title_required(self, fresh_db):
        with pytest.raises(ValueError):
            github_milestones.create_milestone("o/r", title="", db_module=fresh_db)

    def test_create_optional_fields_omitted(self, fresh_db):
        resp = _fake_resp(201, body=_rest_milestone(6, title="bare"))
        with patch.object(github_milestones, "_get_gh_token", return_value="tok"), \
             patch.object(github_milestones.httpx, "post", return_value=resp) as mock_post:
            github_milestones.create_milestone("o/r", title="bare", db_module=fresh_db)
        sent = mock_post.call_args.kwargs["json"]
        assert "due_on" not in sent and "description" not in sent

    def test_create_upserts_mirror(self, fresh_db):
        resp = _fake_resp(201, body=_rest_milestone(8, title="mirrored"))
        with patch.object(github_milestones, "_get_gh_token", return_value="tok"), \
             patch.object(github_milestones.httpx, "post", return_value=resp):
            github_milestones.create_milestone("o/r", title="mirrored", db_module=fresh_db)
        assert fresh_db.get_mirrored_milestone("o/r", 8) is not None


# ── AC-3 / AC-8: edit milestone ───────────────────────────────────────────────

class TestUpdateMilestone:
    def test_patch_edits_fields(self, fresh_db):
        updated = _rest_milestone(5, title="v1.0 GA", due_on="2026-08-15T00:00:00Z")
        resp = _fake_resp(200, body=updated)
        with patch.object(github_milestones, "_get_gh_token", return_value="tok"), \
             patch.object(github_milestones.httpx, "patch", return_value=resp) as mock_patch:
            out = github_milestones.update_milestone(
                "o/r", 5, title="v1.0 GA", due_on="2026-08-15T00:00:00Z",
                db_module=fresh_db)
        url = mock_patch.call_args[0][0]
        assert url == "https://api.github.com/repos/o/r/milestones/5"
        sent = mock_patch.call_args.kwargs["json"]
        assert sent["title"] == "v1.0 GA" and sent["due_on"] == "2026-08-15T00:00:00Z"
        assert out["title"] == "v1.0 GA"

    def test_patch_upserts_mirror(self, fresh_db):
        resp = _fake_resp(200, body=_rest_milestone(5, title="new"))
        with patch.object(github_milestones, "_get_gh_token", return_value="tok"), \
             patch.object(github_milestones.httpx, "patch", return_value=resp):
            github_milestones.update_milestone("o/r", 5, title="new", db_module=fresh_db)
        assert fresh_db.get_mirrored_milestone("o/r", 5)["title"] == "new"


# ── AC-4: close milestone ─────────────────────────────────────────────────────

class TestCloseMilestone:
    def test_close_sets_state_closed(self, fresh_db):
        resp = _fake_resp(200, body=_rest_milestone(5, state="closed"))
        with patch.object(github_milestones, "_get_gh_token", return_value="tok"), \
             patch.object(github_milestones.httpx, "patch", return_value=resp) as mock_patch:
            out = github_milestones.close_milestone("o/r", 5, db_module=fresh_db)
        sent = mock_patch.call_args.kwargs["json"]
        assert sent["state"] == "closed"
        assert out["state"] == "closed"

    def test_close_via_patch_state(self, fresh_db):
        resp = _fake_resp(200, body=_rest_milestone(5, state="closed"))
        with patch.object(github_milestones, "_get_gh_token", return_value="tok"), \
             patch.object(github_milestones.httpx, "patch", return_value=resp):
            out = github_milestones.update_milestone(
                "o/r", 5, state="closed", db_module=fresh_db)
        assert out["state"] == "closed"


# ── AC-5: issues sync captures milestone number + title ───────────────────────

class TestIssueMilestoneCapture:
    def test_normalise_issue_captures_milestone(self):
        item = _rest_issue(1, milestone={"number": 3, "title": "v1.0 GA"})
        norm = github_events_sync._normalise_issue(item)
        assert norm["milestone"] == {"number": 3, "title": "v1.0 GA"}

    def test_normalise_issue_milestone_none(self):
        norm = github_events_sync._normalise_issue(_rest_issue(1, milestone=None))
        assert norm["milestone"] is None

    def test_sync_stores_milestone_in_mirror(self, fresh_db):
        body = [_rest_issue(1, milestone={"number": 3, "title": "v1.0 GA"})]
        with patch.object(github_events_sync, "_get_gh_token", return_value="tok"), \
             patch.object(github_events_sync.httpx, "get",
                          return_value=_fake_resp(200, etag='"v1"', body=body)):
            github_events_sync.sync_issues_mirror("o/r", db_module=fresh_db)
        rows = fresh_db.get_mirrored_issues("o/r")
        assert rows[0]["milestone"] == {"number": 3, "title": "v1.0 GA"}


# ── AC-6: mirror issues endpoint returns milestone, zero GitHub quota ─────────

class TestIssuesMirrorEndpoint:
    def test_list_issues_returns_milestone_no_http(self, fresh_db, monkeypatch):
        monkeypatch.setattr(milestones_service, "_resolve_repo", lambda slug: "o/r")
        github_events_sync_norm = github_events_sync._normalise_issue(
            _rest_issue(1, milestone={"number": 3, "title": "v1.0 GA"}))
        fresh_db.upsert_issues("o/r", [github_events_sync_norm])

        def _boom(*a, **k):
            raise AssertionError("issues read made a live GitHub call")

        with patch.object(github_milestones.httpx, "get", _boom):
            issues = milestones_service.list_issues("r")
        assert issues[0]["milestone"] == {"number": 3, "title": "v1.0 GA"}
