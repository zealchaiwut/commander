"""Tester httpx black-box tests for issue #805 (runs against a live UAT server).

These complement the coder's in-process suite
(``test_805__sprint_history_endpoint.py``), which mounts only the bare router
in an isolated ``FastAPI()`` app. This file instead hits the FULL running
application over HTTP, proving that ``server.py``'s ``include_router`` wiring
actually exposes ``GET /api/sprints/history`` and that the live response carries
the documented shape (AC1, AC2, AC3, AC4/AC6, AC8 — pagination contract).

The server is expected to be running at ``UAT_BASE_URL`` with the #805 code and
a DB seeded with at least one lifecycle ('completed') sprint and one
ledger ('deleted') sprint. See the tester skill Step 0 / Step 5.
"""
import os

import httpx
import pytest

# Resolved from UAT .env at runtime; see tester skill Step 0.
BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )

REQUIRED_SPRINT_KEYS = {
    "lifecycle_state", "duration", "tokens", "estimate_accuracy",
    "pr_number", "summary_path", "issues",
}
REQUIRED_ISSUE_KEYS = {"ticket_id", "state", "time_spent", "pr_number"}
VALID_ISSUE_STATES = {"merged", "closed", "open"}


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# --- Acceptance Criteria (black-box / live-server view) ---

def test_sprint_history__endpoint_exists_with_offset_and_limit(client):
    # AC1: GET /api/sprints/history is reachable on the full app and accepts offset+limit.
    r = client.get("/api/sprints/history", params={"offset": 0, "limit": 5})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["offset"] == 0
    assert body["limit"] == 5
    assert "total" in body and isinstance(body["total"], int)
    assert isinstance(body["sprints"], list)


def test_sprint_history__per_sprint_response_shape(client):
    # AC2: every sprint row carries the full documented shape.
    body = client.get("/api/sprints/history", params={"limit": 20}).json()
    assert body["sprints"], "expected at least one seeded sprint in history"
    for s in body["sprints"]:
        missing = REQUIRED_SPRINT_KEYS - set(s)
        assert not missing, f"sprint {s.get('label')} missing keys: {missing}"
        assert isinstance(s["lifecycle_state"], str) and s["lifecycle_state"]
        assert isinstance(s["issues"], list)


def test_sprint_history__issue_item_shape(client):
    # AC3: each issues[] item has ticket_id/state/time_spent/pr_number, state in vocab.
    body = client.get("/api/sprints/history", params={"limit": 20}).json()
    checked = 0
    for s in body["sprints"]:
        for iss in s["issues"]:
            missing = REQUIRED_ISSUE_KEYS - set(iss)
            assert not missing, f"issue in {s.get('label')} missing keys: {missing}"
            assert iss["state"] in VALID_ISSUE_STATES, f"bad state {iss['state']!r}"
            checked += 1
    assert checked > 0, "expected at least one issue across seeded sprints"


def test_sprint_history__completed_lifecycle_sprint_present(client):
    # AC4/AC6: a 'completed' sprint sourced from the lifecycle table appears.
    body = client.get("/api/sprints/history", params={"limit": 50}).json()
    states = {s["lifecycle_state"] for s in body["sprints"]}
    assert "completed" in states, f"no completed sprint in {states}"


def test_sprint_history__deleted_sprint_is_queryable(client):
    # AC6/AC8: a 'deleted' ledger record is queryable via the live endpoint,
    # carrying its end-reason snapshot shape (ticket list preserved).
    body = client.get("/api/sprints/history", params={"limit": 50}).json()
    deleted = [s for s in body["sprints"] if s["lifecycle_state"] == "deleted"]
    assert deleted, "no deleted sprint surfaced in history feed"
    # The deleted snapshot preserves its captured ticket list.
    assert any(s["issues"] for s in deleted), "deleted sprint lost its ticket snapshot"


def test_sprint_history__limit_bounds_window(client):
    # AC1: limit caps the returned window without changing the reported total.
    full = client.get("/api/sprints/history", params={"limit": 50}).json()
    total = full["total"]
    if total < 2:
        pytest.skip("need >= 2 sprints to exercise limit windowing")
    one = client.get("/api/sprints/history", params={"offset": 0, "limit": 1}).json()
    assert one["total"] == total, "total must be stable across pages"
    assert len(one["sprints"]) == 1, "limit=1 must return exactly one row"


def test_sprint_history__offset_paginates(client):
    # AC1: offset advances the window; page 1 and page 2 differ.
    full = client.get("/api/sprints/history", params={"limit": 50}).json()
    if full["total"] < 2:
        pytest.skip("need >= 2 sprints to exercise offset pagination")
    p0 = client.get("/api/sprints/history", params={"offset": 0, "limit": 1}).json()["sprints"]
    p1 = client.get("/api/sprints/history", params={"offset": 1, "limit": 1}).json()["sprints"]
    assert p0 and p1
    assert p0[0]["label"] != p1[0]["label"], "offset did not advance the window"
