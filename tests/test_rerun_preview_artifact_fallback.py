"""Rerun preview when GitHub sprint label is empty but plan.json lists tickets.

Regression: sprint-15.2 ``process lost`` left tickets on sprint-15.1; History
Re-run → 15.3 modal showed ``No tickets in this sprint``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

import server as srv  # noqa: E402

_FAKE_REPO = "zealchaiwut/vector-search-demo"


def _issue(number: int, *label_names: str, title: str = "") -> dict:
    return {
        "number": number,
        "title": title or f"Issue {number}",
        "labels": [{"name": n} for n in label_names],
    }


@pytest.fixture()
def client():
    return TestClient(srv.app)


class TestRerunPreviewArtifactFallback:
    def test_empty_github_label_uses_plan_json_roster(self, client, tmp_path):
        commander = tmp_path / ".commander" / "sprints"
        commander.mkdir(parents=True)
        (commander / "sprint-15.2-plan.json").write_text(
            json.dumps({"state": "needs_rework", "tickets": [117, 120], "parent": "sprint-15.1"}),
            encoding="utf-8",
        )

        open_issues = [
            _issue(117, "sprint-15.1", "needs-rework", title="CHUNK_OVERLAP"),
            _issue(120, "sprint-15.1", "needs-rework", title="loadRows"),
            _issue(174, "sprint-15", "sprint-summary", title="Executive Summary"),
        ]

        with (
            patch.object(srv, "_project_root_path", return_value=tmp_path),
            patch.object(
                srv.github_client, "list_open_issues_with_body", return_value=open_issues,
            ),
            patch.object(
                srv.github_client, "list_labels",
                return_value=[{"name": "sprint-15.1"}, {"name": "sprint-15.2"}],
            ),
            patch.object(srv, "_next_sprint_sublabel", return_value="sprint-15.3"),
        ):
            resp = client.get(
                "/api/sprints/sprint-15.2/rerun-preview",
                params={"project": _FAKE_REPO},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["suggested_versioned_label"] == "sprint-15.3"
        numbers = [t["number"] for t in body["tickets"]]
        assert numbers == [117, 120]
        assert body["tickets"][0]["category"] == "needs-rework"

    def test_rerun_post_strips_parent_sprint_label(self, client, tmp_path):
        commander = tmp_path / ".commander" / "sprints"
        commander.mkdir(parents=True)
        (commander / "sprint-15.2-plan.json").write_text(
            json.dumps({"state": "failed", "tickets": [117], "parent": "sprint-15.1"}),
            encoding="utf-8",
        )

        open_issues = [_issue(117, "sprint-15.1", "needs-rework")]

        with (
            patch.object(srv, "_project_root_path", return_value=tmp_path),
            patch.object(srv, "_is_sprint_running", return_value=False),
            patch.object(
                srv.github_client, "list_open_issues_with_body", return_value=open_issues,
            ),
            patch.object(
                srv.github_client, "list_labels",
                return_value=[{"name": "sprint-15.1"}, {"name": "sprint-15.2"}],
            ),
            patch.object(srv, "_next_sprint_sublabel", return_value="sprint-15.3"),
            patch.object(srv.github_client, "get_label_color", return_value="0075ca"),
            patch.object(srv.github_client, "create_label", return_value=None),
            patch.object(srv.github_client, "update_labels", return_value=None) as update,
            patch.object(srv, "_await_rerun_relabel", return_value={117}),
            patch.object(srv, "_write_plan_json", return_value=None),
            patch.object(srv, "_emit_dashboard_event", return_value=None),
        ):
            resp = client.post(
                "/api/sprints/sprint-15.2/rerun",
                params={"project": _FAKE_REPO},
                json={"ticket_numbers": [117], "auto_run": False},
            )

        assert resp.status_code == 200
        assert resp.json()["sub_label"] == "sprint-15.3"
        update.assert_called_once()
        _args, kwargs = update.call_args
        assert kwargs["add"] == ["sprint-15.3"]
        assert "sprint-15.1" in kwargs["remove"]
