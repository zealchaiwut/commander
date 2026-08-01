"""Tests for issue #2086: finish endpoint must abort when merge fails.

AC coverage:
  AC1 — finish_sprint returns error when _merge_sprint_branches_for_label reports failure
  AC2 — _get_branch_tip_sha and _is_commit_merged_into_branch helpers exist in startup.py
  AC3 — bulk-complete-preview reports merged=False for absent branch with no merged PR;
         reports merged=True for absent branch that has a merged PR
  AC4 — behavioral: stubbed failing merge → 4xx response, close_issue NOT called,
         _sprint_db_mark_merged_completed NOT called
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _finish_client() -> TestClient:
    from routers.sprint_finish import router as finish_router
    app = FastAPI()
    app.include_router(finish_router)
    return TestClient(app, raise_server_exceptions=False)


def _make_mock_srv(merge_errors: list[str] | None = None):
    """Return a minimal mock server with configurable merge outcome."""
    srv = MagicMock()
    srv._SPRINT_LABEL_RE = re.compile(r"^sprint-\d+(\.\d+)?$")
    srv._FINISH_SPRINT_STATUS_LABELS = {"sit", "in-progress", "backlog"}
    srv._is_sprint_running.return_value = False
    srv._sprint_label_base.side_effect = lambda lbl: lbl.split(".")[0] if "." in lbl else lbl
    srv._is_child_sprint_label.side_effect = lambda lbl: "." in lbl
    srv.children_of.return_value = []
    srv._project_root_path.return_value = Path("/fake/project")
    srv._merge_sprint_branches_for_label.return_value = (merge_errors or [], None)
    srv.github_client.close_issue = MagicMock()
    srv._sprint_db_mark_merged_completed = MagicMock(return_value=True)
    srv._plan_json_set_state = MagicMock()
    srv.broadcast = AsyncMock()
    srv._emit_dashboard_event = MagicMock()
    srv._next_sprint_number.return_value = 43
    srv._read_sprint_summary_url.return_value = None
    return srv


# UAT issues — all have UAT labels so rework guard passes without extra mocking
_FAKE_ISSUES = [
    {"number": 101, "title": "Issue A", "labels": [{"name": "UAT"}]},
    {"number": 102, "title": "Issue B", "labels": [{"name": "UAT"}]},
]


# ── AC1: merge failure returns an HTTP error ───────────────────────────────────

def test_ac1_merge_failure_returns_4xx():
    """AC1: finish must return 4xx (not 200) when _merge_sprint_branches_for_label reports errors."""
    client = _finish_client()
    mock_srv = _make_mock_srv(merge_errors=["sprint/sprint-42 → develop: merge conflict"])

    with (
        patch("routers.sprint_finish._server", return_value=mock_srv),
        patch("routers.sprint_finish._finish_sprint_issues", return_value=_FAKE_ISSUES),
        patch("routers.sprint_finish._finish_rework_tickets", return_value=[]),
    ):
        r = client.post(
            "/api/sprints/sprint-42/finish?project=zealchaiwut/commander",
            json={"confirmed": True, "confirm_rework": True},
        )

    assert r.status_code in (400, 409, 500), (
        f"Expected 4xx/500 when merge fails, got {r.status_code}: {r.text}"
    )


def test_ac1_merge_failure_response_mentions_merge():
    """AC1: error response must communicate the merge failure clearly."""
    client = _finish_client()
    error_msg = "sprint/sprint-42 → develop: ref not found"
    mock_srv = _make_mock_srv(merge_errors=[error_msg])

    with (
        patch("routers.sprint_finish._server", return_value=mock_srv),
        patch("routers.sprint_finish._finish_sprint_issues", return_value=_FAKE_ISSUES),
        patch("routers.sprint_finish._finish_rework_tickets", return_value=[]),
    ):
        r = client.post(
            "/api/sprints/sprint-42/finish?project=zealchaiwut/commander",
            json={"confirmed": True, "confirm_rework": True},
        )

    body = r.json()
    assert "detail" in body, f"Expected 'detail' key in error response, got: {body}"
    detail_str = str(body["detail"])
    assert "merge" in detail_str.lower() or error_msg in detail_str, (
        f"Error body must mention merge failure, got: {detail_str}"
    )


# ── AC2: post-merge verification helpers exist in startup.py ──────────────────

def test_ac2_get_branch_tip_sha_exists():
    """AC2: _get_branch_tip_sha must exist in startup.py."""
    import startup
    assert hasattr(startup, "_get_branch_tip_sha"), (
        "startup._get_branch_tip_sha not found — needed to capture head SHA before merging"
    )
    assert callable(startup._get_branch_tip_sha)


def test_ac2_is_commit_merged_into_branch_exists():
    """AC2: _is_commit_merged_into_branch must exist in startup.py."""
    import startup
    assert hasattr(startup, "_is_commit_merged_into_branch"), (
        "startup._is_commit_merged_into_branch not found — needed for post-merge verification"
    )
    assert callable(startup._is_commit_merged_into_branch)


def test_ac2_merge_sprint_branches_for_label_verifies_after_merge():
    """AC2: _merge_sprint_branches_for_label must call the post-merge verification helper."""
    src = Path(DASHBOARD_DIR / "startup.py").read_text(encoding="utf-8")
    # The function body should reference the post-merge check
    fn_start = src.find("def _merge_sprint_branches_for_label(")
    assert fn_start != -1, "_merge_sprint_branches_for_label not found"
    # Find next top-level def after it
    fn_body_end = src.find("\ndef ", fn_start + 1)
    fn_body = src[fn_start:fn_body_end]
    assert "_is_commit_merged_into_branch" in fn_body or "_get_branch_tip_sha" in fn_body, (
        "_merge_sprint_branches_for_label must reference the post-merge verification helpers (AC2)"
    )


# ── AC3: bulk-complete-preview merged computation ─────────────────────────────

def _bulk_preview_client() -> TestClient:
    from routers.sprint_finish import router as finish_router
    app = FastAPI()
    app.include_router(finish_router)
    return TestClient(app, raise_server_exceptions=False)


def _make_bulk_preview_mock_srv(*, branch_exists: bool, has_merged_pr: bool):
    """Mock server for bulk-complete-preview tests."""
    srv = MagicMock()
    srv._SPRINT_LABEL_RE = re.compile(r"^sprint-\d+(\.\d+)?$")
    srv._sprint_label_base.side_effect = lambda lbl: lbl.split(".")[0] if "." in lbl else lbl
    srv._is_child_sprint_label.side_effect = lambda lbl: "." in lbl
    srv._project_root_path.return_value = Path("/fake/project")
    # all_labels includes both base + child; sprint_issues empty for simplicity
    srv._bulk_complete_collect_issues.return_value = (["sprint-42", "sprint-42.1"], [])
    srv._bulk_complete_unsettled_children.return_value = []
    # merge_steps is empty because _branch_has_unmerged_commits returned False for absent branch
    srv._bulk_complete_merge_steps.return_value = []
    srv._sprint_label_sub_index.side_effect = lambda lbl: int(lbl.split(".")[-1]) if "." in lbl else 0
    srv._sprint_branch_name.side_effect = lambda lbl: f"sprint/{lbl}"
    srv._sprint_merge_parent_label.return_value = "sprint-42"
    srv._bulk_complete_ticket_rows.return_value = []
    srv.db.get_sprint.return_value = {"state": "needs_rework"}
    srv.db.canonical_lifecycle.return_value = "needs_rework"
    srv.db.agent_runs_for_sprint.return_value = []
    # The key settings for the AC3 distinction
    srv._gh_branch_exists.return_value = branch_exists
    srv._has_merged_pr.return_value = has_merged_pr
    return srv


def test_ac3_absent_no_pr_reports_merged_false():
    """AC3: member whose branch is absent AND has no merged PR must have merged=False."""
    client = _bulk_preview_client()
    mock_srv = _make_bulk_preview_mock_srv(branch_exists=False, has_merged_pr=False)

    with patch("routers.sprint_finish._server", return_value=mock_srv):
        r = client.get(
            "/api/sprints/sprint-42/bulk-complete-preview?project=zealchaiwut/commander"
        )

    assert r.status_code == 200, f"Got {r.status_code}: {r.text}"
    body = r.json()
    members = body.get("members", [])
    child = next((m for m in members if m["label"] == "sprint-42.1"), None)
    assert child is not None, f"sprint-42.1 not found in members: {members}"
    assert child["merged"] is False, (
        f"Expected merged=False for absent branch with no merged PR, got: {child}"
    )


def test_ac3_absent_with_merged_pr_reports_merged_true():
    """AC3: member whose branch is absent AND has a merged PR must have merged=True."""
    client = _bulk_preview_client()
    mock_srv = _make_bulk_preview_mock_srv(branch_exists=False, has_merged_pr=True)

    with patch("routers.sprint_finish._server", return_value=mock_srv):
        r = client.get(
            "/api/sprints/sprint-42/bulk-complete-preview?project=zealchaiwut/commander"
        )

    assert r.status_code == 200, f"Got {r.status_code}: {r.text}"
    body = r.json()
    members = body.get("members", [])
    child = next((m for m in members if m["label"] == "sprint-42.1"), None)
    assert child is not None, f"sprint-42.1 not found in members: {members}"
    assert child["merged"] is True, (
        f"Expected merged=True for absent branch with merged PR, got: {child}"
    )


def test_ac3_present_branch_still_reports_correctly():
    """AC3: existing branch with no pending commits reports merged=True (positive path)."""
    client = _bulk_preview_client()
    mock_srv = _make_bulk_preview_mock_srv(branch_exists=True, has_merged_pr=False)

    with patch("routers.sprint_finish._server", return_value=mock_srv):
        r = client.get(
            "/api/sprints/sprint-42/bulk-complete-preview?project=zealchaiwut/commander"
        )

    assert r.status_code == 200, f"Got {r.status_code}: {r.text}"
    body = r.json()
    members = body.get("members", [])
    child = next((m for m in members if m["label"] == "sprint-42.1"), None)
    assert child is not None, f"sprint-42.1 not found in members: {members}"
    assert child["merged"] is True, (
        f"Expected merged=True when branch exists with no pending commits, got: {child}"
    )


# ── AC4: behavioral — stubbed failing merge, issues not closed, not completed ─

def test_ac4_failing_merge_does_not_close_issues():
    """AC4: when merge step fails (stub), close_issue must NOT be called."""
    client = _finish_client()
    mock_srv = _make_mock_srv(merge_errors=["sprint/sprint-42 → develop: no-op merge"])

    with (
        patch("routers.sprint_finish._server", return_value=mock_srv),
        patch("routers.sprint_finish._finish_sprint_issues", return_value=_FAKE_ISSUES),
        patch("routers.sprint_finish._finish_rework_tickets", return_value=[]),
    ):
        client.post(
            "/api/sprints/sprint-42/finish?project=zealchaiwut/commander",
            json={"confirmed": True, "confirm_rework": True},
        )

    mock_srv.github_client.close_issue.assert_not_called()


def test_ac4_failing_merge_does_not_mark_completed():
    """AC4: when merge step fails (stub), _sprint_db_mark_merged_completed must NOT be called."""
    client = _finish_client()
    mock_srv = _make_mock_srv(merge_errors=["sprint/sprint-42 → develop: git push failed"])

    with (
        patch("routers.sprint_finish._server", return_value=mock_srv),
        patch("routers.sprint_finish._finish_sprint_issues", return_value=_FAKE_ISSUES),
        patch("routers.sprint_finish._finish_rework_tickets", return_value=[]),
    ):
        client.post(
            "/api/sprints/sprint-42/finish?project=zealchaiwut/commander",
            json={"confirmed": True, "confirm_rework": True},
        )

    mock_srv._sprint_db_mark_merged_completed.assert_not_called()


def test_ac4_failing_merge_does_not_update_plan_json():
    """AC4: when merge step fails (stub), _plan_json_set_state must NOT be called."""
    client = _finish_client()
    mock_srv = _make_mock_srv(merge_errors=["sprint/sprint-42 → develop: pr create failed"])

    with (
        patch("routers.sprint_finish._server", return_value=mock_srv),
        patch("routers.sprint_finish._finish_sprint_issues", return_value=_FAKE_ISSUES),
        patch("routers.sprint_finish._finish_rework_tickets", return_value=[]),
    ):
        client.post(
            "/api/sprints/sprint-42/finish?project=zealchaiwut/commander",
            json={"confirmed": True, "confirm_rework": True},
        )

    mock_srv._plan_json_set_state.assert_not_called()


# ── Positive path: successful merge still works ────────────────────────────────

def test_successful_merge_closes_issues():
    """Positive path: successful merge still closes issues (no regression)."""
    client = _finish_client()
    mock_srv = _make_mock_srv(merge_errors=[])

    with (
        patch("routers.sprint_finish._server", return_value=mock_srv),
        patch("routers.sprint_finish._finish_sprint_issues", return_value=_FAKE_ISSUES),
        patch("routers.sprint_finish._finish_rework_tickets", return_value=[]),
    ):
        r = client.post(
            "/api/sprints/sprint-42/finish?project=zealchaiwut/commander",
            json={"confirmed": True, "confirm_rework": True},
        )

    assert r.status_code in (200, 207), f"Expected 200/207 on success, got {r.status_code}: {r.text}"
    assert mock_srv.github_client.close_issue.call_count == len(_FAKE_ISSUES)


def test_successful_merge_marks_completed():
    """Positive path: successful merge marks sprint completed (no regression)."""
    client = _finish_client()
    mock_srv = _make_mock_srv(merge_errors=[])

    with (
        patch("routers.sprint_finish._server", return_value=mock_srv),
        patch("routers.sprint_finish._finish_sprint_issues", return_value=_FAKE_ISSUES),
        patch("routers.sprint_finish._finish_rework_tickets", return_value=[]),
    ):
        client.post(
            "/api/sprints/sprint-42/finish?project=zealchaiwut/commander",
            json={"confirmed": True, "confirm_rework": True},
        )

    mock_srv._sprint_db_mark_merged_completed.assert_called()
