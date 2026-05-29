"""Tests for issue #306: /live endpoint exposes locked snapshot issues array."""
import sys
import json
import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

# Add dashboard to path for server import
sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "dashboard"))

from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers to build sprint status payload as sprint_manager would send it
# ---------------------------------------------------------------------------

def _issue(
    number: int,
    title: str = "Test ticket",
    status: str = "pending",
    agent_status: str = "queued",
    coder_started_at: str | None = None,
    coder_finished_at: str | None = None,
    tester_started_at: str | None = None,
    tester_finished_at: str | None = None,
) -> dict:
    return {
        "number": number,
        "title": title,
        "status": status,
        "agent_status": agent_status,
        "coder_started_at": coder_started_at,
        "coder_finished_at": coder_finished_at,
        "tester_started_at": tester_started_at,
        "tester_finished_at": tester_finished_at,
        "failure_reason": None,
        "skip_reason": None,
        "tokens_in": 0,
        "tokens_out": 0,
    }


def _sprint_status(issues: list[dict], estimates: dict | None = None) -> dict:
    return {
        "project": "zealchaiwut/commander",
        "sprint_label": "sprint-99",
        "sprint_number": 99,
        "issues": issues,
        "start_timestamp": "2026-01-01T00:00:00Z",
        "total_tokens_in": 0,
        "total_tokens_out": 0,
        "wall_clock_secs": 0.0,
        "token_budget": 0,
        "paused": False,
        **({"estimates": estimates} if estimates else {}),
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path):
    """FastAPI test client with mocked project root and empty filesystem."""
    import server

    # Patch project-root resolution so it points at tmp_path
    with patch("server._project_root_path", return_value=tmp_path), \
         patch("server._commander_dir", return_value=tmp_path / ".commander"), \
         patch("server._find_latest_sprint_log", return_value=None), \
         patch("server._is_sprint_running", return_value=True):
        # Ensure .commander/sprints exists
        (tmp_path / ".commander" / "sprints").mkdir(parents=True)
        yield TestClient(server.app)


def _post_status(client, payload: dict):
    resp = client.post("/api/sprint-status", json=payload)
    assert resp.status_code == 200, resp.text


def _get_live(client, label: str = "sprint-99") -> dict:
    resp = client.get(f"/api/sprints/{label}/live", params={"project": "zealchaiwut/commander"})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# AC-1: issues array is present with correct fields
# ---------------------------------------------------------------------------

class TestAC1IssuesArrayPresent:
    def test_issues_key_present_in_response(self, client):
        _post_status(client, _sprint_status([_issue(100, "My ticket")]))
        data = _get_live(client)
        assert "issues" in data

    def test_all_required_fields_present(self, client):
        _post_status(client, _sprint_status([_issue(100, "My ticket")]))
        data = _get_live(client)
        iss = data["issues"][0]
        assert "number" in iss
        assert "title" in iss
        assert "status" in iss
        assert "agent_status" in iss
        assert "agent" in iss
        assert "elapsed_secs" in iss
        assert "size" in iss

    def test_number_and_title_values(self, client):
        _post_status(client, _sprint_status([_issue(123, "Fix the bug")]))
        data = _get_live(client)
        iss = data["issues"][0]
        assert iss["number"] == 123
        assert iss["title"] == "Fix the bug"

    def test_empty_sprint_returns_empty_issues_array(self, client):
        _post_status(client, _sprint_status([]))
        data = _get_live(client)
        assert data["issues"] == []


# ---------------------------------------------------------------------------
# AC-2: status derivation
# ---------------------------------------------------------------------------

class TestAC2StatusDerivation:
    @pytest.mark.parametrize("agent_status,expected", [
        ("queued",             "pending"),
        ("coder_dispatched",   "in-progress"),
        ("coder_running",      "in-progress"),
        ("coder_done",         "in-progress"),
        ("tester_dispatched",  "in-progress"),
        ("tester_running",     "in-progress"),
        ("tester_done",        "in-progress"),
        ("completed",          "done"),
        ("failed",             "skipped"),
    ])
    def test_status_mapping(self, client, agent_status, expected):
        # Map agent_status to the underlying issue status field
        raw_status = "done" if agent_status == "completed" else (
            "skipped" if agent_status == "failed" else "pending"
        )
        _post_status(client, _sprint_status([
            _issue(1, agent_status=agent_status, status=raw_status)
        ]))
        data = _get_live(client)
        assert data["issues"][0]["status"] == expected


# ---------------------------------------------------------------------------
# AC-3: agent_status normalization
# ---------------------------------------------------------------------------

class TestAC3AgentStatusNormalization:
    @pytest.mark.parametrize("raw,expected", [
        ("coder_running",    "running"),
        ("tester_running",   "running"),
        ("failed",           "failed"),
        ("queued",           None),
        ("coder_dispatched", None),
        ("coder_done",       None),
        ("tester_dispatched",None),
        ("tester_done",      None),
        ("completed",        None),
    ])
    def test_agent_status_values(self, client, raw, expected):
        raw_status = "done" if raw == "completed" else (
            "skipped" if raw == "failed" else "pending"
        )
        _post_status(client, _sprint_status([_issue(1, agent_status=raw, status=raw_status)]))
        data = _get_live(client)
        assert data["issues"][0]["agent_status"] == expected


# ---------------------------------------------------------------------------
# AC-4: agent field
# ---------------------------------------------------------------------------

class TestAC4AgentField:
    @pytest.mark.parametrize("raw,expected", [
        ("coder_dispatched",  "coder"),
        ("coder_running",     "coder"),
        ("tester_dispatched", "tester"),
        ("tester_running",    "tester"),
        ("queued",            None),
        ("coder_done",        None),
        ("tester_done",       None),
        ("completed",         None),
        ("failed",            None),
    ])
    def test_agent_field_values(self, client, raw, expected):
        raw_status = "done" if raw == "completed" else (
            "skipped" if raw == "failed" else "pending"
        )
        _post_status(client, _sprint_status([_issue(1, agent_status=raw, status=raw_status)]))
        data = _get_live(client)
        assert data["issues"][0]["agent"] == expected


# ---------------------------------------------------------------------------
# AC-5: elapsed_secs computation
# ---------------------------------------------------------------------------

class TestAC5ElapsedSecs:
    def test_null_when_no_coder_started(self, client):
        _post_status(client, _sprint_status([_issue(1)]))
        data = _get_live(client)
        assert data["issues"][0]["elapsed_secs"] is None

    def test_elapsed_uses_tester_finished_when_done(self, client):
        t0 = "2026-01-01T10:00:00Z"
        t1 = "2026-01-01T10:30:00Z"
        iss = _issue(1, status="done", agent_status="completed",
                     coder_started_at=t0, tester_finished_at=t1)
        _post_status(client, _sprint_status([iss]))
        data = _get_live(client)
        assert data["issues"][0]["elapsed_secs"] == 1800  # 30 minutes

    def test_elapsed_grows_when_in_flight(self, client):
        # coder started 60s ago, not yet finished
        t0 = (datetime.now(timezone.utc) - timedelta(seconds=60)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        iss = _issue(1, agent_status="coder_running", coder_started_at=t0)
        _post_status(client, _sprint_status([iss]))
        data = _get_live(client)
        elapsed = data["issues"][0]["elapsed_secs"]
        assert elapsed is not None
        assert elapsed >= 60

    def test_elapsed_non_negative(self, client):
        future = (datetime.now(timezone.utc) + timedelta(seconds=5)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        iss = _issue(1, coder_started_at=future)
        _post_status(client, _sprint_status([iss]))
        data = _get_live(client)
        assert data["issues"][0]["elapsed_secs"] == 0


# ---------------------------------------------------------------------------
# AC-6: size from estimates
# ---------------------------------------------------------------------------

class TestAC6SizeFromEstimates:
    def test_size_null_when_no_estimates(self, client):
        _post_status(client, _sprint_status([_issue(1)]))
        data = _get_live(client)
        assert data["issues"][0]["size"] is None

    @pytest.mark.parametrize("size", ["S", "M", "L", "XL"])
    def test_size_passed_through_from_estimates(self, client, size):
        estimates = {"1": {"number": 1, "title": "x", "size": size, "minutes": 10}}
        _post_status(client, _sprint_status([_issue(1)], estimates=estimates))
        data = _get_live(client)
        assert data["issues"][0]["size"] == size

    def test_size_null_for_unestimated_ticket(self, client):
        # Only ticket 2 is estimated; ticket 1 is not
        estimates = {"2": {"number": 2, "title": "y", "size": "L", "minutes": 30}}
        _post_status(client, _sprint_status([_issue(1), _issue(2)], estimates=estimates))
        data = _get_live(client)
        assert data["issues"][0]["size"] is None   # ticket 1
        assert data["issues"][1]["size"] == "L"    # ticket 2

    def test_size_not_derived_from_minutes(self, client):
        # Estimate entry has minutes but no size key → size must be null
        estimates = {"1": {"number": 1, "title": "x", "minutes": 25}}
        _post_status(client, _sprint_status([_issue(1)], estimates=estimates))
        data = _get_live(client)
        assert data["issues"][0]["size"] is None


# ---------------------------------------------------------------------------
# AC-7: snapshot stability (de-labeled ticket still appears)
# ---------------------------------------------------------------------------

class TestAC7SnapshotStability:
    def test_de_labeled_ticket_appears_from_snapshot(self, client):
        """Ticket in the snapshot still shows even if it were removed from live GitHub."""
        _post_status(client, _sprint_status([
            _issue(10, "Ticket A"),
            _issue(11, "Ticket B"),
        ]))
        # Simulate "label removed from ticket 11" — snapshot is unchanged because we
        # source from _sprint_statuses, not a live GitHub query.
        data = _get_live(client)
        numbers = [i["number"] for i in data["issues"]]
        assert 10 in numbers
        assert 11 in numbers


# ---------------------------------------------------------------------------
# AC-8: pre-existing fields remain intact
# ---------------------------------------------------------------------------

class TestAC8ExistingFieldsUnchanged:
    def test_existing_fields_still_present(self, client):
        _post_status(client, _sprint_status([_issue(1)]))
        data = _get_live(client)
        for field in ("time_spent_sec", "started_at", "current_ticket",
                      "active_agent", "recent_log_lines",
                      "done_count", "failed_count", "skipped_count",
                      "pending_count", "total_count", "complete_count",
                      "est_remaining_minutes"):
            assert field in data, f"Missing field: {field}"
