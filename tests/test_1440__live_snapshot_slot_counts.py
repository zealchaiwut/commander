"""Tests for issue #1440: multi-lane view inert — live snapshot omits max_coder_slots.

The live snapshot endpoint (`get_sprint_live_snapshot` in
`apps/dashboard/routers/sprint_live.py`) must surface the lane-capacity data the
multi-lane running view needs.  Each test is anchored to one acceptance
criterion from the issue body.

AC1  `get_sprint_live_snapshot` includes `max_coder_slots` in the returned
     payload.
AC2  The snapshot payload also includes active coder slot count and active
     tester slot count.
AC3  `live.max_coder_slots` resolves to the value from the snapshot (not always
     1) when the sprint is running.
AC4  When `max_coder_slots > 1`, the snapshot exposes the data the multi-lane
     view gates on: `max_coder_slots > 1` AND an active-coder count > 1.
AC5  When `max_coder_slots === 1`, the snapshot reports a single slot (the
     two-lane layout continues to render as before — no regression).
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add dashboard to path for server import
sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "dashboard"))

from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers — build a sprint status payload as sprint_manager would POST it
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


def _active_coder(number: int, **kw) -> dict:
    """An issue with a coder actively working (coder_running, not finished)."""
    return _issue(
        number,
        status="in-progress",
        agent_status="coder_running",
        coder_started_at="2026-01-01T00:00:00Z",
        coder_finished_at=None,
        **kw,
    )


def _active_tester(number: int, **kw) -> dict:
    """An issue with a tester actively working (coder done, tester running)."""
    return _issue(
        number,
        status="in-progress",
        agent_status="tester_running",
        coder_started_at="2026-01-01T00:00:00Z",
        coder_finished_at="2026-01-01T00:05:00Z",
        tester_started_at="2026-01-01T00:05:00Z",
        tester_finished_at=None,
        **kw,
    )


def _sprint_status(
    issues: list[dict],
    max_coder_slots: int = 1,
    max_tester_slots: int = 1,
) -> dict:
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
        "max_coder_slots": max_coder_slots,
        "max_tester_slots": max_tester_slots,
    }


@pytest.fixture
def client(tmp_path):
    """FastAPI test client with mocked project root and empty filesystem."""
    import server

    with patch("server._project_root_path", return_value=tmp_path), \
         patch("server._commander_dir", return_value=tmp_path / ".commander"), \
         patch("server._is_sprint_running", return_value=True):
        (tmp_path / ".commander" / "sprints").mkdir(parents=True)
        yield TestClient(server.app)


def _post_status(client, payload: dict):
    resp = client.post("/api/sprint-status", json=payload)
    assert resp.status_code == 200, resp.text


def _get_live(client, label: str = "sprint-99") -> dict:
    resp = client.get(
        f"/api/sprints/{label}/live",
        params={"project": "zealchaiwut/commander"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# AC1: snapshot includes max_coder_slots
# ---------------------------------------------------------------------------

class TestAC1MaxCoderSlotsPresent:
    def test_max_coder_slots_key_present(self, client):
        _post_status(client, _sprint_status([_active_coder(100)], max_coder_slots=2))
        data = _get_live(client)
        assert "max_coder_slots" in data

    def test_max_tester_slots_key_present(self, client):
        _post_status(client, _sprint_status([_active_coder(100)], max_tester_slots=2))
        data = _get_live(client)
        assert "max_tester_slots" in data


# ---------------------------------------------------------------------------
# AC2: snapshot includes active coder + active tester slot counts
# ---------------------------------------------------------------------------

class TestAC2ActiveSlotCounts:
    def test_active_slot_count_keys_present(self, client):
        _post_status(client, _sprint_status([_active_coder(100)], max_coder_slots=2))
        data = _get_live(client)
        assert "active_coder_slots" in data, "payload must expose active coder slot count"
        assert "active_tester_slots" in data, "payload must expose active tester slot count"

    def test_active_coder_count_matches_working_coders(self, client):
        _post_status(client, _sprint_status(
            [_active_coder(100), _active_coder(101), _issue(102)],
            max_coder_slots=3,
        ))
        data = _get_live(client)
        assert data["active_coder_slots"] == 2, (
            "two coders working → active_coder_slots must be 2"
        )

    def test_active_tester_count_matches_working_testers(self, client):
        _post_status(client, _sprint_status(
            [_active_coder(100), _active_tester(101)],
            max_coder_slots=2,
            max_tester_slots=2,
        ))
        data = _get_live(client)
        assert data["active_coder_slots"] == 1
        assert data["active_tester_slots"] == 1, (
            "one tester working → active_tester_slots must be 1"
        )

    def test_no_active_agents_reports_zero(self, client):
        _post_status(client, _sprint_status([_issue(100), _issue(101)], max_coder_slots=2))
        data = _get_live(client)
        assert data["active_coder_slots"] == 0
        assert data["active_tester_slots"] == 0


# ---------------------------------------------------------------------------
# AC3: max_coder_slots resolves to the posted value, not always 1
# ---------------------------------------------------------------------------

class TestAC3ResolvesToSnapshotValue:
    def test_value_reflects_posted_capacity(self, client):
        _post_status(client, _sprint_status([_active_coder(100)], max_coder_slots=3))
        data = _get_live(client)
        assert data["max_coder_slots"] == 3, (
            "max_coder_slots must reflect the running sprint's capacity (3), not default 1"
        )

    def test_tester_value_reflects_posted_capacity(self, client):
        _post_status(client, _sprint_status([_active_coder(100)], max_tester_slots=2))
        data = _get_live(client)
        assert data["max_tester_slots"] == 2


# ---------------------------------------------------------------------------
# AC4: max_coder_slots > 1 exposes the multi-lane gating data
# ---------------------------------------------------------------------------

class TestAC4MultiLaneGate:
    def test_multi_slot_with_multiple_active_coders(self, client):
        """The multi-lane view gates on max_coder_slots > 1 AND >1 active coder.
        The snapshot must expose both signals so the per-worker lanes render."""
        _post_status(client, _sprint_status(
            [_active_coder(100), _active_coder(101)],
            max_coder_slots=2,
        ))
        data = _get_live(client)
        assert data["max_coder_slots"] > 1
        assert data["active_coder_slots"] > 1, (
            "with two coders working, the active-coder signal must exceed 1 so "
            "the multi-lane per-worker view renders instead of the two-lane fallback"
        )


# ---------------------------------------------------------------------------
# AC5: max_coder_slots == 1 keeps the two-lane layout (no regression)
# ---------------------------------------------------------------------------

class TestAC5SingleSlotNoRegression:
    def test_single_slot_reports_one(self, client):
        _post_status(client, _sprint_status([_active_coder(100)], max_coder_slots=1))
        data = _get_live(client)
        assert data["max_coder_slots"] == 1, (
            "single-slot sprint must report max_coder_slots == 1 so the two-lane "
            "layout continues to render"
        )

    def test_absent_capacity_defaults_to_one(self, client):
        """A status payload without slot fields must default to 1 (legacy sprints)."""
        payload = _sprint_status([_active_coder(100)])
        del payload["max_coder_slots"]
        del payload["max_tester_slots"]
        _post_status(client, payload)
        data = _get_live(client)
        assert data["max_coder_slots"] == 1
        assert data["max_tester_slots"] == 1
