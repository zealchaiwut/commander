"""Behavioral AC tests for dispatch-by-label resolution (issue #2353).

No live GitHub: the issues mirror / list_issues path is stubbed. start_run is
stubbed so no agent subprocess is spawned.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))


def _load_sprints_router():
    import routers.sprints as routers_sprints

    return routers_sprints


def _issue(number: int, labels: list[str], state: str = "open") -> dict:
    return {
        "number": number,
        "state": state,
        "title": f"Issue {number}",
        "labels": [{"name": n} for n in labels],
    }


@pytest.fixture
def dispatch_env(tmp_path, monkeypatch):
    routers_sprints = _load_sprints_router()
    monkeypatch.setattr(routers_sprints, "_commander_repo_root", lambda: tmp_path)

    import services.sprint_manager.dispatch_runner as dr

    class _Config:
        repo_name = "owner/repo"

    monkeypatch.setattr(dr, "load_project_config", lambda cwd: _Config())
    monkeypatch.setattr(
        "services.sprint_manager.sprint_branch.ensure_sprint_branch",
        lambda *a, **k: "sprint/sprint-1030",
    )

    captured = {}

    def fake_start_run(sprint_label, tickets, *, repo, repo_root, cwd, config, sprint_branch=None, **kw):
        captured["tickets"] = list(tickets)
        captured["sprint_label"] = sprint_label
        captured["repo"] = repo

        class _Run:
            def to_dict(self):
                return {
                    "run_id": "run-2353",
                    "tickets": list(tickets),
                    "sprint_label": sprint_label,
                }

        return _Run()

    monkeypatch.setattr(dr, "start_run", fake_start_run)
    return routers_sprints, captured


def test_all_true_resolves_open_issues_for_label_only(dispatch_env, monkeypatch):
    """Fixture mirror with two sprint labels — dispatching A never includes B."""
    routers_sprints, captured = dispatch_env

    mirror = [
        _issue(10, ["sprint-1030", "backlog"]),
        _issue(30, ["sprint-1030", "backlog"]),
        _issue(20, ["sprint-1029", "backlog"]),  # other sprint
        _issue(40, ["sprint-1030"], state="closed"),  # closed excluded
        _issue(50, ["sprint-1030.1", "backlog"]),  # child label excluded
    ]

    import github_client

    monkeypatch.setattr(
        github_client,
        "list_issues",
        lambda sprint, repo_name=None: [
            i for i in mirror if any(l["name"] == f"sprint-{sprint}" for l in i["labels"])
        ],
    )

    body = routers_sprints.SprintDispatchBody(tickets=[], all=True, repo="owner/repo")
    result = routers_sprints.dispatch_sprint("sprint-1030", body)

    assert result["tickets"] == [10, 30]  # ascending; no 20, 40, 50
    assert captured["tickets"] == [10, 30]


def test_empty_tickets_same_as_all_true(dispatch_env, monkeypatch):
    routers_sprints, captured = dispatch_env
    import github_client

    monkeypatch.setattr(
        github_client,
        "list_issues",
        lambda sprint, repo_name=None: [
            _issue(7, [f"sprint-{sprint}"]),
            _issue(3, [f"sprint-{sprint}"]),
        ],
    )
    body = routers_sprints.SprintDispatchBody(tickets=[], repo="owner/repo")
    result = routers_sprints.dispatch_sprint("sprint-1030", body)
    assert result["tickets"] == [3, 7]


def test_explicit_tickets_preserve_order(dispatch_env, monkeypatch):
    routers_sprints, captured = dispatch_env
    import github_client

    def _boom(*a, **k):
        raise AssertionError("explicit tickets must not hit the mirror")

    monkeypatch.setattr(github_client, "list_issues", _boom)

    body = routers_sprints.SprintDispatchBody(tickets=[90, 10, 20], repo="owner/repo")
    result = routers_sprints.dispatch_sprint("sprint-1030", body)
    assert result["tickets"] == [90, 10, 20]
    assert captured["tickets"] == [90, 10, 20]


def test_zero_open_tickets_returns_400(dispatch_env, monkeypatch):
    routers_sprints, captured = dispatch_env
    import github_client

    monkeypatch.setattr(github_client, "list_issues", lambda *a, **k: [])
    body = routers_sprints.SprintDispatchBody(all=True, repo="owner/repo")
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        routers_sprints.dispatch_sprint("sprint-1030", body)
    assert ei.value.status_code == 400
    assert "no open tickets" in str(ei.value.detail)
    assert "tickets" not in captured


def test_order_dag_reorders_resolved_set(dispatch_env, monkeypatch):
    routers_sprints, captured = dispatch_env
    import github_client

    monkeypatch.setattr(
        github_client,
        "list_issues",
        lambda sprint, repo_name=None: [
            _issue(1, [f"sprint-{sprint}"]),
            _issue(2, [f"sprint-{sprint}"]),
            _issue(3, [f"sprint-{sprint}"]),
        ],
    )
    monkeypatch.setattr(
        routers_sprints.sprints_service,
        "dag_order_preview",
        lambda label, project: {"new_order": [3, 1, 2]},
    )
    body = routers_sprints.SprintDispatchBody(
        all=True, order="dag", repo="owner/repo"
    )
    result = routers_sprints.dispatch_sprint("sprint-1030", body)
    assert result["tickets"] == [3, 1, 2]


def test_open_issue_numbers_helper_excludes_other_labels_and_closed():
    from services.sprint_manager.ticket_retry import open_issue_numbers_for_label

    class FakeGC:
        def list_issues(self, sprint, repo_name=None):
            # Mirrors github_client.list_issues: already filtered to sprint-{N}.
            return [
                _issue(2, [f"sprint-{sprint}"]),
                _issue(1, [f"sprint-{sprint}"], state="closed"),
            ]

    assert open_issue_numbers_for_label(FakeGC(), "sprint-1030") == [2]


def test_child_label_path_uses_exact_match(monkeypatch):
    """Non-numeric / child labels filter by exact name via list_all_open_issues."""
    from services.sprint_manager.ticket_retry import open_issue_numbers_for_label

    class FakeGC:
        def list_issues(self, sprint, repo_name=None):
            raise AssertionError("child labels must not use int list_issues path")

        def list_all_open_issues(self, repo_name=None, limit=200):
            return [
                _issue(11, ["sprint-1030.1"]),
                _issue(12, ["sprint-1030"]),
                _issue(13, ["sprint-1030.1"]),
            ]

    assert open_issue_numbers_for_label(FakeGC(), "sprint-1030.1") == [11, 13]
