"""Tests for the issue-mirror open-set reconcile (#756 follow-up).

The incremental ETag poll can miss a closure (sync down, rate-limited, or the
issue paged off the recently-updated window), leaving a permanently-stale `open`
mirror row that keeps a finished sprint on the board (sprints 66/67/68.1 on PRD).
reconcile_closed_issues diffs the mirror's open set against GitHub's live open
set and closes the difference. The fix must patch the `raw` JSON too, since
mirror reads reconstruct issue state from raw, not the state column.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")

import db as _db_module  # noqa: E402
import github_events_sync  # noqa: E402

REPO = "owner/repo"


@pytest.fixture
def fresh_db(tmp_path):
    db_file = tmp_path / "test_reconcile.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


def _issue(number, state="open", labels=None):
    return {
        "number": number,
        "title": f"issue {number}",
        "state": state,
        "labels": [{"name": n, "color": "ededed"} for n in (labels or [])],
        "updatedAt": "2026-06-11T00:00:00Z",
        "body": "b",
    }


def test_mark_issues_closed_patches_raw_state(fresh_db):
    """db.mark_issues_closed flips both the state column and the raw JSON."""
    fresh_db.upsert_issues(REPO, [_issue(880, "open"), _issue(862, "open")])

    n = fresh_db.mark_issues_closed(REPO, [880])
    assert n == 1

    # Read path reconstructs from raw — must report closed.
    open_issues = fresh_db.get_mirrored_issues(REPO, state="open")
    open_nums = {i["number"] for i in open_issues}
    assert 880 not in open_nums, "reconciled issue must drop out of the open set"
    assert 862 in open_nums, "untouched issue stays open"

    row880 = fresh_db.get_mirrored_issue(REPO, 880)
    assert row880["state"] == "closed", "raw JSON state must be patched to closed"


def test_mark_issues_closed_ignores_already_closed(fresh_db):
    """Only state='open' rows are flipped; closed rows are untouched no-ops."""
    fresh_db.upsert_issues(REPO, [_issue(800, "closed")])
    assert fresh_db.mark_issues_closed(REPO, [800]) == 0


def test_reconcile_closes_stale_open_rows(fresh_db):
    """Mirror-open issues absent from GitHub's open set are closed."""
    # Mirror thinks 880, 881, 862 are open (frozen snapshot).
    fresh_db.upsert_issues(
        REPO, [_issue(880, "open"), _issue(881, "open"), _issue(862, "open")]
    )
    # GitHub now reports only 862 open (880, 881 were closed since the snapshot).
    with patch.object(
        github_events_sync, "_fetch_open_issue_numbers", return_value={862}
    ):
        result = github_events_sync.reconcile_closed_issues(REPO, db_module=fresh_db)

    assert result["reconciled"] == 2
    assert set(result["stale"]) == {880, 881}
    open_nums = {i["number"] for i in fresh_db.get_mirrored_issues(REPO, state="open")}
    assert open_nums == {862}


def test_reconcile_noop_when_mirror_matches_github(fresh_db):
    """No stale rows -> nothing closed."""
    fresh_db.upsert_issues(REPO, [_issue(862, "open")])
    with patch.object(
        github_events_sync, "_fetch_open_issue_numbers", return_value={862}
    ):
        result = github_events_sync.reconcile_closed_issues(REPO, db_module=fresh_db)
    assert result["reconciled"] == 0


def test_reconcile_leaves_mirror_untouched_on_fetch_error(fresh_db):
    """A transient GitHub failure must NOT mass-close the mirror."""
    fresh_db.upsert_issues(REPO, [_issue(880, "open"), _issue(862, "open")])
    with patch.object(
        github_events_sync, "_fetch_open_issue_numbers",
        side_effect=RuntimeError("boom"),
    ):
        result = github_events_sync.reconcile_closed_issues(REPO, db_module=fresh_db)

    assert result["reconciled"] == 0
    assert "error" in result
    open_nums = {i["number"] for i in fresh_db.get_mirrored_issues(REPO, state="open")}
    assert open_nums == {880, 862}, "mirror must be untouched on fetch error"


def test_reconcile_empty_mirror_skips_fetch(fresh_db):
    """No mirrored open rows -> no GitHub call at all."""
    with patch.object(github_events_sync, "_fetch_open_issue_numbers") as m:
        result = github_events_sync.reconcile_closed_issues(REPO, db_module=fresh_db)
    m.assert_not_called()
    assert result["reconciled"] == 0
