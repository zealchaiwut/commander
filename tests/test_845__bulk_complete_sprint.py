"""Tests for bulk-complete sprint (parent + child lineage settlement)."""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent


@contextmanager
def _stack(patches):
    started = [p.start() for p in patches]
    try:
        yield
    finally:
        for p in patches:
            p.stop()


def _import_server():
    import sys

    dash = REPO_ROOT / "apps" / "dashboard"
    if str(dash) not in sys.path:
        sys.path.insert(0, str(dash))
    import server as srv  # noqa: WPS433

    return srv


def _bulk_complete(srv, tmp_path, owner="owner", repo_name="proj-bc", label="sprint-68"):
    from fastapi.testclient import TestClient

    project_root = tmp_path / "proot-bc"
    sprints_dir = project_root / ".commander" / "sprints"
    sprints_dir.mkdir(parents=True, exist_ok=True)
    (sprints_dir / "sprint-68.1-plan.json").write_text("{}")
    (sprints_dir / "sprint-68.2-plan.json").write_text("{}")

    uat_issue = {
        "number": 10,
        "title": "UAT ticket",
        "labels": [{"name": "sprint-68.1"}, {"name": "UAT"}],
    }
    summary_issue = {
        "number": 20,
        "title": "Sprint 68 Executive Summary",
        "labels": [{"name": "sprint-summary"}],
    }

    def fake_get_issues(repo, sprint_label):
        if sprint_label == "sprint-68.1":
            return [uat_issue]
        return []

    def fake_summary(repo, labels):
        return [summary_issue]

    patches = [
        patch("server._project_root_path", return_value=project_root),
        patch("server._is_sprint_running", return_value=False),
        patch("server._sprint_merge_chain_pending", return_value=[]),
        patch("server._get_sprint_issues", side_effect=fake_get_issues),
        patch("server._open_summary_issues_for_labels", side_effect=fake_summary),
        patch("server._plan_json_set_state", return_value=None),
        patch.object(srv.github_client, "close_issue", return_value=None),
        patch.object(srv.github_client, "invalidate", return_value=None),
    ]
    with _stack(patches):
        client = TestClient(srv.app)
        return client.post(
            f"/api/projects/{owner}/{repo_name}/sprints/{label}/bulk-complete",
            json={"confirmed": True, "selected_ticket_numbers": [10, 20]},
        )


@pytest.fixture
def srv():
    return _import_server()


def test_bulk_complete_preview_requires_children(srv, tmp_path):
    from fastapi.testclient import TestClient

    project_root = tmp_path / "empty"
    (project_root / ".commander" / "sprints").mkdir(parents=True)

    with patch("server._project_root_path", return_value=project_root):
        client = TestClient(srv.app)
        resp = client.get("/api/projects/owner/proj/sprints/sprint-68/bulk-complete-preview")
    assert resp.status_code == 400
    assert "child" in resp.json()["detail"].lower()


def test_bulk_complete_closes_uat_and_summary_marks_members(srv, tmp_path):
    resp = _bulk_complete(srv, tmp_path)
    assert resp.status_code == 200
    data = resp.json()
    assert data["closed"] == 2
    assert data["completed"] == 3
    assert "sprint-68" in data["member_labels"]
    assert "sprint-68.1" in data["member_labels"]
    assert "sprint-68.2" in data["member_labels"]


def test_bulk_complete_mirrors_completed_into_lifecycle_db(srv, tmp_path):
    project_root = tmp_path / "proot-db"
    sprints_dir = project_root / ".commander" / "sprints"
    sprints_dir.mkdir(parents=True, exist_ok=True)
    (sprints_dir / "sprint-68.1-plan.json").write_text("{}")

    db_calls: list[tuple] = []

    def capture_db(label, project, state, **extra):
        db_calls.append((label, project, state, extra))

    patches = [
        patch("server._project_root_path", return_value=project_root),
        patch("server._is_sprint_running", return_value=False),
        patch("server._sprint_merge_chain_pending", return_value=[]),
        patch("server._get_sprint_issues", return_value=[]),
        patch("server._open_summary_issues_for_labels", return_value=[]),
        patch("server._plan_json_set_state", return_value=None),
        patch("server._sprint_db_set_state", side_effect=capture_db),
        patch.object(srv.github_client, "close_issue", return_value=None),
        patch.object(srv.github_client, "invalidate", return_value=None),
    ]
    with _stack(patches):
        from fastapi.testclient import TestClient

        client = TestClient(srv.app)
        resp = client.post(
            "/api/projects/owner/proj-bc/sprints/sprint-68/bulk-complete",
            json={"confirmed": True, "selected_ticket_numbers": []},
        )
    assert resp.status_code == 200
    assert {c[0] for c in db_calls} == {"sprint-68", "sprint-68.1"}
    assert all(c[2] == "completed" for c in db_calls)
    assert all(c[3].get("end_reason") == "bulk_complete" for c in db_calls)


def test_bulk_complete_preview_lists_summary_category(srv, tmp_path):
    from fastapi.testclient import TestClient

    project_root = tmp_path / "prev"
    sprints_dir = project_root / ".commander" / "sprints"
    sprints_dir.mkdir(parents=True)
    (sprints_dir / "sprint-68.1-plan.json").write_text("{}")

    patches = [
        patch("server._project_root_path", return_value=project_root),
        patch("server._get_sprint_issues", return_value=[]),
        patch(
            "server._open_summary_issues_for_labels",
            return_value=[{
                "number": 99,
                "title": "Sprint 68 Executive Summary",
                "labels": [{"name": "sprint-summary"}],
            }],
        ),
    ]
    with _stack(patches):
        client = TestClient(srv.app)
        resp = client.get("/api/projects/owner/proj/sprints/sprint-68/bulk-complete-preview")
    assert resp.status_code == 200
    tickets = resp.json()["all_tickets"]
    assert any(t["category"] == "sprint-summary" for t in tickets)


def test_bulk_complete_preview_includes_merge_steps(srv, tmp_path):
    from fastapi.testclient import TestClient

    project_root = tmp_path / "merge-prev"
    sprints_dir = project_root / ".commander" / "sprints"
    sprints_dir.mkdir(parents=True)
    (sprints_dir / "sprint-68.1-plan.json").write_text("{}")

    fake_steps = [{
        "kind": "merge",
        "head": "sprint/sprint-68.1",
        "base": "sprint/sprint-68",
        "label": "sprint-68.1 → sprint-68",
        "delete_branch": True,
        "title": "Merge Sprint: sprint-68.1 → sprint-68",
    }]

    patches = [
        patch("server._project_root_path", return_value=project_root),
        patch("server._get_sprint_issues", return_value=[]),
        patch("server._open_summary_issues_for_labels", return_value=[]),
        patch("server._merge_steps_for_sprint_chain", return_value=fake_steps),
    ]
    with _stack(patches):
        client = TestClient(srv.app)
        resp = client.get("/api/projects/owner/proj/sprints/sprint-68/bulk-complete-preview")
    assert resp.status_code == 200
    assert resp.json()["merge_steps"] == fake_steps


def test_bulk_complete_blocks_when_merge_chain_pending(srv, tmp_path):
    from fastapi.testclient import TestClient

    project_root = tmp_path / "pending"
    (project_root / ".commander" / "sprints").mkdir(parents=True)
    (project_root / ".commander" / "sprints" / "sprint-68.1-plan.json").write_text("{}")

    close_mock = MagicMock(return_value=None)
    patches = [
        patch("server._project_root_path", return_value=project_root),
        patch("server._is_sprint_running", return_value=False),
        patch(
            "server._sprint_merge_chain_pending",
            return_value=["sprint/sprint-68 → develop"],
        ),
        patch("server._get_sprint_issues", return_value=[]),
        patch("server._open_summary_issues_for_labels", return_value=[]),
        patch.object(srv.github_client, "close_issue", close_mock),
    ]
    with _stack(patches):
        client = TestClient(srv.app)
        resp = client.post(
            "/api/projects/owner/proj/sprints/sprint-68/bulk-complete",
            json={"confirmed": True, "selected_ticket_numbers": []},
        )
    assert resp.status_code == 409
    assert "merge" in resp.json()["detail"].lower()
    close_mock.assert_not_called()
