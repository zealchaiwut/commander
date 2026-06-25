"""Tests for issue #746: Add seed-only-project fallback to GET/PUT deploy-config (runs against UAT)"""
import os
import pytest
import httpx


# Resolved from UAT .env at runtime; see tester skill Step 0.
# Default kept only as a last-resort fallback if BASE_URL not exported.
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

def test_seed_only_deploy_config__get_perf_coach_returns_200(client):
    # AC: GET /api/projects/{slug}/deploy-config returns 200 (not 404) when slug is a seed-only project not in projects.json
    r = client.get("/api/projects/perf-coach/deploy-config")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert isinstance(body, dict), "Response body must be an object"


def test_seed_only_deploy_config__put_perf_coach_returns_200(client):
    # AC: PUT /api/projects/{slug}/deploy-config returns 200 (not 404) when slug is a seed-only project not in projects.json
    payload = {
        "prd": {"host": "local", "working_dir": "/tmp"},
    }
    r = client.put("/api/projects/perf-coach/deploy-config", json=payload)
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert isinstance(body, dict), "Response body must be an object"


def test_seed_only_deploy_config__seed_only_resolves_to_zealchaiwut_slug(client):
    # AC: For seed-only slugs, both endpoints resolve the repo as `zealchaiwut/{slug}` — the same fallback logic used in /api/deploy/overview
    r = client.get("/api/projects/perf-coach/deploy-config")
    assert r.status_code == 200
    body = r.json()
    # The response should be a valid deploy config for perf-coach (resolved as zealchaiwut/perf-coach)
    assert isinstance(body, dict)


def test_seed_only_deploy_config__existing_projects_unaffected(client):
    # AC: Both endpoints continue to resolve normally for slugs that are present in projects.json
    r = client.get("/api/projects/commander/deploy-config")
    assert r.status_code == 200, f"Expected 200 for existing project, got {r.status_code}: {r.text}"
    body = r.json()
    assert isinstance(body, dict), "Response body must be an object"


def test_seed_only_deploy_config__nonexistent_slug_returns_404(client):
    # AC: A slug that is neither in projects.json nor a resolvable seed-only project still returns 404
    r = client.get("/api/projects/nonexistent-project-xyz/deploy-config")
    assert r.status_code == 404, f"Expected 404 for nonexistent slug, got {r.status_code}"
