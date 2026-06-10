"""Tests for issue #771: Add Start/Stop controls and clarify Deploy vs Start (runs against UAT)

HTTP integration tests for the new environment lifecycle endpoints:
  POST /api/projects/{slug}/environments/{env}/stop
  POST /api/projects/{slug}/environments/{env}/start
  GET  /api/projects/{slug}/environments/{env}/run-state
and the `start_stop_supported` field on /api/deploy/overview.

Safety note: the happy-path Start/Stop on a launchd-backed env would actually
bootout/bootstrap a real service (e.g. com.commander.dashboard). These tests
deliberately exercise only non-mutating paths — validation 400s, the idle
run-state of a label-less env, and the overview data that drives the UI — so
no live service is stopped. Real Start/Stop lifecycle is verified manually.
"""
import os

import pytest
import httpx


# Resolved from UAT .env at runtime; see tester skill Step 0.
BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )

# The commander project's "uat" env is configured host=local with NO launchd_label
# and no stop/start scripts, so stop/start hit validation (400) and run-state
# reads `idle` — all without touching a real service. The "prd" env IS launchd
# backed (com.commander.dashboard); never call stop/start against it here.
SLUG = "commander"
SAFE_LOCAL_ENV = "uat"


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


def _overview_entry(client, project, env):
    r = client.get("/api/deploy/overview")
    assert r.status_code == 200, r.text
    for e in r.json().get("environments", []):
        if e.get("project") == project and e.get("env") == env:
            return e
    return None


# --- Acceptance Criteria ---

def test_start_stop_controls__overview_marks_local_start_stop_supported(client):
    # AC1/AC9: local envs advertise start_stop_supported=true so the UI renders
    # the Start/Stop buttons alongside Deploy/Restart.
    entry = _overview_entry(client, "commander", "uat")
    assert entry is not None, "commander/uat missing from deploy overview"
    assert entry.get("host") == "local"
    assert entry.get("start_stop_supported") is True


def test_start_stop_controls__overview_hides_start_stop_for_render(client):
    # AC10: render envs have no clean stop equivalent -> start_stop_supported=false,
    # which drives the UI to HIDE (not disable) Start/Stop with a tooltip.
    entry = _overview_entry(client, "perf-coach", "prd")
    assert entry is not None, "expected a render env (perf-coach/prd) in overview"
    assert entry.get("host") == "render"
    assert entry.get("start_stop_supported") is False


def test_start_stop_controls__run_state_idle_for_labelless_local(client):
    # AC7: a label-less local env has no reliable probe -> run state reads `idle`.
    r = client.get(f"/api/projects/{SLUG}/environments/{SAFE_LOCAL_ENV}/run-state")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("host") == "local"
    assert body.get("state") == "idle"


def test_start_stop_controls__run_state_state_is_valid_enum(client):
    # AC7: run state is always one of running | stopped | idle.
    r = client.get(f"/api/projects/{SLUG}/environments/{SAFE_LOCAL_ENV}/run-state")
    assert r.status_code == 200, r.text
    assert r.json().get("state") in {"running", "stopped", "idle"}


def test_start_stop_controls__run_state_unknown_env_rejected(client):
    # AC7: an unknown env has no deploy config -> 400, not a 500/crash.
    r = client.get(f"/api/projects/{SLUG}/environments/does-not-exist/run-state")
    assert r.status_code == 400, r.text


def test_start_stop_controls__stop_requires_a_configured_target(client):
    # AC4: Stop validates before running any command; a label-less, script-less
    # env is rejected (400) so no shell command executes.
    r = client.post(f"/api/projects/{SLUG}/environments/{SAFE_LOCAL_ENV}/stop")
    assert r.status_code == 400, r.text
    assert "stop" in r.json().get("detail", "").lower()


def test_start_stop_controls__start_requires_a_configured_target(client):
    # AC3: Start validates before running any command; a label-less, script-less
    # env is rejected (400) so no shell command executes.
    r = client.post(f"/api/projects/{SLUG}/environments/{SAFE_LOCAL_ENV}/start")
    assert r.status_code == 400, r.text
    assert "start" in r.json().get("detail", "").lower()


def test_start_stop_controls__start_brings_up_real_service():
    # AC3/AC5: Start on a running launchd env (state transition + button enable/
    # disable) would bootstrap a real service — cannot be HTTP-tested safely.
    pytest.skip("manual — cannot be HTTP-tested without bouncing a live service")


def test_start_stop_controls__stop_halts_real_service():
    # AC4/AC5: Stop on a running launchd env would bootout a live service.
    pytest.skip("manual — cannot be HTTP-tested without bouncing a live service")


def test_start_stop_controls__no_regression_overview_keeps_existing_fields(client):
    # AC11: existing Deploy/Restart card data (branch, port for local) is still
    # present alongside the new start_stop_supported field.
    entry = _overview_entry(client, "commander", "uat")
    assert entry is not None
    assert "branch" in entry
    assert "port" in entry
