"""Tests for issue #183: Show project name in tester-rejected alerts and banners.

AC-1: _alert_dashboard_banner includes repo in the JSON body posted to /api/alerts.
AC-2: dispatch_alerts accepts an optional repo parameter and forwards it.
AC-3: All dispatch_alerts call sites (TESTER_REJECTED, coder HANG, tester HANG) pass repo.
AC-4: Frontend repoPrefix logic in renderAlertBanners already handles repo correctly.
AC-5: /api/alerts POST persists repo; GET returns it; missing repo returns null gracefully.
AC-6: Smoke test — alert with repo shows correct owner/repo in API response.
"""
import inspect
import re
import httpx
import pytest

BASE_URL = "http://localhost:8000"
SPRINT_MANAGER_PATH = (
    "services/sprint_manager/sprint_manager.py"
)


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


@pytest.fixture(autouse=True)
def _clear_test_alerts(client):
    """Remove any alerts posted by this test run after each test."""
    yield
    # Best-effort cleanup: fetch and delete any alert we may have posted
    try:
        resp = client.get("/api/alerts")
        if resp.status_code == 200:
            # Delete from last to first so indices stay valid
            for idx in reversed(range(len(resp.json()))):
                client.delete(f"/api/alerts/{idx}")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# AC-1 — _alert_dashboard_banner sends repo in JSON payload
# ---------------------------------------------------------------------------

def test_ac1_alert_dashboard_banner_includes_repo_in_source():
    """AC-1: _alert_dashboard_banner constructs the payload with a 'repo' key."""
    import pathlib

    sm_path = pathlib.Path(__file__).parents[3] / SPRINT_MANAGER_PATH
    source = sm_path.read_text(encoding="utf-8")

    # Extract the _alert_dashboard_banner function body
    match = re.search(
        r"def _alert_dashboard_banner\(.*?\)\s*->\s*None:(.*?)(?=\ndef |\Z)",
        source,
        re.DOTALL,
    )
    assert match, "_alert_dashboard_banner function not found in sprint_manager.py"
    fn_body = match.group(0)

    # Must accept a repo parameter
    assert "repo" in fn_body.split(") -> None:")[0], (
        "_alert_dashboard_banner must accept a 'repo' parameter"
    )
    # Must include "repo" key in the payload dict
    assert '"repo"' in fn_body or "'repo'" in fn_body, (
        "_alert_dashboard_banner payload does not contain a 'repo' key"
    )


# ---------------------------------------------------------------------------
# AC-2 — dispatch_alerts accepts optional repo parameter
# ---------------------------------------------------------------------------

def test_ac2_dispatch_alerts_signature_has_repo_param():
    """AC-2: dispatch_alerts accepts repo: Optional[str] = None."""
    import pathlib

    sm_path = pathlib.Path(__file__).parents[3] / SPRINT_MANAGER_PATH
    source = sm_path.read_text(encoding="utf-8")

    # Extract dispatch_alerts function signature block
    match = re.search(
        r"def dispatch_alerts\((.*?)\)\s*->\s*None:",
        source,
        re.DOTALL,
    )
    assert match, "dispatch_alerts function not found in sprint_manager.py"
    sig_block = match.group(1)

    assert "repo" in sig_block, (
        "dispatch_alerts must have a 'repo' parameter"
    )
    # Default must be None
    assert re.search(r"repo\s*:.*?=\s*None", sig_block), (
        "dispatch_alerts 'repo' parameter must default to None"
    )


def test_ac2_dispatch_alerts_forwards_repo_to_banner():
    """AC-2: dispatch_alerts passes repo through to _alert_dashboard_banner."""
    import pathlib

    sm_path = pathlib.Path(__file__).parents[3] / SPRINT_MANAGER_PATH
    source = sm_path.read_text(encoding="utf-8")

    # Extract the dispatch_alerts function body
    match = re.search(
        r"def dispatch_alerts\(.*?\)\s*->\s*None:(.*?)(?=\ndef |\Z)",
        source,
        re.DOTALL,
    )
    assert match, "dispatch_alerts function not found in sprint_manager.py"
    fn_body = match.group(1)

    assert "repo=repo" in fn_body, (
        "dispatch_alerts must forward repo=repo to _alert_dashboard_banner"
    )


# ---------------------------------------------------------------------------
# AC-3 — All call sites pass repo
# ---------------------------------------------------------------------------

def test_ac3_call_sites_pass_repo():
    """AC-3: TESTER_REJECTED, coder HANG, and tester HANG dispatch_alerts calls pass repo."""
    import pathlib

    sm_path = pathlib.Path(__file__).parents[3] / SPRINT_MANAGER_PATH
    source = sm_path.read_text(encoding="utf-8")

    # Find all dispatch_alerts(...) call blocks and verify repo= appears in each
    # We look for each known dispatch site by surrounding context
    tester_rejected_block = re.search(
        r"category=FailureCategory\.TESTER_REJECTED.*?(?=\n\s*\))",
        source,
        re.DOTALL,
    )
    assert tester_rejected_block, "TESTER_REJECTED dispatch_alerts call not found"
    assert "repo=" in source[
        max(0, tester_rejected_block.start() - 200): tester_rejected_block.end() + 50
    ], "TESTER_REJECTED dispatch_alerts call does not pass repo="

    # coder HANG: look for "HANG detected\b" near dispatch_alerts
    coder_hang_match = re.search(
        r'title=f"Issue #\{issue_num\}: HANG detected".*?category=FailureCategory\.HANG.*?(?=\n\s*\))',
        source,
        re.DOTALL,
    )
    assert coder_hang_match, "Coder HANG dispatch_alerts call not found"
    assert "repo=" in source[
        max(0, coder_hang_match.start() - 200): coder_hang_match.end() + 50
    ], "Coder HANG dispatch_alerts call does not pass repo="

    # tester HANG: look for "HANG detected in tester"
    tester_hang_match = re.search(
        r'title=f"Issue #\{issue_num\}: HANG detected in tester".*?category=FailureCategory\.HANG.*?(?=\n\s*\))',
        source,
        re.DOTALL,
    )
    assert tester_hang_match, "Tester HANG dispatch_alerts call not found"
    assert "repo=" in source[
        max(0, tester_hang_match.start() - 200): tester_hang_match.end() + 50
    ], "Tester HANG dispatch_alerts call does not pass repo="


# ---------------------------------------------------------------------------
# AC-4 — Frontend repoPrefix logic handles repo correctly
# ---------------------------------------------------------------------------

def test_ac4_frontend_repo_prefix_logic_exists():
    """AC-4: app.js renderAlertBanners contains repoPrefix logic that uses a.repo."""
    import pathlib

    app_js = pathlib.Path(__file__).parents[1] / "static" / "app.js"
    assert app_js.exists(), f"app.js not found at {app_js}"

    source = app_js.read_text(encoding="utf-8")
    assert "repoPrefix" in source, "repoPrefix not found in app.js"
    assert "a.repo" in source, "a.repo not referenced in app.js repoPrefix logic"

    # Confirm the prefix format: [projectname]
    assert "a.repo.split" in source or ".pop()" in source, (
        "repoPrefix does not extract the project name portion from owner/repo"
    )


# ---------------------------------------------------------------------------
# AC-5 — API stores and returns repo; missing repo returns null gracefully
# ---------------------------------------------------------------------------

def test_ac5_post_alert_with_repo_is_stored_and_returned(client):
    """AC-5: POST /api/alerts with repo persists it; GET returns it."""
    payload = {
        "title": "Issue #42 skipped: TESTER_REJECTED",
        "body": "test body",
        "repo": "zealchaiwut/commander",
    }
    r = client.post("/api/alerts", json=payload)
    assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.text}"

    r2 = client.get("/api/alerts")
    assert r2.status_code == 200
    alerts = r2.json()
    # Find our alert (filter by title since other alerts may exist)
    matching = [
        a for a in alerts
        if a.get("title") == "Issue #42 skipped: TESTER_REJECTED"
        and a.get("repo") == "zealchaiwut/commander"
    ]
    assert matching, (
        "Alert with repo='zealchaiwut/commander' not found in GET /api/alerts response"
    )
    assert matching[0]["repo"] == "zealchaiwut/commander"


def test_ac5_post_alert_without_repo_returns_null_gracefully(client):
    """AC-5: POST /api/alerts without repo returns null/omit for repo gracefully."""
    payload = {"title": "Old alert no repo", "body": "no repo here"}
    r = client.post("/api/alerts", json=payload)
    assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.text}"

    r2 = client.get("/api/alerts")
    assert r2.status_code == 200
    alerts = r2.json()
    matching = [a for a in alerts if a.get("title") == "Old alert no repo"]
    assert matching, "Alert without repo not found in GET /api/alerts"
    # repo should be null or absent — not a string
    repo_val = matching[0].get("repo")
    assert repo_val is None, (
        f"Expected repo to be null for alert without repo field, got {repo_val!r}"
    )


# ---------------------------------------------------------------------------
# AC-6 — Smoke test: alert with repo shows correct owner/repo in API response
# ---------------------------------------------------------------------------

def test_ac6_smoke_alert_banner_repo_field_present_in_api(client):
    """AC-6: Alert posted with repo returns the full owner/repo in GET /api/alerts."""
    payload = {
        "title": "Issue #183 skipped: TESTER_REJECTED",
        "body": "smoke test",
        "repo": "zealchaiwut/commander",
        "issue_num": 183,
        "category": "TESTER_REJECTED",
    }
    r = client.post("/api/alerts", json=payload)
    assert r.status_code == 201

    r2 = client.get("/api/alerts")
    assert r2.status_code == 200
    alerts = r2.json()
    matching = [
        a for a in alerts
        if a.get("issue_num") == 183 and a.get("category") == "TESTER_REJECTED"
    ]
    assert matching, "Smoke test alert not found in GET /api/alerts"
    alert = matching[0]
    assert alert["repo"] == "zealchaiwut/commander", (
        f"Expected repo='zealchaiwut/commander', got {alert['repo']!r}"
    )
    # Verify the project name (last segment) can be extracted as the frontend does
    project_name = alert["repo"].split("/")[-1]
    assert project_name == "commander", (
        f"Extracted project name should be 'commander', got {project_name!r}"
    )
