"""Tests for issue #1814 — mirror-first label reads may serve stale labels.

After state_machine.transition() writes labels via gh issue edit, the DB mirror
row for that issue is now deleted (invalidated) so the next _get_issue_labels
call falls back to REST and returns live state, not a stale cached copy.

AC1  db.invalidate_mirrored_issue(repo, issue_number) deletes the mirror row
AC2  After invalidation, db.get_mirrored_issue returns None for that issue
AC3  state_machine.transition() invalidates the mirror row after a successful edit
AC4  _get_issue_labels returns REST-live labels when the mirror was invalidated
"""
from __future__ import annotations

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

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")

import db as _db  # noqa: E402


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def fresh_db(tmp_path):
    """Isolated SQLite DB with one pre-populated issue row."""
    db_file = tmp_path / "test_1814.db"
    original = _db.DB_PATH
    _db.DB_PATH = db_file
    _db.init_db()
    yield _db
    _db.DB_PATH = original


def _seed_issue(db_mod, repo: str, num: int, labels: list[str]) -> None:
    db_mod.upsert_issues(repo, [{
        "number": num,
        "title": f"Issue #{num}",
        "state": "open",
        "labels": [{"name": n} for n in labels],
        "updatedAt": "2026-07-01T00:00:00",
    }])


def _rest_ok(*label_names: str) -> MagicMock:
    m = MagicMock()
    m.returncode = 0
    m.stdout = json.dumps({"labels": [{"name": n} for n in label_names]})
    m.stderr = ""
    return m


def _edit_ok() -> MagicMock:
    m = MagicMock()
    m.returncode = 0
    m.stdout = ""
    m.stderr = ""
    return m


# ── AC1: db.invalidate_mirrored_issue exists and deletes the row ──────────────

def test_ac1_invalidate_mirrored_issue_exists():
    """AC1: db module exposes invalidate_mirrored_issue function."""
    assert callable(getattr(_db, "invalidate_mirrored_issue", None)), (
        "db.invalidate_mirrored_issue must exist and be callable"
    )


def test_ac1_invalidate_deletes_the_row(fresh_db):
    """AC1: invalidate_mirrored_issue removes the mirror row from the DB."""
    repo = "owner/test-repo"
    _seed_issue(fresh_db, repo, 42, ["in-progress"])
    assert fresh_db.get_mirrored_issue(repo, 42) is not None, "Row must exist before invalidation"
    fresh_db.invalidate_mirrored_issue(repo, 42)
    result = fresh_db.get_mirrored_issue(repo, 42)
    assert result is None, f"Expected None after invalidation, got {result}"


# ── AC2: get_mirrored_issue returns None after invalidation ───────────────────

def test_ac2_get_mirrored_issue_returns_none_after_invalidation(fresh_db):
    """AC2: After invalidate_mirrored_issue, get_mirrored_issue returns None."""
    repo = "owner/repo-ac2"
    _seed_issue(fresh_db, repo, 99, ["SIT", "sprint-10"])
    assert fresh_db.get_mirrored_issue(repo, 99) is not None, "Pre-condition: row must exist"
    fresh_db.invalidate_mirrored_issue(repo, 99)
    assert fresh_db.get_mirrored_issue(repo, 99) is None


def test_ac2_invalidate_is_noop_for_missing_row(fresh_db):
    """AC2: invalidate_mirrored_issue on a non-existent row does not raise."""
    repo = "owner/repo-ac2b"
    # Issue 9999 does not exist — must not raise
    fresh_db.invalidate_mirrored_issue(repo, 9999)


def test_ac2_other_issues_unaffected(fresh_db):
    """AC2: invalidating one issue does not remove other issues from the mirror."""
    repo = "owner/repo-ac2c"
    _seed_issue(fresh_db, repo, 10, ["backlog"])
    _seed_issue(fresh_db, repo, 20, ["in-progress"])
    fresh_db.invalidate_mirrored_issue(repo, 10)
    assert fresh_db.get_mirrored_issue(repo, 10) is None, "Issue 10 should be gone"
    assert fresh_db.get_mirrored_issue(repo, 20) is not None, "Issue 20 must remain"


# ── AC3: transition() invalidates the mirror after a successful edit ──────────

def test_ac3_transition_invalidates_mirror_on_success(fresh_db):
    """AC3: state_machine.transition() calls db.invalidate_mirrored_issue after gh issue edit succeeds."""
    from services.sprint_manager.state_machine import TicketState, transition  # noqa: PLC0415

    invalidate_calls: list[tuple] = []
    original_fn = fresh_db.invalidate_mirrored_issue

    def spy_invalidate(repo: str, issue_number: int) -> None:
        invalidate_calls.append((repo, issue_number))
        original_fn(repo, issue_number)

    def fake_subprocess(args, **kwargs):
        cmd = [str(a) for a in args]
        if "api" in cmd:
            return _rest_ok("backlog", "sprint-1")
        return _edit_ok()

    with patch("services.sprint_manager.state_machine.subprocess.run",
               side_effect=fake_subprocess):
        with patch.object(fresh_db, "invalidate_mirrored_issue", side_effect=spy_invalidate):
            with patch.dict("sys.modules", {"db": fresh_db}):
                transition(55, TicketState.IN_PROGRESS, actor="test", repo="owner/repo")

    assert any(c[1] == 55 for c in invalidate_calls), (
        f"Expected db.invalidate_mirrored_issue(_, 55) to be called after transition; "
        f"actual calls: {invalidate_calls}"
    )


def test_ac3_transition_does_not_invalidate_on_noop(fresh_db):
    """AC3: transition() skips invalidation when already in the desired state (no edit needed)."""
    from services.sprint_manager.state_machine import TicketState, transition  # noqa: PLC0415

    invalidate_calls: list = []

    def spy_invalidate(repo: str, issue_number: int) -> None:
        invalidate_calls.append((repo, issue_number))

    def fake_subprocess(args, **kwargs):
        # Issue already has in-progress — transition is a no-op
        return _rest_ok("in-progress", "sprint-1")

    with patch("services.sprint_manager.state_machine.subprocess.run",
               side_effect=fake_subprocess):
        with patch.object(fresh_db, "invalidate_mirrored_issue", side_effect=spy_invalidate):
            with patch.dict("sys.modules", {"db": fresh_db}):
                changed = transition(77, TicketState.IN_PROGRESS, actor="test", repo="owner/repo")

    assert changed is False, "Expected no-op (False return) when already in target state"
    assert invalidate_calls == [], (
        f"Expected no db.invalidate_mirrored_issue call on noop; got {invalidate_calls}"
    )


# ── AC4: _get_issue_labels falls back to REST after mirror is cleared ─────────

def test_ac4_get_issue_labels_uses_rest_when_mirror_none():
    """AC4: _get_issue_labels returns REST-live labels when mirror returns None (simulating post-invalidation)."""
    sys.path.insert(0, str(DASHBOARD_DIR))
    from services.sprint_manager.label_transitions import _get_issue_labels  # noqa: PLC0415

    rest_calls: list = []

    def counting_subprocess(args, **kwargs):
        cmd = [str(a) for a in args]
        if "api" in cmd:
            rest_calls.append(cmd)
        m = MagicMock()
        m.returncode = 0
        m.stdout = json.dumps({"labels": [{"name": "SIT"}, {"name": "sprint-5"}]})
        m.stderr = ""
        return m

    import github_client as gc  # noqa: PLC0415
    with patch.object(gc, "_mirror_issue", return_value=None):
        with patch("subprocess.run", side_effect=counting_subprocess):
            result = _get_issue_labels(123, "owner/repo")

    assert "SIT" in result, f"Expected 'SIT' from REST fallback; got {result}"
    assert "sprint-5" in result, f"Expected 'sprint-5' from REST fallback; got {result}"
    assert len(rest_calls) >= 1, "Expected at least one REST 'gh api' call"


def test_ac4_stale_mirror_not_returned_after_invalidation(fresh_db):
    """AC4: labels returned by _get_issue_labels reflect live REST data after mirror row is deleted."""
    sys.path.insert(0, str(DASHBOARD_DIR))
    from services.sprint_manager.label_transitions import _get_issue_labels  # noqa: PLC0415

    repo = "owner/repo-ac4"
    _seed_issue(fresh_db, repo, 77, ["in-progress"])  # stale mirror shows in-progress

    # Simulate post-transition invalidation
    fresh_db.invalidate_mirrored_issue(repo, 77)
    assert fresh_db.get_mirrored_issue(repo, 77) is None, "Mirror row must be gone after invalidation"

    rest_calls: list = []

    def counting_subprocess(args, **kwargs):
        cmd = [str(a) for a in args]
        if "api" in cmd:
            rest_calls.append(cmd)
        m = MagicMock()
        m.returncode = 0
        m.stdout = json.dumps({"labels": [{"name": "SIT"}]})
        m.stderr = ""
        return m

    import github_client as gc  # noqa: PLC0415
    with patch.object(gc, "_mirror_issue", return_value=None):
        with patch("subprocess.run", side_effect=counting_subprocess):
            result = _get_issue_labels(77, repo)

    assert "SIT" in result, f"Expected REST-fresh 'SIT'; got {result}"
    assert "in-progress" not in result, f"Stale 'in-progress' must not appear; got {result}"
    assert len(rest_calls) >= 1, "Expected REST fallback call after mirror invalidation"
