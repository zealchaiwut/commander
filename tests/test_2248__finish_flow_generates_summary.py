"""Tests for issue #2248: Attach sprint-summary generation to the Finish flow.

AC coverage:
  AC1 — finish_sprint calls write_sprint_summary after a successful merge
  AC2 — _read_sprint_summary_url can find URLs written by the Finish flow (via state file)
  AC3 — _generate_finish_summary stores summary_issue_url in state file (History can read it)
  AC4 — summary generation uses existing state file when available; falls back to
         minimal SprintState built from GitHub issues; end_reason is "complete"
"""
from __future__ import annotations

import json
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
    srv.db.get_sprint.return_value = {"started_at": "2026-08-01T10:00:00Z", "ended_at": "2026-08-01T11:00:00Z"}
    srv.db.update_sprint_pr_number = MagicMock()
    return srv


_FAKE_ISSUES_UAT = [
    {"number": 101, "title": "Issue A", "labels": [{"name": "UAT"}]},
    {"number": 102, "title": "Issue B", "labels": [{"name": "UAT"}]},
]

_FAKE_ISSUES_MIXED = [
    {"number": 101, "title": "Issue A", "labels": [{"name": "UAT"}]},
    {"number": 102, "title": "Issue B", "labels": [{"name": "sit"}]},
]


# ── AC1: finish_sprint calls write_sprint_summary on success ──────────────────

def test_ac1_finish_calls_write_sprint_summary():
    """AC1: a successful finish_sprint call must invoke write_sprint_summary."""
    client = _finish_client()
    mock_srv = _make_mock_srv(merge_errors=[])

    with (
        patch("routers.sprint_finish._server", return_value=mock_srv),
        patch("routers.sprint_finish._finish_sprint_issues", return_value=_FAKE_ISSUES_UAT),
        patch("routers.sprint_finish._finish_rework_tickets", return_value=[]),
        patch("routers.sprint_finish._generate_finish_summary") as mock_gen,
    ):
        mock_gen.return_value = "https://github.com/owner/repo/issues/99"
        r = client.post(
            "/api/sprints/sprint-42/finish?project=zealchaiwut/commander",
            json={"confirmed": True, "confirm_rework": True},
        )

    assert r.status_code in (200, 207), f"Expected success, got {r.status_code}: {r.text}"
    mock_gen.assert_called_once()


def test_ac1_write_summary_not_called_on_merge_failure():
    """AC1: when merge fails, write_sprint_summary must NOT be called (abort before close)."""
    client = _finish_client()
    mock_srv = _make_mock_srv(merge_errors=["merge conflict"])

    with (
        patch("routers.sprint_finish._server", return_value=mock_srv),
        patch("routers.sprint_finish._finish_sprint_issues", return_value=_FAKE_ISSUES_UAT),
        patch("routers.sprint_finish._finish_rework_tickets", return_value=[]),
        patch("routers.sprint_finish._generate_finish_summary") as mock_gen,
    ):
        client.post(
            "/api/sprints/sprint-42/finish?project=zealchaiwut/commander",
            json={"confirmed": True, "confirm_rework": True},
        )

    mock_gen.assert_not_called()


def test_ac1_summary_url_passed_to_sprint_finished_event():
    """AC1: the sprint_finished event includes the summary_issue_url from summary generation."""
    client = _finish_client()
    mock_srv = _make_mock_srv(merge_errors=[])
    expected_url = "https://github.com/zealchaiwut/commander/issues/99"

    with (
        patch("routers.sprint_finish._server", return_value=mock_srv),
        patch("routers.sprint_finish._finish_sprint_issues", return_value=_FAKE_ISSUES_UAT),
        patch("routers.sprint_finish._finish_rework_tickets", return_value=[]),
        patch("routers.sprint_finish._generate_finish_summary", return_value=expected_url),
    ):
        client.post(
            "/api/sprints/sprint-42/finish?project=zealchaiwut/commander",
            json={"confirmed": True, "confirm_rework": True},
        )

    # _emit_dashboard_event must have been called with the summary URL
    emit_call = mock_srv._emit_dashboard_event.call_args
    assert emit_call is not None
    detail = emit_call.kwargs.get("detail") or emit_call[1].get("detail") or {}
    assert detail.get("summary_issue_url") == expected_url, (
        f"sprint_finished event detail must include summary_issue_url={expected_url!r}, "
        f"got: {detail}"
    )


# ── AC2: _read_sprint_summary_url can find URLs written by Finish flow ─────────

def test_ac2_read_summary_url_reads_state_file(tmp_path):
    """AC2: _read_sprint_summary_url reads from state file as fallback (finds Finish-written URL)."""
    import startup
    import db as db_mod

    sprint_label = "sprint-99"
    expected_url = "https://github.com/owner/repo/issues/500"

    # Write a state file (as write_sprint_summary would)
    sprints_dir = tmp_path / ".commander" / "sprints"
    sprints_dir.mkdir(parents=True)
    state_file = sprints_dir / f"{sprint_label}-state.json"
    state_file.write_text(
        json.dumps({"sprint_label": sprint_label, "summary_issue_url": expected_url}),
        encoding="utf-8",
    )

    # Mock db so DB row has no summary_issue_url
    with patch.object(db_mod, "get_sprint", return_value={"state": "completed", "summary_issue_url": None}):
        result = startup._read_sprint_summary_url(tmp_path, sprint_label, "owner/repo")

    assert result == expected_url, (
        f"_read_sprint_summary_url must find URL in state file, got: {result!r}"
    )


def test_ac2_read_summary_url_prefers_db_row(tmp_path):
    """AC2: _read_sprint_summary_url returns DB row URL when present (DB takes priority)."""
    import startup
    import db as db_mod

    sprint_label = "sprint-99"
    db_url = "https://github.com/owner/repo/issues/200"
    state_url = "https://github.com/owner/repo/issues/500"

    sprints_dir = tmp_path / ".commander" / "sprints"
    sprints_dir.mkdir(parents=True)
    state_file = sprints_dir / f"{sprint_label}-state.json"
    state_file.write_text(
        json.dumps({"sprint_label": sprint_label, "summary_issue_url": state_url}),
        encoding="utf-8",
    )

    with patch.object(db_mod, "get_sprint", return_value={"summary_issue_url": db_url}):
        result = startup._read_sprint_summary_url(tmp_path, sprint_label, "owner/repo")

    assert result == db_url, (
        f"DB row URL must take priority over state file URL, got: {result!r}"
    )


# ── AC3: _generate_finish_summary stores URL in state file ─────────────────────

def test_ac3_generate_finish_summary_writes_state_file(tmp_path):
    """AC3: _generate_finish_summary writes summary_issue_url to the state file."""
    from routers.sprint_finish import _generate_finish_summary

    sprint_label = "sprint-55"
    repo = "zealchaiwut/commander"
    expected_url = "https://github.com/zealchaiwut/commander/issues/777"

    mock_srv = MagicMock()
    mock_srv.db.get_sprint.return_value = {
        "started_at": "2026-08-01T10:00:00Z",
        "ended_at": "2026-08-01T11:00:00Z",
    }

    # Prepare a state file so load_state_file finds it
    sprints_dir = tmp_path / ".commander" / "sprints"
    sprints_dir.mkdir(parents=True)
    state_file = sprints_dir / f"{sprint_label}-state.json"
    fake_state = {
        "sprint_label": sprint_label,
        "sprint_number": 55,
        "project": repo,
        "issues": [{"number": 10, "title": "T1", "status": "done"}],
        "start_timestamp": "2026-08-01T10:00:00Z",
        "wall_clock_secs": 3600.0,
    }
    state_file.write_text(json.dumps(fake_state), encoding="utf-8")

    def fake_write_sprint_summary(state, elapsed_secs, **kwargs):
        # Simulate write_sprint_summary storing the URL in the state file
        state.summary_issue_url = expected_url
        state_data = json.loads(state_file.read_text())
        state_data["summary_issue_url"] = expected_url
        state_file.write_text(json.dumps(state_data), encoding="utf-8")

    with patch("routers.sprint_finish.write_sprint_summary", fake_write_sprint_summary):
        url = _generate_finish_summary(mock_srv, tmp_path, sprint_label, repo, [])

    assert url == expected_url, f"Expected summary URL, got: {url!r}"
    state_data = json.loads(state_file.read_text())
    assert state_data.get("summary_issue_url") == expected_url, (
        f"summary_issue_url must be stored in state file, got: {state_data}"
    )


def test_ac3_history_can_read_summary_url_via_state_file(tmp_path):
    """AC3: after Finish flow, the History service can read summary_issue_url from state file."""
    from routers.sprint_artifact_service import load_state_file

    sprint_label = "sprint-55"
    expected_url = "https://github.com/zealchaiwut/commander/issues/777"

    sprints_dir = tmp_path / ".commander" / "sprints"
    sprints_dir.mkdir(parents=True)
    state_file = sprints_dir / f"{sprint_label}-state.json"
    state_file.write_text(
        json.dumps({"sprint_label": sprint_label, "summary_issue_url": expected_url}),
        encoding="utf-8",
    )

    state = load_state_file(sprints_dir, sprint_label)
    assert state is not None, "State file must be loadable"
    assert state.get("summary_issue_url") == expected_url, (
        f"History service must find summary_issue_url in state file: {state}"
    )


# ── AC4: summary uses existing state file; falls back to minimal state; end_reason="complete"

def test_ac4_uses_existing_state_file_when_available(tmp_path):
    """AC4: when a state file exists, _generate_finish_summary uses it (not a synthesised state)."""
    from routers.sprint_finish import _generate_finish_summary

    sprint_label = "sprint-55"
    repo = "zealchaiwut/commander"

    mock_srv = MagicMock()
    mock_srv.db.get_sprint.return_value = {}

    sprints_dir = tmp_path / ".commander" / "sprints"
    sprints_dir.mkdir(parents=True)
    state_file = sprints_dir / f"{sprint_label}-state.json"
    fake_state = {
        "sprint_label": sprint_label,
        "sprint_number": 55,
        "project": repo,
        "issues": [{"number": 10, "title": "Existing ticket", "status": "done"}],
        "start_timestamp": "2026-08-01T10:00:00Z",
        "wall_clock_secs": 7200.0,
    }
    state_file.write_text(json.dumps(fake_state), encoding="utf-8")

    captured_states: list = []

    def fake_write_sprint_summary(state, elapsed_secs, **kwargs):
        captured_states.append({"state": state, "elapsed_secs": elapsed_secs, "kwargs": kwargs})
        state.summary_issue_url = "https://github.com/owner/repo/issues/1"

    with patch("routers.sprint_finish.write_sprint_summary", fake_write_sprint_summary):
        _generate_finish_summary(mock_srv, tmp_path, sprint_label, repo, [])

    assert captured_states, "write_sprint_summary must have been called"
    used_state = captured_states[0]["state"]
    # The state from the file has elapsed 7200s; that would be the wall_clock_secs
    assert abs(captured_states[0]["elapsed_secs"] - 7200.0) < 1, (
        f"elapsed_secs must come from state file wall_clock_secs (7200), got: {captured_states[0]['elapsed_secs']}"
    )
    assert any(i.number == 10 for i in used_state.issues), (
        "State loaded from file must be used — issue #10 from state file must appear"
    )


def test_ac4_falls_back_to_minimal_state_when_no_state_file(tmp_path):
    """AC4: when no state file exists, _generate_finish_summary builds minimal SprintState."""
    from routers.sprint_finish import _generate_finish_summary

    sprint_label = "sprint-55"
    repo = "zealchaiwut/commander"
    sprint_issues = [
        {"number": 201, "title": "Ticket A", "labels": [{"name": "UAT"}]},
        {"number": 202, "title": "Ticket B", "labels": [{"name": "sit"}]},
    ]

    mock_srv = MagicMock()
    mock_srv.db.get_sprint.return_value = {
        "started_at": "2026-08-01T10:00:00Z",
        "ended_at": "2026-08-01T11:30:00Z",
    }

    # No state file in tmp_path
    captured_states: list = []

    def fake_write_sprint_summary(state, elapsed_secs, **kwargs):
        captured_states.append({"state": state, "elapsed_secs": elapsed_secs, "kwargs": kwargs})
        state.summary_issue_url = "https://github.com/owner/repo/issues/2"

    with patch("routers.sprint_finish.write_sprint_summary", fake_write_sprint_summary):
        _generate_finish_summary(mock_srv, tmp_path, sprint_label, repo, sprint_issues)

    assert captured_states, "write_sprint_summary must have been called"
    used_state = captured_states[0]["state"]
    issue_numbers = [i.number for i in used_state.issues]
    assert 201 in issue_numbers, f"UAT ticket #201 must appear in minimal state, got: {issue_numbers}"
    assert 202 in issue_numbers, f"Non-UAT ticket #202 must appear in minimal state, got: {issue_numbers}"

    uat_issue = next(i for i in used_state.issues if i.number == 201)
    assert uat_issue.status == "done", f"UAT ticket must have status=done, got: {uat_issue.status}"

    non_uat_issue = next(i for i in used_state.issues if i.number == 202)
    assert non_uat_issue.status == "skipped", (
        f"Non-UAT ticket must have status=skipped, got: {non_uat_issue.status}"
    )


def test_ac4_end_reason_is_complete(tmp_path):
    """AC4: summary is generated with end_reason='complete'."""
    from routers.sprint_finish import _generate_finish_summary

    sprint_label = "sprint-55"
    repo = "zealchaiwut/commander"

    mock_srv = MagicMock()
    mock_srv.db.get_sprint.return_value = {}

    captured_kwargs: list = []

    def fake_write_sprint_summary(state, elapsed_secs, **kwargs):
        captured_kwargs.append(kwargs)
        state.summary_issue_url = "https://github.com/owner/repo/issues/3"

    with patch("routers.sprint_finish.write_sprint_summary", fake_write_sprint_summary):
        _generate_finish_summary(mock_srv, tmp_path, sprint_label, repo, [])

    assert captured_kwargs, "write_sprint_summary must have been called"
    assert captured_kwargs[0].get("end_reason") == "complete", (
        f"end_reason must be 'complete', got: {captured_kwargs[0].get('end_reason')!r}"
    )


def test_ac4_sprint_summary_excluded_from_issue_list(tmp_path):
    """AC4: sprint-summary/docs issues are excluded from the minimal SprintState issues."""
    from routers.sprint_finish import _generate_finish_summary

    sprint_label = "sprint-55"
    repo = "zealchaiwut/commander"
    sprint_issues = [
        {"number": 201, "title": "Work ticket", "labels": [{"name": "UAT"}]},
        {"number": 999, "title": "Summary issue", "labels": [{"name": "sprint-summary"}]},
        {"number": 998, "title": "Docs issue", "labels": [{"name": "docs"}]},
    ]

    mock_srv = MagicMock()
    mock_srv.db.get_sprint.return_value = {}

    captured_states: list = []

    def fake_write_sprint_summary(state, elapsed_secs, **kwargs):
        captured_states.append(state)
        state.summary_issue_url = "https://github.com/owner/repo/issues/4"

    with patch("routers.sprint_finish.write_sprint_summary", fake_write_sprint_summary):
        _generate_finish_summary(mock_srv, tmp_path, sprint_label, repo, sprint_issues)

    assert captured_states, "write_sprint_summary must have been called"
    issue_numbers = [i.number for i in captured_states[0].issues]
    assert 201 in issue_numbers, "Work ticket must be included"
    assert 999 not in issue_numbers, "sprint-summary issue must be excluded"
    assert 998 not in issue_numbers, "docs issue must be excluded"


def test_ac4_summary_generation_failure_does_not_abort_finish():
    """AC4: if summary generation raises, finish_sprint still returns 200 (best-effort)."""
    client = _finish_client()
    mock_srv = _make_mock_srv(merge_errors=[])

    with (
        patch("routers.sprint_finish._server", return_value=mock_srv),
        patch("routers.sprint_finish._finish_sprint_issues", return_value=_FAKE_ISSUES_UAT),
        patch("routers.sprint_finish._finish_rework_tickets", return_value=[]),
        patch("routers.sprint_finish._generate_finish_summary", side_effect=RuntimeError("boom")),
    ):
        r = client.post(
            "/api/sprints/sprint-42/finish?project=zealchaiwut/commander",
            json={"confirmed": True, "confirm_rework": True},
        )

    assert r.status_code in (200, 207), (
        f"Summary generation failure must not abort finish — got {r.status_code}: {r.text}"
    )
