"""Tests for issue #815: issues-mirror sync follows Link pagination (removes 100-issue cap).

AC-1: _fetch_issues_conditional follows Link: rel="next" pagination, fetching all pages
      until no next link is present.
AC-2: ETag/If-None-Match conditional request only on first page; subsequent pages
      fetched unconditionally.
AC-3: After full sync, mirror contains all issues (>100), not just most-recently-updated.
AC-4: gh fallback not reached on subsequent syncs when mirror is populated with >100 issues.
AC-5: 304 on first page → early exit, no additional pages fetched.
AC-6: per_page=100 retained on every page request.
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


@pytest.fixture
def fresh_db(tmp_path):
    """Isolated SQLite DB; yields the patched db module."""
    db_file = tmp_path / "test_815.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


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


def _fake_resp(status_code, *, etag='"abc"', remaining=4999, body=None, link=None):
    resp = MagicMock()
    resp.status_code = status_code
    headers: dict = {
        "ETag": etag,
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Reset": "0",
    }
    if link is not None:
        headers["Link"] = link
    resp.headers = headers
    resp.json.return_value = body if body is not None else []
    resp.raise_for_status.return_value = None
    return resp


PAGE2_LINK = '<https://api.github.com/repos/o/r/issues?page=2&per_page=100>; rel="next"'
PAGE3_LINK = '<https://api.github.com/repos/o/r/issues?page=3&per_page=100>; rel="next"'


# ── AC-1: follows Link: rel="next" until no next link ────────────────────────

class TestAC1_PaginationFollowsNextLink:
    def test_two_pages_returns_combined_issues(self):
        """200 page 1 with next link → fetches page 2, returns 150 issues total."""
        page1 = [_rest_issue(i) for i in range(1, 101)]
        page2 = [_rest_issue(i) for i in range(101, 151)]
        responses = iter([
            _fake_resp(200, body=page1, link=PAGE2_LINK),
            _fake_resp(200, body=page2),
        ])
        with patch.object(github_events_sync, "_get_gh_token", return_value="tok"), \
             patch.object(github_events_sync.httpx, "get",
                          side_effect=lambda *a, **k: next(responses)):
            status, items, _, _ = github_events_sync._fetch_issues_conditional("o", "r")

        assert status == 200
        assert items is not None
        assert len(items) == 150

    def test_single_page_no_link_fetches_once(self):
        """Single page with no Link header: exactly one HTTP call."""
        with patch.object(github_events_sync, "_get_gh_token", return_value="tok"), \
             patch.object(github_events_sync.httpx, "get",
                          return_value=_fake_resp(200, body=[_rest_issue(1)])) as mock_get:
            _, items, _, _ = github_events_sync._fetch_issues_conditional("o", "r")

        assert mock_get.call_count == 1
        assert len(items) == 1

    def test_three_pages_all_fetched(self):
        """Follows next links across 3 pages, returning all 250 items."""
        p1 = [_rest_issue(i) for i in range(1, 101)]
        p2 = [_rest_issue(i) for i in range(101, 201)]
        p3 = [_rest_issue(i) for i in range(201, 251)]
        responses = iter([
            _fake_resp(200, body=p1, link=PAGE2_LINK),
            _fake_resp(200, body=p2, link=PAGE3_LINK),
            _fake_resp(200, body=p3),
        ])
        with patch.object(github_events_sync, "_get_gh_token", return_value="tok"), \
             patch.object(github_events_sync.httpx, "get",
                          side_effect=lambda *a, **k: next(responses)):
            _, items, _, _ = github_events_sync._fetch_issues_conditional("o", "r")

        assert len(items) == 250

    def test_prs_excluded_across_all_pages(self):
        """PR items (has 'pull_request' key) are filtered from all pages."""
        pr = {**_rest_issue(2), "pull_request": {"url": "..."}}
        responses = iter([
            _fake_resp(200, body=[_rest_issue(1), pr], link=PAGE2_LINK),
            _fake_resp(200, body=[_rest_issue(3)]),
        ])
        with patch.object(github_events_sync, "_get_gh_token", return_value="tok"), \
             patch.object(github_events_sync.httpx, "get",
                          side_effect=lambda *a, **k: next(responses)):
            _, items, _, _ = github_events_sync._fetch_issues_conditional("o", "r")

        nums = {it["number"] for it in items}
        assert 2 not in nums, "PR must be excluded across all pages"
        assert nums == {1, 3}


# ── AC-2: ETag/If-None-Match only on first page ───────────────────────────────

class TestAC2_ETagOnlyOnFirstPage:
    def test_if_none_match_sent_on_first_page(self):
        """Stored ETag is sent as If-None-Match on the first request only."""
        call_headers: list[dict] = []

        def mock_get(url, headers, **kwargs):
            call_headers.append(dict(headers))
            if len(call_headers) == 1:
                return _fake_resp(200, body=[_rest_issue(i) for i in range(1, 101)],
                                  link=PAGE2_LINK)
            return _fake_resp(200, body=[_rest_issue(101)])

        with patch.object(github_events_sync, "_get_gh_token", return_value="tok"), \
             patch.object(github_events_sync.httpx, "get", side_effect=mock_get):
            github_events_sync._fetch_issues_conditional("o", "r", etag='"stored"')

        assert len(call_headers) == 2
        assert call_headers[0].get("If-None-Match") == '"stored"', \
            "first page must send If-None-Match with stored ETag"
        assert "If-None-Match" not in call_headers[1], \
            "subsequent pages must NOT send If-None-Match"

    def test_no_etag_stored_no_if_none_match(self):
        """When no ETag is stored, the first request has no If-None-Match header."""
        with patch.object(github_events_sync, "_get_gh_token", return_value="tok"), \
             patch.object(github_events_sync.httpx, "get",
                          return_value=_fake_resp(200, body=[])) as mock_get:
            github_events_sync._fetch_issues_conditional("o", "r", etag=None)

        headers = mock_get.call_args[1].get("headers", {})
        assert "If-None-Match" not in headers


# ── AC-3: mirror contains all issues after paginated sync ─────────────────────

class TestAC3_MirrorContainsAllIssues:
    def test_sync_upserts_all_paginated_issues(self, fresh_db):
        """After a 2-page sync, mirror holds all 150 issues, not just 100."""
        page1 = [_rest_issue(i) for i in range(1, 101)]
        page2 = [_rest_issue(i) for i in range(101, 151)]
        responses = iter([
            _fake_resp(200, body=page1, link=PAGE2_LINK),
            _fake_resp(200, body=page2),
        ])
        with patch.object(github_events_sync, "_get_gh_token", return_value="tok"), \
             patch.object(github_events_sync.httpx, "get",
                          side_effect=lambda *a, **k: next(responses)):
            result = github_events_sync.sync_issues_mirror("o/r", db_module=fresh_db)

        assert result["status"] == 200
        assert result["synced"] == 150
        rows = fresh_db.get_mirrored_issues("o/r")
        assert len(rows) == 150
        assert {r["number"] for r in rows} == set(range(1, 151))


# ── AC-4: gh fallback not reached when mirror is populated ────────────────────

class TestAC4_GhFallbackNotReachedAfterFullSync:
    def test_mirror_populated_above_100_after_paginated_sync(self, fresh_db):
        """After a paginated sync with 150 issues, mirror has >100 rows
        (so _mirror_issues returns them and the gh fallback is not triggered)."""
        page1 = [_rest_issue(i) for i in range(1, 101)]
        page2 = [_rest_issue(i) for i in range(101, 151)]
        responses = iter([
            _fake_resp(200, body=page1, link=PAGE2_LINK),
            _fake_resp(200, body=page2),
        ])
        with patch.object(github_events_sync, "_get_gh_token", return_value="tok"), \
             patch.object(github_events_sync.httpx, "get",
                          side_effect=lambda *a, **k: next(responses)):
            github_events_sync.sync_issues_mirror("o/r", db_module=fresh_db)

        rows = fresh_db.get_mirrored_issues("o/r")
        assert rows is not None and len(rows) > 100, \
            "mirror must hold >100 rows so _mirror_issues returns non-None, preventing gh fallback"


# ── AC-5: 304 on first page → early exit, no additional pages ────────────────

class TestAC5_304FastPathNoAdditionalPages:
    def test_304_returns_immediately_no_extra_pages(self):
        """304 on the first page returns immediately with exactly one HTTP call."""
        with patch.object(github_events_sync, "_get_gh_token", return_value="tok"), \
             patch.object(github_events_sync.httpx, "get",
                          return_value=_fake_resp(304)) as mock_get:
            status, items, _, _ = github_events_sync._fetch_issues_conditional(
                "o", "r", etag='"e1"'
            )

        assert status == 304
        assert items is None
        assert mock_get.call_count == 1, "304 must not trigger any additional page fetches"

    def test_sync_result_is_304_when_first_page_304(self, fresh_db):
        """sync_issues_mirror propagates 304 and makes exactly one HTTP call."""
        fresh_db.set_sync_etag("issues:o/r", '"e1"')
        with patch.object(github_events_sync, "_get_gh_token", return_value="tok"), \
             patch.object(github_events_sync.httpx, "get",
                          return_value=_fake_resp(304)) as mock_get:
            result = github_events_sync.sync_issues_mirror("o/r", db_module=fresh_db)

        assert result["status"] == 304
        assert result["synced"] == 0
        assert mock_get.call_count == 1


# ── AC-6: per_page=100 retained on every request ─────────────────────────────

class TestAC6_PerPageRetained:
    def test_first_page_params_include_per_page_100(self):
        """First-page request params dict has per_page=100."""
        with patch.object(github_events_sync, "_get_gh_token", return_value="tok"), \
             patch.object(github_events_sync.httpx, "get",
                          return_value=_fake_resp(200, body=[])) as mock_get:
            github_events_sync._fetch_issues_conditional("o", "r")

        _, kwargs = mock_get.call_args
        assert kwargs.get("params", {}).get("per_page") == 100

    def test_next_link_url_includes_per_page_100(self):
        """The Link header next URL passed to subsequent requests includes per_page=100."""
        page1 = [_rest_issue(i) for i in range(1, 101)]
        call_urls: list[str] = []

        def mock_get(url, **kwargs):
            call_urls.append(url)
            if len(call_urls) == 1:
                return _fake_resp(200, body=page1, link=PAGE2_LINK)
            return _fake_resp(200, body=[])

        with patch.object(github_events_sync, "_get_gh_token", return_value="tok"), \
             patch.object(github_events_sync.httpx, "get", side_effect=mock_get):
            github_events_sync._fetch_issues_conditional("o", "r")

        assert len(call_urls) == 2
        assert "per_page=100" in call_urls[1], \
            "second-page URL (from Link header) must include per_page=100"
