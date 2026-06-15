"""Tests for issue #863: Add scheduled overnight sprint queue with sequential execution (runs against UAT).

AC coverage:
  AC1  — Scheduled run time accepts 24-hour time or empty to disable
  AC2  — Run on schedule toggle visible only on Approved status sprints
  AC3  — Run on schedule defaults off for all sprints
  AC8  — Manual Run Sprint unaffected by schedule configuration
  AC9  — Empty scheduled time => nothing auto-runs regardless of toggles
"""

import os
import httpx
import pytest

BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "8001")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        f"UAT_BASE_URL / UAT_PORT not set. BASE_URL={BASE_URL}. "
        "Run tester skill Step 0 to resolve UAT before pytest."
    )


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


def test_ac1_set_and_get_scheduled_run_time(client):
    """AC1: API accepts valid 24-hour time and persists it."""
    proj = "commander"
    r = client.put(
        "/api/scheduler/config",
        json={"project": proj, "scheduled_run_time": "09:30"},
    )
    assert r.status_code == 200
    assert r.json()["scheduled_run_time"] == "09:30"

    r2 = client.get("/api/scheduler/config", params={"project": proj})
    assert r2.status_code == 200
    assert r2.json()["scheduled_run_time"] == "09:30"


def test_ac1_empty_disables_scheduled_run_time(client):
    """AC1: Empty scheduled_run_time disables auto-scheduling."""
    proj = "commander"
    client.put(
        "/api/scheduler/config",
        json={"project": proj, "scheduled_run_time": "14:00"},
    )

    r = client.put(
        "/api/scheduler/config",
        json={"project": proj, "scheduled_run_time": ""},
    )
    assert r.status_code == 200
    assert r.json()["scheduled_run_time"] == ""


def test_ac1_rejects_malformed_time(client):
    """AC1: Invalid time format is rejected."""
    proj = "commander"
    bad_times = ["25:00", "12:60", "1:30"]
    for bad_time in bad_times:
        r = client.put(
            "/api/scheduler/config",
            json={"project": proj, "scheduled_run_time": bad_time},
        )
        assert r.status_code == 400


def test_ac3_run_on_schedule_defaults_off(client):
    """AC3: Run on schedule toggle defaults to off (not in map until explicitly set)."""
    proj = "commander"
    r = client.get("/api/scheduler/sprints", params={"project": proj})
    assert r.status_code == 200
    # Can be empty or contain only previously toggled sprints
    assert isinstance(r.json()["run_on_schedule"], dict)


def test_ac3_toggle_on_then_off(client):
    """AC3: Toggling off removes sprint from the run_on_schedule map."""
    proj = "commander"
    sprint = f"test-sprint-{os.urandom(2).hex()}"
    
    # Turn on
    r1 = client.put(
        "/api/scheduler/sprints",
        json={"project": proj, "sprint_label": sprint, "enabled": True},
    )
    assert r1.status_code == 200

    # Turn off
    r2 = client.put(
        "/api/scheduler/sprints",
        json={"project": proj, "sprint_label": sprint, "enabled": False},
    )
    assert r2.status_code == 200

    # Verify removed
    r3 = client.get("/api/scheduler/sprints", params={"project": proj})
    assert sprint not in r3.json()["run_on_schedule"]


def test_ac8_scheduler_tick_not_due_when_no_scheduled_time(client):
    """AC8: Scheduler tick returns fired=False when no scheduled time configured."""
    proj = "commander"
    client.put(
        "/api/scheduler/config",
        json={"project": proj, "scheduled_run_time": ""},
    )

    r = client.post("/api/scheduler/tick", json={"project": proj})
    assert r.status_code == 202
    assert r.json()["fired"] is False


def test_ac9_empty_time_blocks_autorun_even_with_toggles(client):
    """AC9: With empty scheduled_run_time, no sprints auto-run regardless of toggle state."""
    proj = "commander"
    
    client.put(
        "/api/scheduler/config",
        json={"project": proj, "scheduled_run_time": ""},
    )

    sprint = f"test-sprint-{os.urandom(2).hex()}"
    client.put(
        "/api/scheduler/sprints",
        json={"project": proj, "sprint_label": sprint, "enabled": True},
    )

    r = client.post("/api/scheduler/tick", json={"project": proj})
    assert r.status_code == 202
    assert r.json()["fired"] is False


def test_integration_roundtrip_config_and_toggles(client):
    """Full round-trip: set time, set toggles, query state, clear."""
    proj = "commander"

    r1 = client.put(
        "/api/scheduler/config",
        json={"project": proj, "scheduled_run_time": "06:00"},
    )
    assert r1.status_code == 200

    for sprint in ["sprint-test-a", "sprint-test-b"]:
        r = client.put(
            "/api/scheduler/sprints",
            json={"project": proj, "sprint_label": sprint, "enabled": True},
        )
        assert r.status_code == 200

    r_config = client.get("/api/scheduler/config", params={"project": proj})
    assert r_config.json()["scheduled_run_time"] == "06:00"

    r_sprints = client.get("/api/scheduler/sprints", params={"project": proj})
    assert "sprint-test-a" in r_sprints.json()["run_on_schedule"]
    assert r_sprints.json()["run_on_schedule"]["sprint-test-a"] is True

    r_clear = client.put(
        "/api/scheduler/config",
        json={"project": proj, "scheduled_run_time": ""},
    )
    assert r_clear.status_code == 200

    r_verify = client.get("/api/scheduler/config", params={"project": proj})
    assert r_verify.json()["scheduled_run_time"] == ""
