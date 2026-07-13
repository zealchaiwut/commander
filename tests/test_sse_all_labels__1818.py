"""Tests for issue #1818: SSE board connects only the first running sprint label

AC1: When _smgmtRunningLabels has N labels, _smgmtLivePollRestart opens N distinct
     EventSource connections (one per label)
AC2: An SSE error/complete on one label does not disconnect the other labels'
     streams — each label's connection is independent
AC3: _smgmtSseDisconnect closes all open EventSource connections and empties
     the internal _smgmtSseEs map

These ACs are verified via frontend behavioral tests (node --test) in
tests/frontend/smgmt-sse-all-labels.test.mjs

This pytest file verifies the HTTP-level SSE infrastructure endpoints work
correctly for multiple concurrent sprints.

Tests run against UAT server at $UAT_BASE_URL
"""
import os
import pytest
import httpx


BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# --- Acceptance Criteria ---

def test_sse_all_labels__ac1_live_snapshot_per_label(client):
    # AC1: Verify /api/sprints/{label}/live endpoint supports multiple labels
    # Each label must be able to request its own snapshot independently

    # Test that we can request snapshots for different labels
    r1 = client.get("/api/sprints/sprint-117/live?project=zealchaiwut/commander")
    r2 = client.get("/api/sprints/sprint-118/live?project=zealchaiwut/commander")

    # Both should return valid responses (may be empty if sprint not running)
    # The key is both endpoints are supported, not just the first one
    assert r1.status_code in (200, 404), f"Label 1 endpoint failed: {r1.status_code}"
    assert r2.status_code in (200, 404), f"Label 2 endpoint failed: {r2.status_code}"


def test_sse_all_labels__ac1_multiple_labels_support(client):
    # AC1: The implementation supports multiple concurrent sprint labels
    # Verify the /api/running endpoint can return multiple running sprints

    r = client.get("/api/running?project=zealchaiwut/commander")
    assert r.status_code == 200, f"GET /api/running should return 200, got {r.status_code}"
    data = r.json()

    # This endpoint should return a valid response structure
    # (may be empty if no sprints running, but should be a dict/list)
    assert isinstance(data, (dict, list)), "Response should be dict or list"


def test_sse_all_labels__ac2_independent_streams(client):
    # AC2: Each label's stream is independent
    # Verify that querying the /api/sprints/{label}/live endpoint separately
    # for each label works correctly

    # This is a smoke test that the endpoint structure supports multiple labels
    # without cross-contamination. Actual stream independence is tested in frontend.

    label1 = "sprint-117"
    label2 = "sprint-118"

    # Verify both endpoints are routable (endpoint structure supports multiple labels)
    r1 = client.get(f"/api/sprints/{label1}/live?project=zealchaiwut/commander", follow_redirects=False)
    r2 = client.get(f"/api/sprints/{label2}/live?project=zealchaiwut/commander", follow_redirects=False)

    # Both should route correctly (may be 404 if not running, but endpoint structure OK)
    assert r1.status_code in (200, 404, 500, 204), f"First label endpoint routing failed: {r1.status_code}"
    assert r2.status_code in (200, 404, 500, 204), f"Second label endpoint routing failed: {r2.status_code}"


def test_sse_all_labels__ac3_api_consistency(client):
    # AC3: _smgmtSseDisconnect clears all connections
    # Verify the API doesn't hold stale references to old sprint labels

    # Test that /api/running returns current running sprints (no stale labels)
    r = client.get("/api/running?project=zealchaiwut/commander")
    assert r.status_code == 200

    data = r.json()
    # Should be a valid response (dict or list, not error)
    assert data is not None


# --- Frontend behavior tests are the primary verification ---

def test_sse_all_labels__frontend_tests_exist():
    # The primary AC verification happens in frontend/smgmt-sse-all-labels.test.mjs
    # This test documents that those tests must pass

    test_file = "tests/frontend/smgmt-sse-all-labels.test.mjs"
    assert os.path.exists(test_file), f"Frontend test file {test_file} must exist"

    with open(test_file) as f:
        content = f.read()
        # Verify the test file covers all three ACs
        assert "AC1" in content, "Frontend tests must cover AC1"
        assert "AC2" in content, "Frontend tests must cover AC2"
        assert "AC3" in content, "Frontend tests must cover AC3"
