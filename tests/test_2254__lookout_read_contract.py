"""Contract tests for issue #2254: Lock the lookout read contract.

lookout/gather.py reads Commander over HTTP at exactly three endpoints:

    GET /api/health
    GET /api/projects/{slug}/brief
    GET /api/sprints/history

These are permanent keeps. This test suite locks their response shapes so a
future refactor or endpoint cut does not silently break lookout without a
failing test.

AC coverage:
  AC1 — All three endpoints return HTTP 200 with their current response shape.
  AC2 — lookout is named as the consumer in this comment; see gather.py in the
         lookout repo for the exact fields read from each response.
  AC3 — Optional cross-reference: lookout/docs/hermes-contract.md documents the
         vault file-path contract (not HTTP shapes); the HTTP contract is what
         this file defines. gather.py line-references are inline below.

How these tests FAIL before implementation
------------------------------------------
  If an endpoint is removed or its response shape changes, the corresponding
  assert will fail and make the breakage visible before lookout is deployed.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# ── Path setup ────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest-2254.db")
os.environ.setdefault("COMMANDER_DISABLE_NEON", "1")
os.environ.setdefault("COMMANDER_DISABLE_AUTO_RECONCILE", "1")

# ── Fixture ───────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def client():
    """Shared TestClient against the real server.app (no mocking)."""
    from fastapi.testclient import TestClient
    import server  # noqa: PLC0415
    return TestClient(server.app)


# ── AC1a: GET /api/health ─────────────────────────────────────────────────────
# Consumer: lookout/gather.py line 536
#   health_data, _ = _safe_get(f"{commander_api}/api/health")
#   health = health_data if isinstance(health_data, dict) else {}
# The caller treats the full dict as the health snapshot written to
# manifest.json. At minimum 'status' is the sentinel that synthesize.py reads.


class TestHealthEndpointContract:
    """AC1: GET /api/health returns 200 with the expected response shape.

    Consumer: lookout (gather.py).  If this test fails, lookout will write an
    empty health dict to manifest.json and synthesize.py will lose the status
    signal.
    """

    def test_health_returns_200(self, client):
        """AC1: /api/health responds with HTTP 200."""
        resp = client.get("/api/health")
        assert resp.status_code == 200, (
            f"GET /api/health returned {resp.status_code}; expected 200. "
            f"Body: {resp.text[:400]}"
        )

    def test_health_response_is_json_object(self, client):
        """AC1: /api/health body is a JSON object (dict), not a list or scalar."""
        resp = client.get("/api/health")
        body = resp.json()
        assert isinstance(body, dict), (
            f"Expected a JSON object; got {type(body).__name__}. Body: {body!r:.200}"
        )

    def test_health_has_status_field(self, client):
        """AC1: 'status' key present — synthesize.py reads this for capacity verdict."""
        body = client.get("/api/health").json()
        assert "status" in body, (
            f"'status' missing from /api/health response. Keys: {list(body)}"
        )

    def test_health_status_is_valid_string(self, client):
        """AC1: 'status' value is one of the known health strings."""
        body = client.get("/api/health").json()
        assert isinstance(body["status"], str), (
            f"'status' should be a string; got {type(body['status']).__name__}"
        )
        assert body["status"] in {"healthy", "degraded", "unhealthy"}, (
            f"Unexpected status value: {body['status']!r}. "
            "lookout synthesize.py expects one of healthy/degraded/unhealthy."
        )

    def test_health_has_uptime_seconds(self, client):
        """AC1: 'uptime_seconds' key present (integer) — written to manifest.json."""
        body = client.get("/api/health").json()
        assert "uptime_seconds" in body, (
            f"'uptime_seconds' missing from /api/health. Keys: {list(body)}"
        )
        assert isinstance(body["uptime_seconds"], int), (
            f"'uptime_seconds' should be an int; got {type(body['uptime_seconds']).__name__}"
        )

    def test_health_has_checked_at(self, client):
        """AC1: 'checked_at' key present — ISO-8601 timestamp string."""
        body = client.get("/api/health").json()
        assert "checked_at" in body, (
            f"'checked_at' missing from /api/health. Keys: {list(body)}"
        )
        assert isinstance(body["checked_at"], str), (
            f"'checked_at' should be a string; got {type(body['checked_at']).__name__}"
        )

    def test_health_has_disk_and_sprints_fields(self, client):
        """AC1: operational collector keys are present (may be null on first call)."""
        body = client.get("/api/health").json()
        for key in ("disk", "sprints", "orphan_pids"):
            assert key in body, (
                f"'{key}' missing from /api/health response. Keys: {list(body)}"
            )


# ── AC1b: GET /api/projects/{slug}/brief ─────────────────────────────────────
# Consumer: lookout/gather.py lines 539-540
#   brief_data, brief_err = _safe_get(f"{commander_api}/api/projects/{slug}/brief")
# The response dict is written as-is to vault/.../brief.json under "brief".
# synthesize.py reads the top-level section keys to build the situation card.


class TestProjectBriefEndpointContract:
    """AC1: GET /api/projects/{slug}/brief returns 200 with the ProjectBrief shape.

    Consumer: lookout (gather.py).  If this test fails, brief.json will be
    missing and the situation card synthesizer will have no data to work with.
    """

    _SLUG = "commander"  # always tracked; used in lookout targets.yaml

    def test_brief_returns_200(self, client):
        """AC1: /api/projects/{slug}/brief responds with HTTP 200."""
        resp = client.get(f"/api/projects/{self._SLUG}/brief")
        assert resp.status_code == 200, (
            f"GET /api/projects/{self._SLUG}/brief returned {resp.status_code}; "
            f"expected 200. Body: {resp.text[:400]}"
        )

    def test_brief_response_is_json_object(self, client):
        """AC1: response body is a JSON object."""
        resp = client.get(f"/api/projects/{self._SLUG}/brief")
        body = resp.json()
        assert isinstance(body, dict), (
            f"Expected dict; got {type(body).__name__}. Body: {body!r:.200}"
        )

    def test_brief_has_project_identifier(self, client):
        """AC1: 'project' key present — lookout needs this to identify the source."""
        body = client.get(f"/api/projects/{self._SLUG}/brief").json()
        assert "project" in body, (
            f"'project' missing from brief response. Keys: {list(body)}"
        )

    def test_brief_has_date_field(self, client):
        """AC1: 'date' key present — situation card timestamps derive from this."""
        body = client.get(f"/api/projects/{self._SLUG}/brief").json()
        assert "date" in body, (
            f"'date' missing from brief response. Keys: {list(body)}"
        )
        assert isinstance(body["date"], str), (
            f"'date' should be a string; got {type(body['date']).__name__}"
        )

    def test_brief_has_all_section_keys(self, client):
        """AC1: all ProjectBrief section keys are present (may be empty lists)."""
        body = client.get(f"/api/projects/{self._SLUG}/brief").json()
        required_sections = (
            "shipped",
            "in_progress",
            "up_next",
            "blocked",
            "kpis",
            "recent_activity",
        )
        for key in required_sections:
            assert key in body, (
                f"Section '{key}' missing from /api/projects/{self._SLUG}/brief. "
                f"Keys present: {list(body)}"
            )

    def test_brief_shipped_is_list(self, client):
        """AC1: 'shipped' value is a list — synthesize.py iterates it."""
        body = client.get(f"/api/projects/{self._SLUG}/brief").json()
        assert isinstance(body["shipped"], list), (
            f"'shipped' should be a list; got {type(body['shipped']).__name__}"
        )

    def test_brief_blocked_is_list(self, client):
        """AC1: 'blocked' value is a list."""
        body = client.get(f"/api/projects/{self._SLUG}/brief").json()
        assert isinstance(body["blocked"], list), (
            f"'blocked' should be a list; got {type(body['blocked']).__name__}"
        )

    def test_brief_recent_activity_is_list(self, client):
        """AC1: 'recent_activity' value is a list."""
        body = client.get(f"/api/projects/{self._SLUG}/brief").json()
        assert isinstance(body["recent_activity"], list), (
            f"'recent_activity' should be a list; got {type(body['recent_activity']).__name__}"
        )

    def test_brief_kpis_has_expected_fields(self, client):
        """AC1: 'kpis' object carries the expected ProjectKpis fields."""
        body = client.get(f"/api/projects/{self._SLUG}/brief").json()
        kpis = body.get("kpis")
        # kpis may be null for projects with no activity — shape check when present
        if kpis is not None:
            for key in ("sprints_shipped", "tickets_done", "needs_you"):
                assert key in kpis, (
                    f"kpis.'{key}' missing. kpis keys: {list(kpis)}"
                )


# ── AC1c: GET /api/sprints/history ───────────────────────────────────────────
# Consumer: lookout/gather.py lines 541-548
#   history_data, history_err = _safe_get(f"{commander_api}/api/sprints/history")
#   if isinstance(history_data, list): filtered_history = [...]
# Note: the endpoint returns a SprintHistoryResponse object (dict with
# "sprints", "offset", "limit", "total"), not a bare list.  gather.py's
# list-check falls through to filtered_history=[] in that case, but the
# endpoint must still return 200 so brief_service.py remains the source of
# truth for sprint history.  A non-200 breaks the gather run entirely.


class TestSprintHistoryEndpointContract:
    """AC1: GET /api/sprints/history returns 200 with the SprintHistoryResponse shape.

    Consumer: lookout (gather.py).  If this endpoint returns non-200, gather
    marks sprints_history as 'absent' and the situation card has no sprint data.
    """

    def test_history_returns_200(self, client):
        """AC1: /api/sprints/history responds with HTTP 200."""
        resp = client.get("/api/sprints/history")
        assert resp.status_code == 200, (
            f"GET /api/sprints/history returned {resp.status_code}; expected 200. "
            f"Body: {resp.text[:400]}"
        )

    def test_history_response_is_json_object(self, client):
        """AC1: response body is a JSON object (SprintHistoryResponse)."""
        resp = client.get("/api/sprints/history")
        body = resp.json()
        assert isinstance(body, dict), (
            f"Expected dict; got {type(body).__name__}. Body: {body!r:.200}"
        )

    def test_history_has_sprints_list(self, client):
        """AC1: 'sprints' key present and is a list."""
        body = client.get("/api/sprints/history").json()
        assert "sprints" in body, (
            f"'sprints' missing from /api/sprints/history response. Keys: {list(body)}"
        )
        assert isinstance(body["sprints"], list), (
            f"'sprints' should be a list; got {type(body['sprints']).__name__}"
        )

    def test_history_has_pagination_fields(self, client):
        """AC1: pagination envelope keys present — offset, limit, total."""
        body = client.get("/api/sprints/history").json()
        for key in ("offset", "limit", "total"):
            assert key in body, (
                f"Pagination key '{key}' missing from /api/sprints/history. "
                f"Keys: {list(body)}"
            )

    def test_history_offset_and_limit_are_ints(self, client):
        """AC1: offset and limit are integers (not strings or null)."""
        body = client.get("/api/sprints/history").json()
        assert isinstance(body["offset"], int), (
            f"'offset' should be int; got {type(body['offset']).__name__}"
        )
        assert isinstance(body["limit"], int), (
            f"'limit' should be int; got {type(body['limit']).__name__}"
        )

    def test_history_total_is_non_negative_int(self, client):
        """AC1: 'total' is a non-negative integer."""
        body = client.get("/api/sprints/history").json()
        assert isinstance(body["total"], int), (
            f"'total' should be int; got {type(body['total']).__name__}"
        )
        assert body["total"] >= 0, (
            f"'total' should be >= 0; got {body['total']}"
        )

    def test_history_sprint_items_have_required_fields(self, client):
        """AC1: each sprint item in 'sprints' carries lifecycle_state and issues.

        Checked against the SprintHistoryItem model (routers/sprint_history.py).
        Only exercised when the history is non-empty.
        """
        body = client.get("/api/sprints/history").json()
        sprints = body["sprints"]
        if not sprints:
            pytest.skip("Sprint history is empty — shape check skipped")
        first = sprints[0]
        for key in ("lifecycle_state", "issues"):
            assert key in first, (
                f"SprintHistoryItem missing '{key}'. Keys: {list(first)}"
            )
        assert isinstance(first["issues"], list), (
            f"SprintHistoryItem.issues should be a list; got {type(first['issues']).__name__}"
        )
