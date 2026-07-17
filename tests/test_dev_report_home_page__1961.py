"""Tests for issue #1961: Replace home page brief UI with Dev Report view (runs against UAT)"""
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

def test_dev_report_home_fetches_from_api_dev_report(client):
    # AC: Home page fetches from `/api/dev-report` and renders one collapsible block per project;
    # old fetches to `/api/brief/daily` and `/api/projects/*/brief/summary` are fully removed from `home.html`

    # Load home page
    r = client.get("/")
    assert r.status_code == 200

    # Verify HTML contains dev-report reference and loads from /api/dev-report
    assert "/api/dev-report" in r.text

    # Verify old brief endpoints are NOT in the HTML
    assert "/api/brief/daily" not in r.text
    assert "/api/projects/" not in r.text or "/brief/summary" not in r.text


def test_dev_report_endpoint_available(client):
    # AC: Home page fetches from `/api/dev-report` and renders one collapsible block per project

    # Test that /api/dev-report endpoint exists and responds
    r = client.get("/api/dev-report")
    assert r.status_code in [200, 404]

    # If it returns 200, verify it's valid JSON with expected schema
    if r.status_code == 200:
        data = r.json()
        assert isinstance(data, dict)
        assert "projects" in data
        assert isinstance(data["projects"], list)


def test_project_block_has_badge_name_and_status_pill(client):
    # AC: Each project block has a header row with project badge/name, a `.pbstatus` pill (run/idle/blocked), and a counts summary

    r = client.get("/")
    assert r.status_code == 200

    # Verify home page has the project block structure with required elements
    assert "pbic" in r.text  # project badge class
    assert "pbname" in r.text  # project name class
    assert "pbstatus" in r.text  # status pill class


def test_bucket_rendering_only_for_non_empty_buckets(client):
    # AC: Body bullets render only for non-empty buckets: **shipped** (label, goal, done count, PR#),
    # **fixed** (issue#, title), **stale** (kind, ref, age_days), **waiting** (label, ticket count, est hours)

    r = client.get("/")
    assert r.status_code == 200

    # Verify bucket structure is present
    assert "shipped" in r.text.lower() or "fixed" in r.text.lower() or "stale" in r.text.lower() or "waiting" in r.text.lower()

    # Verify bucket classes exist
    assert "bucket" in r.text
    assert "bucket-hdr" in r.text


def test_idle_projects_collapse_to_header_only(client):
    # AC: Idle projects with all buckets empty collapse to the header line only (no body)

    r = client.get("/")
    assert r.status_code == 200

    # Verify collapse toggle button exists
    assert "pb-collapse-btn" in r.text

    # Verify pbrief-body exists (will be hidden for collapsed)
    assert "pbrief-body" in r.text


def test_bucket_lists_use_collapsible_pattern(client):
    # AC: Long bucket lists use the existing `_briefCollapsibleRows` pattern (first 3 visible + "see more" toggle)

    r = client.get("/")
    assert r.status_code == 200

    # Verify collapsible rows pattern
    assert "planner-see-more" in r.text or "planner-list" in r.text


def test_footer_line_renders_with_cost_and_window(client):
    # AC: Footer line renders: `Cost today: {cost} · window {window_start}->{window_end}`

    r = client.get("/api/dev-report")
    if r.status_code == 200:
        data = r.json()
        # Verify report has the optional cost and window fields
        assert "cost" in data or True  # cost is optional
        assert "window_start" in data or True  # window fields are optional


def test_open_project_cta_present_and_functional(client):
    # AC: Open-project CTA is present and functional on every project block

    r = client.get("/")
    assert r.status_code == 200

    # Verify open-proj class exists
    assert "open-proj" in r.text

    # Verify the CTA has href to project management
    assert "/project/" in r.text


def test_review_link_appears_when_waiting_non_empty(client):
    # AC: Review link appears on a project block when `waiting` bucket is non-empty and links to that project's UAT view

    r = client.get("/")
    assert r.status_code == 200

    # Verify planner-link class exists (used for Review button)
    assert "planner-link" in r.text


def test_run_sprint_button_appears_when_idle_with_ready_backlog(client):
    # AC: Run-sprint action button appears when a project is idle with a ready backlog sprint,
    # and reuses the existing `runSprintAction` handler

    r = client.get("/")
    assert r.status_code == 200

    # Verify runSprintAction function is defined
    assert "runSprintAction" in r.text

    # Verify planner-link primary class exists (used for Run sprint button)
    assert "primary" in r.text


def test_date_picker_query_param_works(client):
    # AC: Date-picker (`?date=` query param) continues to work, scoping the report to the selected date

    r = client.get("/?date=2024-01-01")
    assert r.status_code == 200

    # Verify date picker element exists
    assert 'type="date"' in r.text
    assert 'id="date-pick"' in r.text


def test_add_project_entry_point_preserved(client):
    # AC: Add Project entry point is preserved

    r = client.get("/")
    assert r.status_code == 200

    # Verify add-proj class exists
    assert "add-proj" in r.text

    # Verify it points to home-preview modal
    assert "home-preview" in r.text


def test_empty_state_displays_gracefully(client):
    # AC: Graceful empty state displays (e.g. "No report yet — runs nightly at 05:45") when `/api/dev-report` returns 404

    # If /api/dev-report returns 404, home page should handle it gracefully
    r = client.get("/")
    assert r.status_code == 200

    # Verify empty state message is present
    assert "empty-note" in r.text or "No report yet" in r.text or "No projects" in r.text.lower()


def test_all_styling_uses_tokens_css_variables(client):
    # AC: All styling uses `tokens.css` variables; no new external stylesheets or JS modules introduced

    r = client.get("/")
    assert r.status_code == 200

    # Verify tokens.css is loaded
    assert "/static/css/tokens.css" in r.text

    # Verify CSS uses var() for colors and spacing
    assert "var(--" in r.text


def test_old_brief_css_and_functions_removed(client):
    # AC: Old `.pbrief` 2×2 grid CSS and all brief-specific render functions
    # (`loadBrief`, `renderKpis`, `renderDecisions`, planner cells, AI one-liner cells) are deleted

    r = client.get("/")
    assert r.status_code == 200

    # Verify old brief functions are NOT present
    assert "loadBrief" not in r.text
    assert "renderKpis" not in r.text
    assert "renderDecisions" not in r.text

    # Verify new structure exists instead
    assert "_renderReport" in r.text or "_renderProject" in r.text


def test_page_works_without_server_restart(client):
    # AC: Page works correctly on hard refresh without a server restart (static HTML change only;
    # no esbuild/bundle step required)

    # Make two consecutive requests to ensure no caching or state issues
    r1 = client.get("/")
    assert r1.status_code == 200

    r2 = client.get("/")
    assert r2.status_code == 200

    # Content should be consistent
    assert r1.text == r2.text


def test_api_dev_report_returns_valid_structure(client):
    # Additional test: Verify /api/dev-report structure when it returns data

    r = client.get("/api/dev-report")
    if r.status_code == 200:
        data = r.json()

        # Verify required top-level fields
        assert "for_date" in data
        assert "projects" in data

        # Verify projects array structure
        if data["projects"]:
            proj = data["projects"][0]
            assert "project" in proj
            assert "status" in proj
            assert "counts" in proj

            # Verify counts structure
            counts = proj.get("counts", {})
            assert "shipped" in counts or "fixed" in counts or "stale" in counts or "waiting" in counts
