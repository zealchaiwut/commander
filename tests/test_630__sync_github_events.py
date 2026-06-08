"""Tests for issue #630: Sync GitHub Events into Events Table on Logs Open.

AC-1: Opening the Logs screen triggers a poll of the GitHub Events API
AC-2: Manual refresh re-polls without user needing to close/reopen the screen
AC-3: Supported event types captured and stored with source=github
  AC-3a: issue_opened  (IssuesEvent, action=opened)
  AC-3b: issue_closed  (IssuesEvent, action=closed)
  AC-3c: label_added   (IssuesEvent, action=labeled) — stores label name+color
  AC-3d: label_removed (IssuesEvent, action=unlabeled) — stores label name+color
  AC-3e: branch_created (CreateEvent, ref_type=branch)
  AC-3f: pr_opened     (PullRequestEvent, action=opened) — stores head+base ref
  AC-3g: pr_merged     (PullRequestEvent, action=closed + merged=true) — stores merged=true
AC-4: Dedup by GitHub event id — re-polling same window inserts no duplicate rows
AC-5: GitHub event stamped with existing action_id when Commander event matches
      same target within ±5 min
AC-6: actor field from actor.login; TODO comment for bot-identity in source
AC-7: Poll bounded — at most N=300 events, one page only
AC-8: Rate-limit headers checked; if remaining < threshold skip poll and log warning
AC-9: Unsupported event types ignored (not stored)
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))

import db as _db_module  # noqa: E402


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def fresh_db(tmp_path):
    """Isolated SQLite DB; yields patched db module."""
    db_file = tmp_path / "test_630.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


def _make_issues_event(gh_id: str, action: str, issue_num: int,
                       actor: str = "alice", label: dict | None = None) -> dict:
    payload: dict = {"action": action, "issue": {"number": issue_num}}
    if label:
        payload["label"] = label
    return {
        "id": gh_id,
        "type": "IssuesEvent",
        "actor": {"login": actor},
        "payload": payload,
        "created_at": "2026-06-08T10:00:00Z",
    }


def _make_create_event(gh_id: str, ref: str, actor: str = "alice") -> dict:
    return {
        "id": gh_id,
        "type": "CreateEvent",
        "actor": {"login": actor},
        "payload": {"ref_type": "branch", "ref": ref},
        "created_at": "2026-06-08T10:00:00Z",
    }


def _make_pr_event(gh_id: str, action: str, merged: bool = False,
                   head: str = "feature/x", base: str = "main",
                   pr_num: int = 1, actor: str = "alice") -> dict:
    return {
        "id": gh_id,
        "type": "PullRequestEvent",
        "actor": {"login": actor},
        "payload": {
            "action": action,
            "pull_request": {
                "number": pr_num,
                "merged": merged,
                "head": {"ref": head},
                "base": {"ref": base},
            },
        },
        "created_at": "2026-06-08T10:00:00Z",
    }


def _count_github_events(db: object) -> int:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as n FROM events WHERE source='github'"
        ).fetchone()
    return row["n"]


def _get_github_events(db: object) -> list[dict]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE source='github' ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]


# ── Import sync module ────────────────────────────────────────────────────────

import github_events_sync as sync_mod  # noqa: E402


# ── AC-3: Supported event types stored with source=github ────────────────────

class TestSupportedEventTypes:
    """AC-3: All seven supported event types stored correctly."""

    def _run_sync(self, events: list[dict], fresh_db) -> list[dict]:
        with patch.object(sync_mod, "_fetch_events", return_value=(events, {"remaining": 100})):
            sync_mod.sync_github_events("owner/repo", "owner/repo", db_module=fresh_db)
        return _get_github_events(fresh_db)

    def test_issue_opened(self, fresh_db):
        """AC-3a: IssuesEvent action=opened → type=issue_opened, source=github."""
        ev = _make_issues_event("gh-001", "opened", issue_num=42)
        rows = self._run_sync([ev], fresh_db)
        assert len(rows) == 1
        r = rows[0]
        assert r["source"] == "github"
        assert r["type"] == "issue_opened"
        assert r["target"] == "#42"
        assert r["actor"] == "alice"

    def test_issue_closed(self, fresh_db):
        """AC-3b: IssuesEvent action=closed → type=issue_closed."""
        ev = _make_issues_event("gh-002", "closed", issue_num=7)
        rows = self._run_sync([ev], fresh_db)
        assert rows[0]["type"] == "issue_closed"

    def test_label_added_stores_name_and_color(self, fresh_db):
        """AC-3c: IssuesEvent action=labeled → type=label_added with label name+color."""
        ev = _make_issues_event(
            "gh-003", "labeled", issue_num=5,
            label={"name": "in-progress", "color": "d93f0b"}
        )
        rows = self._run_sync([ev], fresh_db)
        assert rows[0]["type"] == "label_added"
        detail = json.loads(rows[0]["detail"])
        assert detail["label_name"] == "in-progress"
        assert detail["label_color"] == "d93f0b"

    def test_label_removed_stores_name_and_color(self, fresh_db):
        """AC-3d: IssuesEvent action=unlabeled → type=label_removed with label name+color."""
        ev = _make_issues_event(
            "gh-004", "unlabeled", issue_num=5,
            label={"name": "SIT", "color": "0075ca"}
        )
        rows = self._run_sync([ev], fresh_db)
        assert rows[0]["type"] == "label_removed"
        detail = json.loads(rows[0]["detail"])
        assert detail["label_name"] == "SIT"
        assert detail["label_color"] == "0075ca"

    def test_branch_created(self, fresh_db):
        """AC-3e: CreateEvent ref_type=branch → type=branch_created, target=ref name."""
        ev = _make_create_event("gh-005", "feature/my-feature")
        rows = self._run_sync([ev], fresh_db)
        assert rows[0]["type"] == "branch_created"
        assert rows[0]["target"] == "feature/my-feature"

    def test_pr_opened_stores_head_and_base(self, fresh_db):
        """AC-3f: PullRequestEvent action=opened → type=pr_opened with head+base."""
        ev = _make_pr_event("gh-006", "opened", head="feature/x", base="main", pr_num=99)
        rows = self._run_sync([ev], fresh_db)
        assert rows[0]["type"] == "pr_opened"
        detail = json.loads(rows[0]["detail"])
        assert detail["head"] == "feature/x"
        assert detail["base"] == "main"

    def test_pr_merged_stores_merged_true(self, fresh_db):
        """AC-3g: PullRequestEvent action=closed + merged=True → type=pr_merged, merged=true."""
        ev = _make_pr_event("gh-007", "closed", merged=True, head="feature/x", base="main", pr_num=99)
        rows = self._run_sync([ev], fresh_db)
        assert rows[0]["type"] == "pr_merged"
        detail = json.loads(rows[0]["detail"])
        assert detail.get("merged") is True

    def test_pr_closed_not_merged_ignored(self, fresh_db):
        """AC-9 edge: PullRequestEvent closed without merge is ignored."""
        ev = _make_pr_event("gh-008", "closed", merged=False)
        rows = self._run_sync([ev], fresh_db)
        assert len(rows) == 0


# ── AC-4: Deduplication ───────────────────────────────────────────────────────

class TestDeduplication:
    """AC-4: Re-polling same events does not insert duplicates."""

    def test_same_github_event_id_not_duplicated(self, fresh_db):
        ev = _make_issues_event("gh-dup-001", "opened", issue_num=10)
        with patch.object(sync_mod, "_fetch_events", return_value=([ev], {"remaining": 100})):
            sync_mod.sync_github_events("owner/repo", "owner/repo", db_module=fresh_db)
            sync_mod.sync_github_events("owner/repo", "owner/repo", db_module=fresh_db)

        assert _count_github_events(fresh_db) == 1

    def test_different_github_ids_both_stored(self, fresh_db):
        ev1 = _make_issues_event("gh-x1", "opened", issue_num=1)
        ev2 = _make_issues_event("gh-x2", "closed", issue_num=1)
        with patch.object(sync_mod, "_fetch_events", return_value=([ev1, ev2], {"remaining": 100})):
            sync_mod.sync_github_events("owner/repo", "owner/repo", db_module=fresh_db)

        assert _count_github_events(fresh_db) == 2

    def test_result_skipped_count_on_repoll(self, fresh_db):
        ev = _make_issues_event("gh-skip-001", "opened", issue_num=20)
        with patch.object(sync_mod, "_fetch_events", return_value=([ev], {"remaining": 100})):
            sync_mod.sync_github_events("owner/repo", "owner/repo", db_module=fresh_db)
            result = sync_mod.sync_github_events("owner/repo", "owner/repo", db_module=fresh_db)

        assert result["skipped"] >= 1


# ── AC-5: action_id matching ──────────────────────────────────────────────────

class TestActionIdMatching:
    """AC-5: GitHub event gets action_id from matching Commander event within ±5 min."""

    def test_github_event_adopts_commander_action_id(self, fresh_db):
        action_id = "test-action-uuid-1234"
        ts = "2026-06-08T10:00:00"
        with fresh_db.get_conn() as conn:
            conn.execute(
                """INSERT INTO events (project, timestamp, source, actor, type, target, action_id, detail)
                   VALUES (?, ?, 'dashboard', 'bot', 'label_added', '#42', ?, '{}')""",
                ("owner/repo", ts, action_id),
            )
            conn.commit()

        gh_ev = _make_issues_event("gh-act-001", "labeled", issue_num=42,
                                    label={"name": "SIT", "color": "0075ca"})
        gh_ev["created_at"] = "2026-06-08T10:02:00Z"  # 2 min later, within window

        with patch.object(sync_mod, "_fetch_events", return_value=([gh_ev], {"remaining": 100})):
            sync_mod.sync_github_events("owner/repo", "owner/repo", db_module=fresh_db)

        rows = _get_github_events(fresh_db)
        assert len(rows) == 1
        assert rows[0]["action_id"] == action_id

    def test_github_event_no_action_id_when_outside_window(self, fresh_db):
        action_id = "old-action-uuid"
        ts = "2026-06-08T09:00:00"
        with fresh_db.get_conn() as conn:
            conn.execute(
                """INSERT INTO events (project, timestamp, source, actor, type, target, action_id, detail)
                   VALUES (?, ?, 'dashboard', 'bot', 'label_added', '#42', ?, '{}')""",
                ("owner/repo", ts, action_id),
            )
            conn.commit()

        gh_ev = _make_issues_event("gh-act-002", "labeled", issue_num=42,
                                    label={"name": "SIT", "color": "0075ca"})
        gh_ev["created_at"] = "2026-06-08T10:30:00Z"  # 90 min later, outside window

        with patch.object(sync_mod, "_fetch_events", return_value=([gh_ev], {"remaining": 100})):
            sync_mod.sync_github_events("owner/repo", "owner/repo", db_module=fresh_db)

        rows = _get_github_events(fresh_db)
        assert rows[0]["action_id"] is None


# ── AC-6: Actor field and TODO comment ───────────────────────────────────────

class TestActorField:
    """AC-6: actor populated from actor.login; TODO comment for bot-identity."""

    def test_actor_populated_from_actor_login(self, fresh_db):
        ev = _make_issues_event("gh-actor-1", "opened", issue_num=1, actor="zealchaiwut")
        with patch.object(sync_mod, "_fetch_events", return_value=([ev], {"remaining": 100})):
            sync_mod.sync_github_events("owner/repo", "owner/repo", db_module=fresh_db)
        rows = _get_github_events(fresh_db)
        assert rows[0]["actor"] == "zealchaiwut"

    def test_todo_comment_present_in_source(self):
        """AC-6: TODO comment about bot-identity attribution present in github_events_sync.py."""
        src = (DASHBOARD_DIR / "github_events_sync.py").read_text()
        assert "TODO" in src
        assert "bot" in src.lower() or "identity" in src.lower() or "attribution" in src.lower()


# ── AC-7: Poll bounded to one page ────────────────────────────────────────────

class TestPollBounded:
    """AC-7: At most POLL_PAGE_SIZE events fetched; per_page passed to API."""

    def test_poll_page_size_constant_defined(self):
        assert hasattr(sync_mod, "POLL_PAGE_SIZE")
        assert sync_mod.POLL_PAGE_SIZE <= 300

    def test_fetch_events_called_with_per_page(self, fresh_db):
        captured = {}

        def fake_fetch(owner, repo, per_page=100):
            captured["per_page"] = per_page
            return ([], {"remaining": 100})

        with patch.object(sync_mod, "_fetch_events", side_effect=fake_fetch):
            sync_mod.sync_github_events("owner/repo", "owner/repo", db_module=fresh_db)

        assert "per_page" in captured, "_fetch_events not called"
        assert captured["per_page"] <= 100  # one page at GitHub max

    def test_only_first_n_events_stored_when_api_returns_more(self, fresh_db):
        """Even if API returns 100 items, we store at most POLL_PAGE_SIZE."""
        events = [
            _make_issues_event(f"gh-bulk-{i}", "opened", issue_num=i)
            for i in range(1, 101)
        ]
        with patch.object(sync_mod, "_fetch_events", return_value=(events, {"remaining": 100})):
            result = sync_mod.sync_github_events("owner/repo", "owner/repo", db_module=fresh_db)

        assert result["synced"] <= sync_mod.POLL_PAGE_SIZE


# ── AC-8: Rate-limit check ────────────────────────────────────────────────────

class TestRateLimitCheck:
    """AC-8: remaining < threshold → skip poll and log warning."""

    def test_rate_limited_returns_rate_limited_true(self, fresh_db):
        ev = _make_issues_event("gh-rl-1", "opened", issue_num=1)
        with patch.object(sync_mod, "_fetch_events", return_value=([ev], {"remaining": 5})):
            result = sync_mod.sync_github_events("owner/repo", "owner/repo", db_module=fresh_db)

        assert result["rate_limited"] is True

    def test_rate_limited_inserts_no_rows(self, fresh_db):
        ev = _make_issues_event("gh-rl-2", "opened", issue_num=2)
        with patch.object(sync_mod, "_fetch_events", return_value=([ev], {"remaining": 5})):
            sync_mod.sync_github_events("owner/repo", "owner/repo", db_module=fresh_db)

        assert _count_github_events(fresh_db) == 0

    def test_rate_limited_logs_warning(self, fresh_db, caplog):
        ev = _make_issues_event("gh-rl-3", "opened", issue_num=3)
        with patch.object(sync_mod, "_fetch_events", return_value=([ev], {"remaining": 9})):
            with caplog.at_level(logging.WARNING, logger="github_events_sync"):
                sync_mod.sync_github_events("owner/repo", "owner/repo", db_module=fresh_db)

        assert any("rate" in r.message.lower() for r in caplog.records)

    def test_threshold_constant_defined(self):
        assert hasattr(sync_mod, "RATE_LIMIT_THRESHOLD")
        assert sync_mod.RATE_LIMIT_THRESHOLD >= 5


# ── AC-9: Unsupported event types ignored ────────────────────────────────────

class TestUnsupportedEventTypes:
    """AC-9: Events not in supported list are not stored."""

    def test_push_event_ignored(self, fresh_db):
        push_ev = {
            "id": "gh-push-1",
            "type": "PushEvent",
            "actor": {"login": "alice"},
            "payload": {"commits": []},
            "created_at": "2026-06-08T10:00:00Z",
        }
        with patch.object(sync_mod, "_fetch_events", return_value=([push_ev], {"remaining": 100})):
            sync_mod.sync_github_events("owner/repo", "owner/repo", db_module=fresh_db)

        assert _count_github_events(fresh_db) == 0

    def test_watch_event_ignored(self, fresh_db):
        watch_ev = {
            "id": "gh-watch-1",
            "type": "WatchEvent",
            "actor": {"login": "alice"},
            "payload": {"action": "started"},
            "created_at": "2026-06-08T10:00:00Z",
        }
        with patch.object(sync_mod, "_fetch_events", return_value=([watch_ev], {"remaining": 100})):
            sync_mod.sync_github_events("owner/repo", "owner/repo", db_module=fresh_db)

        assert _count_github_events(fresh_db) == 0

    def test_create_event_tag_type_ignored(self, fresh_db):
        """CreateEvent for ref_type=tag (not branch) should be ignored."""
        tag_ev = {
            "id": "gh-tag-1",
            "type": "CreateEvent",
            "actor": {"login": "alice"},
            "payload": {"ref_type": "tag", "ref": "v1.0.0"},
            "created_at": "2026-06-08T10:00:00Z",
        }
        with patch.object(sync_mod, "_fetch_events", return_value=([tag_ev], {"remaining": 100})):
            sync_mod.sync_github_events("owner/repo", "owner/repo", db_module=fresh_db)

        assert _count_github_events(fresh_db) == 0

    def test_issues_event_unknown_action_ignored(self, fresh_db):
        """IssuesEvent with action=assigned (unsupported) is ignored."""
        ev = _make_issues_event("gh-assign-1", "assigned", issue_num=10)
        with patch.object(sync_mod, "_fetch_events", return_value=([ev], {"remaining": 100})):
            sync_mod.sync_github_events("owner/repo", "owner/repo", db_module=fresh_db)

        assert _count_github_events(fresh_db) == 0


# ── AC-1 + AC-2: Frontend wires sync on open and refresh ─────────────────────

class TestFrontendWiring:
    """AC-1 + AC-2: Frontend calls sync endpoint on logs open and manual refresh."""

    _HTML = (DASHBOARD_DIR / "static" / "project.html").read_text()

    def test_logs_sync_github_function_exists(self):
        """AC-1: logsSyncGitHub function defined in project.html."""
        assert "logsSyncGitHub" in self._HTML

    def test_logs_init_calls_sync_github(self):
        """AC-1: logsInit() calls logsSyncGitHub (triggered on Logs screen open)."""
        # Find logsInit body and check it references logsSyncGitHub
        idx = self._HTML.find("function logsInit(")
        assert idx != -1, "logsInit function not found"
        # Extract function body (next ~30 lines)
        snippet = self._HTML[idx: idx + 800]
        assert "logsSyncGitHub" in snippet, "logsInit does not call logsSyncGitHub"

    def test_manual_refresh_triggers_sync(self):
        """AC-2: A refresh control exists that calls logsSyncGitHub."""
        # Either a button with onclick=logsSyncGitHub or a refresh function that calls it
        assert "logsSyncGitHub" in self._HTML
        # The function should be callable via some UI element (button or interval)
        assert (
            "logsRefresh" in self._HTML or
            "onclick" in self._HTML and "logsSyncGitHub" in self._HTML
        )

    def test_sync_endpoint_called_with_post(self):
        """AC-1: Frontend POSTs to /api/logs/sync-github."""
        assert "/api/logs/sync-github" in self._HTML

    def test_sync_endpoint_exists_in_server(self):
        """AC-1: Backend exposes POST /api/logs/sync-github endpoint."""
        server_src = (DASHBOARD_DIR / "server.py").read_text()
        assert "/api/logs/sync-github" in server_src


# ── sync_github_events result shape ──────────────────────────────────────────

class TestSyncResult:
    """sync_github_events returns dict with synced, skipped, rate_limited keys."""

    def test_result_has_required_keys(self, fresh_db):
        with patch.object(sync_mod, "_fetch_events", return_value=([], {"remaining": 100})):
            result = sync_mod.sync_github_events("owner/repo", "owner/repo", db_module=fresh_db)

        assert "synced" in result
        assert "skipped" in result
        assert "rate_limited" in result

    def test_synced_count_matches_inserted_rows(self, fresh_db):
        events = [
            _make_issues_event("gh-cnt-1", "opened", issue_num=1),
            _make_issues_event("gh-cnt-2", "closed", issue_num=2),
        ]
        with patch.object(sync_mod, "_fetch_events", return_value=(events, {"remaining": 100})):
            result = sync_mod.sync_github_events("owner/repo", "owner/repo", db_module=fresh_db)

        assert result["synced"] == 2
        assert _count_github_events(fresh_db) == 2
