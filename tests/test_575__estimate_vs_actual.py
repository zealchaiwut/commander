"""
Unit tests for issue #575: Estimate-vs-Actual Report for Finished Sprints.

Acceptance criteria tested:
- Happy path: finished sprint returns HTTP 200 with per-ticket comparison data.
- Sprint not finished: in-progress sprint (pending issues) returns HTTP 404.
- Sprint not found: non-existent state file returns HTTP 404.
- Tickets with missing estimates: included with estimated_size/delta as null.
"""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Add apps/dashboard to path so we can import server helpers
_DASHBOARD_ROOT = Path(__file__).parent.parent / "apps" / "dashboard"
sys.path.insert(0, str(_DASHBOARD_ROOT))


# ---------------------------------------------------------------------------
# Helpers to build fixture files
# ---------------------------------------------------------------------------

def _make_project(tmp: Path, sprint_label: str, issues: list[dict],
                  plan_state: str = "completed") -> Path:
    """Create .commander directory structure under tmp."""
    commander = tmp / ".commander"
    sprints_dir = commander / "sprints"
    sprints_dir.mkdir(parents=True, exist_ok=True)

    # plan.json
    plan = {"state": plan_state, "tickets": [i["number"] for i in issues]}
    (sprints_dir / f"{sprint_label}-plan.json").write_text(
        json.dumps(plan), encoding="utf-8"
    )

    # sprint-N-state.json
    import re
    m = re.search(r"(\d+)", sprint_label)
    n = m.group(1) if m else sprint_label
    state = {
        "sprint_label": sprint_label,
        "sprint_number": int(n),
        "issues": issues,
    }
    (sprints_dir / f"sprint-{n}-state.json").write_text(
        json.dumps(state), encoding="utf-8"
    )
    return tmp


def _make_estimate(project_root: Path, issue_num: int, size: str) -> None:
    est_dir = project_root / ".commander" / "estimates"
    est_dir.mkdir(parents=True, exist_ok=True)
    est = {"issue_number": issue_num, "size": size}
    (est_dir / f"issue-{issue_num}.json").write_text(json.dumps(est), encoding="utf-8")


_DONE_ISSUE = {
    "number": 101,
    "title": "Add feature X",
    "status": "done",
    "agent_status": None,
    "failure_reason": None,
    "coder_started_at":  "2026-06-01T10:00:00Z",
    "tester_finished_at": "2026-06-01T10:30:00Z",
    "status_changed_at": "2026-06-01T10:30:00Z",
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestHappyPath:
    def test_returns_200_with_ticket_data(self, tmp_path):
        project = _make_project(tmp_path, "sprint-99", [_DONE_ISSUE])
        _make_estimate(project, 101, "M")

        from server import get_sprint_estimate_vs_actual, _project_root_path

        with (
            patch("server._project_root_path", return_value=project),
            patch("server._is_sprint_running", return_value=False),
        ):
            result = get_sprint_estimate_vs_actual("sprint-99", "test/repo")

        assert result["sprint_label"] == "sprint-99"
        tickets = result["tickets"]
        assert len(tickets) == 1
        t = tickets[0]
        assert t["ticket_id"] == 101
        assert t["title"] == "Add feature X"
        assert t["estimated_size"] == "M"
        assert t["estimated_minutes"] == 15
        assert t["actual_elapsed_minutes"] == 30.0
        assert t["delta_minutes"] == 15.0   # 30 actual - 15 estimated
        assert t["status"] == "done"

    def test_elapsed_uses_tester_finished_at(self, tmp_path):
        issue = {**_DONE_ISSUE, "tester_finished_at": "2026-06-01T10:20:00Z"}
        project = _make_project(tmp_path, "sprint-99", [issue])
        _make_estimate(project, 101, "L")

        from server import get_sprint_estimate_vs_actual

        with (
            patch("server._project_root_path", return_value=project),
            patch("server._is_sprint_running", return_value=False),
        ):
            result = get_sprint_estimate_vs_actual("sprint-99", "test/repo")

        t = result["tickets"][0]
        assert t["actual_elapsed_minutes"] == 20.0
        assert t["estimated_minutes"] == 30
        assert t["delta_minutes"] == -10.0

    def test_multiple_tickets(self, tmp_path):
        issue2 = {
            **_DONE_ISSUE,
            "number": 102, "title": "Fix bug Y",
            "coder_started_at": "2026-06-01T11:00:00Z",
            "tester_finished_at": "2026-06-01T11:10:00Z",
        }
        project = _make_project(tmp_path, "sprint-99", [_DONE_ISSUE, issue2])
        _make_estimate(project, 101, "M")
        _make_estimate(project, 102, "S")

        from server import get_sprint_estimate_vs_actual

        with (
            patch("server._project_root_path", return_value=project),
            patch("server._is_sprint_running", return_value=False),
        ):
            result = get_sprint_estimate_vs_actual("sprint-99", "test/repo")

        assert len(result["tickets"]) == 2


class TestNotFinished:
    def test_running_sprint_returns_404(self, tmp_path):
        project = _make_project(tmp_path, "sprint-99", [_DONE_ISSUE], plan_state="running")
        from fastapi import HTTPException
        from server import get_sprint_estimate_vs_actual

        with (
            patch("server._project_root_path", return_value=project),
            patch("server._is_sprint_running", return_value=True),
        ):
            with pytest.raises(HTTPException) as exc_info:
                get_sprint_estimate_vs_actual("sprint-99", "test/repo")
        assert exc_info.value.status_code == 404
        assert "in progress" in exc_info.value.detail.lower()

    def test_planning_state_returns_404(self, tmp_path):
        project = _make_project(tmp_path, "sprint-99", [_DONE_ISSUE], plan_state="planning")
        from fastapi import HTTPException
        from server import get_sprint_estimate_vs_actual

        with (
            patch("server._project_root_path", return_value=project),
            patch("server._is_sprint_running", return_value=False),
        ):
            with pytest.raises(HTTPException) as exc_info:
                get_sprint_estimate_vs_actual("sprint-99", "test/repo")
        assert exc_info.value.status_code == 404
        assert "not finished" in exc_info.value.detail.lower()

    def test_cancelled_returns_404(self, tmp_path):
        project = _make_project(tmp_path, "sprint-99", [_DONE_ISSUE], plan_state="cancelled")
        from fastapi import HTTPException
        from server import get_sprint_estimate_vs_actual

        with (
            patch("server._project_root_path", return_value=project),
            patch("server._is_sprint_running", return_value=False),
        ):
            with pytest.raises(HTTPException) as exc_info:
                get_sprint_estimate_vs_actual("sprint-99", "test/repo")
        assert exc_info.value.status_code == 404
        assert "cancelled" in exc_info.value.detail.lower()

    def test_pending_issue_makes_sprint_not_finished(self, tmp_path):
        pending_issue = {**_DONE_ISSUE, "status": "pending"}
        project = _make_project(tmp_path, "sprint-99", [pending_issue], plan_state="")
        # Remove plan.json so plan_state is empty
        (project / ".commander" / "sprints" / "sprint-99-plan.json").unlink()
        from fastapi import HTTPException
        from server import get_sprint_estimate_vs_actual

        with (
            patch("server._project_root_path", return_value=project),
            patch("server._is_sprint_running", return_value=False),
        ):
            with pytest.raises(HTTPException) as exc_info:
                get_sprint_estimate_vs_actual("sprint-99", "test/repo")
        assert exc_info.value.status_code == 404


class TestNotFound:
    def test_missing_state_file_returns_404(self, tmp_path):
        commander = tmp_path / ".commander" / "sprints"
        commander.mkdir(parents=True, exist_ok=True)
        # Create plan.json with completed state but no state.json
        (commander / "sprint-99-plan.json").write_text(
            json.dumps({"state": "completed", "tickets": []}), encoding="utf-8"
        )
        from fastapi import HTTPException
        from server import get_sprint_estimate_vs_actual

        with (
            patch("server._project_root_path", return_value=tmp_path),
            patch("server._is_sprint_running", return_value=False),
        ):
            with pytest.raises(HTTPException) as exc_info:
                get_sprint_estimate_vs_actual("sprint-99", "test/repo")
        assert exc_info.value.status_code == 404

    def test_invalid_label_returns_400(self, tmp_path):
        from fastapi import HTTPException
        from server import get_sprint_estimate_vs_actual

        with pytest.raises(HTTPException) as exc_info:
            get_sprint_estimate_vs_actual("not-a-sprint", "test/repo")
        assert exc_info.value.status_code == 400


class TestMissingEstimates:
    def test_ticket_without_estimate_has_null_fields(self, tmp_path):
        project = _make_project(tmp_path, "sprint-99", [_DONE_ISSUE])
        # No estimate file created

        from server import get_sprint_estimate_vs_actual

        with (
            patch("server._project_root_path", return_value=project),
            patch("server._is_sprint_running", return_value=False),
        ):
            result = get_sprint_estimate_vs_actual("sprint-99", "test/repo")

        t = result["tickets"][0]
        assert t["estimated_size"] is None
        assert t["estimated_minutes"] is None
        assert t["delta_minutes"] is None
        assert t["actual_elapsed_minutes"] == 30.0  # still computed from timestamps

    def test_ticket_with_missing_timestamps_has_null_elapsed(self, tmp_path):
        issue_no_ts = {**_DONE_ISSUE, "coder_started_at": None, "tester_finished_at": None, "status_changed_at": None}
        project = _make_project(tmp_path, "sprint-99", [issue_no_ts])
        _make_estimate(project, 101, "M")

        from server import get_sprint_estimate_vs_actual

        with (
            patch("server._project_root_path", return_value=project),
            patch("server._is_sprint_running", return_value=False),
        ):
            result = get_sprint_estimate_vs_actual("sprint-99", "test/repo")

        t = result["tickets"][0]
        assert t["actual_elapsed_seconds"] is None
        assert t["actual_elapsed_minutes"] is None
        assert t["delta_minutes"] is None
        assert t["estimated_size"] == "M"
