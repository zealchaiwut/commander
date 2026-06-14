"""Tests for issue #721: Emit scoped activity-log events for sprint lifecycle.

Acceptance Criteria:
- AC-1: POST /api/sprints/run emits _emit_dashboard_event(type="sprint_started")
        with project = the actual target repo (not the literal "dashboard").
- AC-2: POST /api/projects/{owner}/{repo}/sprints/{label}/finish emits
        type="sprint_finished", project = target repo, summary issue link in payload.
- AC-3: POST /api/sprints/{label}/rerun emits type="sprint_rerun",
        project = target repo, with the new sub-sprint label in the payload.
- AC-4: No instrumented endpoint emits project="dashboard" unless the sprint
        target is literally the dashboard repo.
- AC-5: Each event produces a row in the correct project's activity log
        (verifiable via the events-table query for that project).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))


# ── helpers ───────────────────────────────────────────────────────────────────

def make_capture():
    """Return (calls_list, capture_fn) for monkeypatching db.record_event."""
    calls = []

    def capture(project, source, actor, type, target, detail, action_id=None):
        calls.append({
            "project": project,
            "source": source,
            "actor": actor,
            "type": type,
            "target": target,
            "detail": detail,
            "action_id": action_id,
        })

    return calls, capture


@pytest.fixture
def server_module(monkeypatch):
    """Import server freshly with db.record_event captured."""
    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv

    calls, capture = make_capture()
    monkeypatch.setattr(srv.db, "record_event", capture)
    monkeypatch.setattr(srv._slog, "event", lambda *a, **kw: None)

    return srv, calls


class _stack:
    def __init__(self, ctx_managers):
        self._cms = ctx_managers
        self._entered = []

    def __enter__(self):
        for cm in self._cms:
            self._entered.append(cm.__enter__())
        return self

    def __exit__(self, *args):
        for cm in reversed(self._cms):
            cm.__exit__(*args)


def _run_managed(srv, tmp_path, project="owner/proj-run", label="sprint-90"):
    """Invoke POST /api/sprints/run with all I/O mocked. Returns response."""
    from fastapi.testclient import TestClient

    project_root = tmp_path / "proot"
    (project_root / ".commander" / "sprints").mkdir(parents=True, exist_ok=True)
    (project_root / ".commander" / "logs").mkdir(parents=True, exist_ok=True)
    coder_path = tmp_path / "coder"
    coder_path.mkdir(parents=True, exist_ok=True)

    fake_manager = tmp_path / "sprint_manager.py"
    fake_manager.write_text("")

    mock_proc = MagicMock()
    mock_proc.pid = 4242
    mock_proc.wait.side_effect = subprocess.TimeoutExpired("cmd", 2.0)

    patches = [
        patch("server.SPRINT_MANAGER_PATH", fake_manager),
        patch("server._DAG_BUILDER_AVAILABLE", False),
        patch("server._MIS_SIZING_AVAILABLE", False),
        patch("server._any_sprint_running", return_value=None),
        patch("server._project_root_path", return_value=project_root),
        patch("server._coder_clone_path", return_value=coder_path),
        patch("server._build_sprint_subprocess_env", return_value={}),
        patch("server._plan_json_set_state", return_value=None),
        patch("subprocess.Popen", return_value=mock_proc),
    ]
    with _stack(patches):
        client = TestClient(srv.app)
        return client.post("/api/sprints/run", json={"project": project, "sprint_label": label})


def _finish_sprint(srv, tmp_path, owner="owner", repo_name="proj-fin",
                   label="sprint-91", summary_url="https://github.com/owner/proj-fin/issues/777"):
    """Invoke POST /api/projects/{owner}/{repo}/sprints/{label}/finish, mocked."""
    from fastapi.testclient import TestClient

    project_root = tmp_path / "proot-fin"
    sprints_dir = project_root / ".commander" / "sprints"
    sprints_dir.mkdir(parents=True, exist_ok=True)
    # Persist the summary issue link in the sprint state file (where finish reads it).
    m = label.split("-")[1].split(".")[0]
    (sprints_dir / f"sprint-{m}-state.json").write_text(
        json.dumps({"sprint_label": label, "summary_issue_url": summary_url})
    )

    issues = [{"number": 5, "title": "Done", "labels": [{"name": label}, {"name": "UAT"}]}]

    patches = [
        patch("server._project_root_path", return_value=project_root),
        patch("server._is_sprint_running", return_value=False),
        patch("server._get_sprint_issues", return_value=issues),
        patch("server.children_of", return_value=[]),
        patch("server._merge_sprint_branch_chain", return_value=[]),
        patch("server._gh_branch_exists", return_value=False),
        patch("server._plan_json_set_state", return_value=None),
        patch.object(srv.github_client, "close_issue", return_value=None),
        patch.object(srv.github_client, "update_labels", return_value=None),
        patch.object(srv.github_client, "ensure_sprint_label", return_value=None),
        patch.object(srv.github_client, "invalidate", return_value=None),
    ]
    with _stack(patches):
        client = TestClient(srv.app)
        return client.post(
            f"/api/projects/{owner}/{repo_name}/sprints/{label}/finish",
            json={"confirmed": True, "selected_ticket_numbers": [5]},
        )


def _rerun_sprint(srv, tmp_path, project="owner/proj-rer", label="sprint-92"):
    """Invoke POST /api/sprints/{label}/rerun, mocked. Returns response."""
    from fastapi.testclient import TestClient

    project_root = tmp_path / "proot-rer"
    (project_root / ".commander" / "sprints").mkdir(parents=True, exist_ok=True)
    (project_root / ".commander" / "logs").mkdir(parents=True, exist_ok=True)
    coder_path = tmp_path / "coder-rer"
    coder_path.mkdir(parents=True, exist_ok=True)

    issues = [{"number": 9, "title": "Rework",
               "labels": [{"name": label}, {"name": "needs-rework"}]}]

    fake_manager = tmp_path / "sprint_manager.py"
    fake_manager.write_text("")

    mock_proc = MagicMock()
    mock_proc.pid = 5151
    mock_proc.wait.side_effect = subprocess.TimeoutExpired("cmd", 2.0)

    patches = [
        patch("server._is_sprint_running", return_value=False),
        patch("server._project_root_path", return_value=project_root),
        patch("server._coder_clone_path", return_value=coder_path),
        patch("server.SPRINT_MANAGER_PATH", fake_manager),
        patch("server._build_sprint_subprocess_env", return_value={}),
        patch("server._plan_json_set_state", return_value=None),
        patch("server._get_sprint_issues", return_value=issues),
        patch.object(srv.github_client, "list_labels", return_value=[]),
        patch.object(srv.github_client, "get_label_color", return_value="0075ca"),
        patch.object(srv.github_client, "create_label", return_value=None),
        patch.object(srv.github_client, "update_labels", return_value=None),
        patch.object(srv.github_client, "invalidate", return_value=None),
        patch("subprocess.Popen", return_value=mock_proc),
    ]
    with _stack(patches):
        client = TestClient(srv.app)
        return client.post(f"/api/sprints/{label}/rerun?project={project}",
                           json={"confirm": True})


# ── AC-1: sprint_started ──────────────────────────────────────────────────────

class TestSprintStartedEvent:
    def test_run_emits_sprint_started(self, server_module, tmp_path):
        srv, calls = server_module
        resp = _run_managed(srv, tmp_path, project="owner/proj-run", label="sprint-90")
        assert resp.status_code == 202, resp.text
        started = [c for c in calls if c["type"] == "sprint_started"]
        assert len(started) == 1, f"expected 1 sprint_started, got {len(started)}"

    def test_sprint_started_project_is_target_not_dashboard(self, server_module, tmp_path):
        srv, calls = server_module
        resp = _run_managed(srv, tmp_path, project="owner/proj-run", label="sprint-90")
        assert resp.status_code == 202, resp.text
        ev = next(c for c in calls if c["type"] == "sprint_started")
        assert ev["project"] == "owner/proj-run"
        assert ev["project"] != "dashboard"

    def test_sprint_started_payload_has_sprint_label(self, server_module, tmp_path):
        srv, calls = server_module
        resp = _run_managed(srv, tmp_path, project="owner/proj-run", label="sprint-90")
        assert resp.status_code == 202, resp.text
        ev = next(c for c in calls if c["type"] == "sprint_started")
        assert ev["detail"].get("sprint_id") == "sprint-90" or ev["target"] == "sprint-90"
        assert ev["source"] == "dashboard"
        assert ev["action_id"]
        uuid.UUID(ev["action_id"])


# ── AC-2: sprint_finished ─────────────────────────────────────────────────────

class TestSprintFinishedEvent:
    def test_finish_emits_sprint_finished(self, server_module, tmp_path):
        srv, calls = server_module
        resp = _finish_sprint(srv, tmp_path, owner="owner", repo_name="proj-fin", label="sprint-91")
        assert resp.status_code in (200, 207), resp.text
        finished = [c for c in calls if c["type"] == "sprint_finished"]
        assert len(finished) == 1, f"expected 1 sprint_finished, got {len(finished)}"

    def test_sprint_finished_project_is_target_repo(self, server_module, tmp_path):
        srv, calls = server_module
        resp = _finish_sprint(srv, tmp_path, owner="owner", repo_name="proj-fin", label="sprint-91")
        assert resp.status_code in (200, 207), resp.text
        ev = next(c for c in calls if c["type"] == "sprint_finished")
        assert ev["project"] == "owner/proj-fin"
        assert ev["project"] != "dashboard"

    def test_sprint_finished_payload_has_summary_issue_link(self, server_module, tmp_path):
        srv, calls = server_module
        url = "https://github.com/owner/proj-fin/issues/777"
        resp = _finish_sprint(srv, tmp_path, owner="owner", repo_name="proj-fin",
                              label="sprint-91", summary_url=url)
        assert resp.status_code in (200, 207), resp.text
        ev = next(c for c in calls if c["type"] == "sprint_finished")
        payload_vals = list(ev["detail"].values())
        assert url in payload_vals, f"summary issue link {url!r} not in payload {ev['detail']!r}"
        assert ev["source"] == "dashboard"
        assert ev["action_id"]
        uuid.UUID(ev["action_id"])


# ── AC-3: sprint_rerun ────────────────────────────────────────────────────────

class TestSprintRerunEvent:
    def test_rerun_emits_sprint_rerun(self, server_module, tmp_path):
        srv, calls = server_module
        resp = _rerun_sprint(srv, tmp_path, project="owner/proj-rer", label="sprint-92")
        assert resp.status_code == 200, resp.text
        rerun = [c for c in calls if c["type"] == "sprint_rerun"]
        assert len(rerun) == 1, f"expected 1 sprint_rerun, got {len(rerun)}"

    def test_sprint_rerun_project_is_target(self, server_module, tmp_path):
        srv, calls = server_module
        resp = _rerun_sprint(srv, tmp_path, project="owner/proj-rer", label="sprint-92")
        assert resp.status_code == 200, resp.text
        ev = next(c for c in calls if c["type"] == "sprint_rerun")
        assert ev["project"] == "owner/proj-rer"
        assert ev["project"] != "dashboard"

    def test_sprint_rerun_payload_has_sub_label(self, server_module, tmp_path):
        srv, calls = server_module
        resp = _rerun_sprint(srv, tmp_path, project="owner/proj-rer", label="sprint-92")
        assert resp.status_code == 200, resp.text
        ev = next(c for c in calls if c["type"] == "sprint_rerun")
        payload_vals = list(ev["detail"].values()) + [ev["target"]]
        assert "sprint-92.1" in payload_vals, (
            f"sub-sprint label 'sprint-92.1' not in event {ev!r}"
        )
        assert ev["source"] == "dashboard"
        assert ev["action_id"]
        uuid.UUID(ev["action_id"])


# ── AC-4: no scope leak to "dashboard" ────────────────────────────────────────

class TestNoDashboardScopeLeak:
    def test_run_finish_rerun_never_emit_dashboard_project(self, server_module, tmp_path):
        srv, calls = server_module
        _run_managed(srv, tmp_path / "r", project="owner/proj-run", label="sprint-90")
        _finish_sprint(srv, tmp_path / "f", owner="owner", repo_name="proj-fin", label="sprint-91")
        _rerun_sprint(srv, tmp_path / "x", project="owner/proj-rer", label="sprint-92")

        lifecycle = [c for c in calls
                     if c["type"] in ("sprint_started", "sprint_finished", "sprint_rerun")]
        assert lifecycle, "no lifecycle events captured"
        leaked = [c for c in lifecycle if c["project"] == "dashboard"]
        assert not leaked, f"lifecycle events leaked project='dashboard': {leaked}"


# ── AC-5: row lands in the correct project's activity log ─────────────────────

class TestRowInCorrectActivityLog:
    def test_sprint_started_row_scoped_to_target_project(self, monkeypatch, tmp_path):
        """End-to-end: real db.record_event writes a row under the target project,
        and none under 'dashboard'."""
        if "server" in sys.modules:
            del sys.modules["server"]
        import server as srv

        monkeypatch.setattr(srv._slog, "event", lambda *a, **kw: None)
        # Point the DB at a temp file and create the schema (get_conn reads DB_PATH
        # at call time, so patching the module global is sufficient).
        db_file = tmp_path / "events.db"
        monkeypatch.setattr(srv.db, "DB_PATH", db_file)
        srv.db.init_db()

        resp = _run_managed(srv, tmp_path, project="owner/proj-five", label="sprint-95")
        assert resp.status_code == 202, resp.text

        with srv.db.get_conn() as conn:
            target_rows = conn.execute(
                "SELECT * FROM events WHERE project = ? AND type = 'sprint_started'",
                ("owner/proj-five",),
            ).fetchall()
            dashboard_rows = conn.execute(
                "SELECT * FROM events WHERE project = 'dashboard' AND type = 'sprint_started'",
            ).fetchall()

        assert len(target_rows) == 1, "sprint_started row not found under target project"
        assert len(dashboard_rows) == 0, "sprint_started leaked into dashboard activity log"
