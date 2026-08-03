"""Tests for issue #2058 — sprint label validation at creation/assignment time.

AC1: Sprint creation (and any path that assigns a sprint label to tickets)
     validates the label against the canonical pattern and fails with a clear,
     actionable error naming the expected format.
AC2: The error message states the required format explicitly.
AC3: Startup/reconcile-time warning lists existing non-conforming sprint-* labels.
AC4: Behavioral tests:
     - sprint-viz9003 is rejected with the documented error
     - sprint-121 and sprint-121.2 are accepted
     - The warning path lists a seeded non-conforming label
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")

import github_client  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from routers.sprint_labels import router as sprint_labels_router  # noqa: E402
from routers.board_service import (  # noqa: E402
    SPRINT_LABEL_FORMAT_ERROR,
    find_nonconforming_sprint_labels,
)


def _make_batch_labels_client() -> TestClient:
    app = FastAPI()
    app.include_router(sprint_labels_router)
    return TestClient(app, raise_server_exceptions=False)


# ── AC1 + AC2 + AC4: sprint-viz9003 rejected with documented error ────────────

class TestSprintLabelRejection:
    """sprint-viz9003 must be rejected with the format-explicit error message."""

    def test_viz_label_rejected_in_batch(self):
        """AC1/AC4: batch-labels rejects sprint-viz9003 — no GitHub call made."""
        client = _make_batch_labels_client()
        with patch.object(github_client, "assign_sprint_by_label") as mock_assign:
            resp = client.post("/api/sprints/batch-labels", json={
                "changes": [{"issue_num": 1, "sprint_label": "sprint-viz9003"}],
                "project": "owner/repo",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["failed"] == 1
        assert data["applied"] == 0
        mock_assign.assert_not_called()

    def test_viz_label_error_contains_format_spec(self):
        """AC2: the error message names the expected format explicitly."""
        client = _make_batch_labels_client()
        with patch.object(github_client, "assign_sprint_by_label"):
            resp = client.post("/api/sprints/batch-labels", json={
                "changes": [{"issue_num": 42, "sprint_label": "sprint-viz9003"}],
                "project": "owner/repo",
            })
        data = resp.json()
        assert data["errors"], "errors list must not be empty for rejected label"
        err = data["errors"][0]
        # AC2: error must name the format, specifically the regex or "invisible on the board"
        assert "sprint-" in err and (
            r"\d+" in err or "invisible on the board" in err
        ), f"Error must name the required format; got: {err!r}"

    def test_viz_label_error_cites_invisible_consequence(self):
        """AC2: error warns that free-text labels are invisible on the board."""
        client = _make_batch_labels_client()
        with patch.object(github_client, "assign_sprint_by_label"):
            resp = client.post("/api/sprints/batch-labels", json={
                "changes": [{"issue_num": 1, "sprint_label": "sprint-viz9003"}],
                "project": "owner/repo",
            })
        err = resp.json()["errors"][0]
        assert "not visible on the board" in err or "invisible on the board" in err, (
            f"Error must state the invisibility consequence; got: {err!r}"
        )


# ── AC1 + AC4: sprint-121 and sprint-121.2 are accepted ─────────────────────

class TestSprintLabelAccepted:
    """Canonical labels sprint-121 and sprint-121.2 must pass validation."""

    def test_sprint_121_accepted(self):
        """AC4: sprint-121 passes validation and triggers the GitHub call."""
        client = _make_batch_labels_client()
        with (
            patch.object(github_client, "assign_sprint_by_label", return_value=None),
            patch.object(github_client, "get_repo_for_operation", return_value="owner/repo"),
            patch("routers.board_cache.invalidate_board"),
        ):
            resp = client.post("/api/sprints/batch-labels", json={
                "changes": [{"issue_num": 1, "sprint_label": "sprint-121"}],
                "project": "owner/repo",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["applied"] == 1, f"sprint-121 must be accepted; got: {data}"
        assert data["failed"] == 0

    def test_sprint_121_2_accepted(self):
        """AC4: sprint-121.2 passes validation and triggers the GitHub call."""
        client = _make_batch_labels_client()
        with (
            patch.object(github_client, "assign_sprint_by_label", return_value=None),
            patch.object(github_client, "get_repo_for_operation", return_value="owner/repo"),
            patch("routers.board_cache.invalidate_board"),
        ):
            resp = client.post("/api/sprints/batch-labels", json={
                "changes": [{"issue_num": 2, "sprint_label": "sprint-121.2"}],
                "project": "owner/repo",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["applied"] == 1, f"sprint-121.2 must be accepted; got: {data}"
        assert data["failed"] == 0

    def test_mixed_batch_partial_rejection(self):
        """AC1: in a mixed batch, bad labels fail and good labels succeed."""
        client = _make_batch_labels_client()
        with (
            patch.object(github_client, "assign_sprint_by_label", return_value=None),
            patch.object(github_client, "get_repo_for_operation", return_value="owner/repo"),
            patch("routers.board_cache.invalidate_board"),
        ):
            resp = client.post("/api/sprints/batch-labels", json={
                "changes": [
                    {"issue_num": 1, "sprint_label": "sprint-121"},
                    {"issue_num": 2, "sprint_label": "sprint-viz9003"},
                    {"issue_num": 3, "sprint_label": "sprint-121.2"},
                ],
                "project": "owner/repo",
            })
        data = resp.json()
        assert data["applied"] == 2
        assert data["failed"] == 1


# ── AC3 + AC4: find_nonconforming_sprint_labels — the warning path ────────────

class TestFindNonconformingSprintLabels:
    """AC3/AC4: find_nonconforming_sprint_labels detects pre-existing bad labels."""

    def test_viz_label_detected(self):
        """AC4: seeded sprint-viz9001 is listed by the warning function."""
        result = find_nonconforming_sprint_labels(["sprint-viz9001"])
        assert "sprint-viz9001" in result

    def test_all_viz_labels_detected(self):
        """AC4: sprint-viz9001, sprint-viz9002, sprint-viz9003 are all flagged."""
        result = find_nonconforming_sprint_labels(
            ["sprint-viz9001", "sprint-viz9002", "sprint-viz9003"]
        )
        assert "sprint-viz9001" in result
        assert "sprint-viz9002" in result
        assert "sprint-viz9003" in result

    def test_numeric_labels_not_flagged(self):
        """AC4: canonical labels sprint-121 and sprint-121.2 are NOT flagged."""
        result = find_nonconforming_sprint_labels(["sprint-121", "sprint-121.2"])
        assert "sprint-121" not in result
        assert "sprint-121.2" not in result

    def test_non_sprint_labels_ignored(self):
        """Non-sprint labels (backlog, bug) are not flagged regardless of format."""
        result = find_nonconforming_sprint_labels(["backlog", "bug", "in-progress"])
        assert result == []

    def test_mixed_labels(self):
        """Returns only the non-conforming sprint-* labels from a mixed list."""
        labels = [
            "sprint-121",
            "sprint-viz9003",
            "sprint-121.2",
            "sprint-viz9001",
            "backlog",
            "bug",
            "sprint-9",
        ]
        result = find_nonconforming_sprint_labels(labels)
        assert set(result) == {"sprint-viz9003", "sprint-viz9001"}

    def test_empty_input(self):
        """Empty input returns empty list."""
        assert find_nonconforming_sprint_labels([]) == []

    def test_error_constant_contains_format_spec(self):
        """AC2: SPRINT_LABEL_FORMAT_ERROR names the required format."""
        assert r"\d+" in SPRINT_LABEL_FORMAT_ERROR
        assert "not visible on the board" in SPRINT_LABEL_FORMAT_ERROR or "invisible on the board" in SPRINT_LABEL_FORMAT_ERROR
