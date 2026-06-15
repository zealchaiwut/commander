"""Regression tests: batch sprint moves and rerun preview match board columns.

When multi-ticket drag-drop posts to /api/sprints/batch-labels, dotted sprint
labels (e.g. sprint-72.2) must be removed — not left as stale duplicates.
Rerun preview must list the same tickets the board shows for that sprint.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

import server as srv  # noqa: E402

_FAKE_REPO = "zealchaiwut/commander"


def _issue(number: int, *label_names: str) -> dict:
    return {
        "number": number,
        "title": f"Issue {number}",
        "labels": [{"name": n} for n in label_names],
    }


class TestBatchLabelsDottedSprints:
    def test_batch_move_uses_assign_sprint_by_label(self):
        client = TestClient(srv.app)
        with patch.object(
            srv.github_client, "assign_sprint_by_label", return_value=None,
        ) as assign:
            resp = client.post(
                "/api/sprints/batch-labels",
                json={
                    "project": _FAKE_REPO,
                    "changes": [
                        {"issue_num": 885, "sprint_label": "sprint-74"},
                        {"issue_num": 881, "sprint_label": "backlog"},
                    ],
                },
            )
        assert resp.status_code == 200
        assert resp.json() == {"applied": 2, "failed": 0, "errors": []}
        assign.assert_any_call(885, "sprint-74", repo_name=_FAKE_REPO)
        assign.assert_any_call(881, None, repo_name=_FAKE_REPO)

    def test_batch_move_accepts_dotted_target_label(self):
        client = TestClient(srv.app)
        with patch.object(
            srv.github_client, "assign_sprint_by_label", return_value=None,
        ) as assign:
            resp = client.post(
                "/api/sprints/batch-labels",
                json={
                    "project": _FAKE_REPO,
                    "changes": [{"issue_num": 1040, "sprint_label": "sprint-72.3"}],
                },
            )
        assert resp.status_code == 200
        assign.assert_called_once_with(1040, "sprint-72.3", repo_name=_FAKE_REPO)


class TestGetSprintIssuesPrimaryLabel:
    def test_excludes_stale_secondary_sprint_label(self):
        issues = [
            _issue(881, "sprint-72.2", "UAT"),
            _issue(885, "sprint-74", "sprint-72.2", "UAT"),
        ]
        with patch.object(
            srv.github_client, "list_open_issues_with_body", return_value=issues,
        ):
            on_722 = srv._get_sprint_issues(_FAKE_REPO, "sprint-72.2")
            on_74 = srv._get_sprint_issues(_FAKE_REPO, "sprint-74")
        assert [i["number"] for i in on_722] == [881]
        assert [i["number"] for i in on_74] == [885]


class TestRerunPreviewBoardAlignment:
    def test_rerun_preview_matches_board_column(self):
        issues = [
            _issue(881, "sprint-72.2", "UAT"),
            _issue(884, "sprint-72.2", "UAT"),
            _issue(885, "sprint-74", "sprint-72.2", "needs-rework"),
            _issue(1038, "sprint-74", "sprint-72.2", "needs-rework"),
        ]
        client = TestClient(srv.app)
        with (
            patch.object(
                srv.github_client, "list_open_issues_with_body", return_value=issues,
            ),
            patch.object(
                srv.github_client, "list_labels", return_value=[{"name": "sprint-72.2"}],
            ),
            patch.object(srv, "_project_root_path", return_value=REPO_ROOT),
            patch.object(srv, "_next_sprint_sublabel", return_value="sprint-72.3"),
        ):
            resp = client.get(
                "/api/sprints/sprint-72.2/rerun-preview",
                params={"project": _FAKE_REPO},
            )
        assert resp.status_code == 200
        numbers = [t["number"] for t in resp.json()["tickets"]]
        assert numbers == [881, 884]
        assert resp.json()["suggested_versioned_label"] == "sprint-72.3"
