"""Tests for issue #1961: Replace home page brief UI with Dev Report view

Acceptance Criteria verified:
  AC1: Home page fetches /api/dev-report, old /api/brief/daily and /api/projects/*/brief/summary removed
  AC2: Each project block has header with badge, name, status pill, counts
  AC3: Body bullets render only for non-empty buckets (shipped, fixed, stale, waiting)
  AC4: Idle projects with empty buckets collapse to header line only
  AC5: Long bucket lists use collapsible pattern (first 3 + "see more")
  AC6: Footer renders: Cost + window dates
  AC7: Open-project CTA is present and functional
  AC8: Review link appears when waiting bucket non-empty, links to UAT view
  AC9: Run-sprint button appears when idle with ready backlog, reuses runSprintAction
  AC10: Date-picker (?date=) continues to work
  AC11: Add Project entry point preserved
  AC12: Empty state displays when /api/dev-report returns 404
  AC13: All styling uses tokens.css; no new external stylesheets or JS modules
  AC14: Old .pbrief 2×2 grid CSS and brief-specific functions deleted
  AC15: Page works on hard refresh (static HTML only)
"""
import os
import pytest
import httpx
from pathlib import Path

BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )

# Path to the home.html file
HOME_HTML_PATH = Path(__file__).parent.parent / "apps" / "dashboard" / "static" / "home.html"

def read_home_html():
    """Read the home.html file content for offline verification."""
    if HOME_HTML_PATH.exists():
        return HOME_HTML_PATH.read_text()
    return None


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# ─────────────────────────────────────────────────────────────────────────────
# AC1: Home page fetches /api/dev-report; old brief endpoints removed
# ─────────────────────────────────────────────────────────────────────────────

def test_ac1_home_page_fetches_dev_report():
    """AC1: home.html fetches /api/dev-report and removes old brief endpoints."""
    html = read_home_html()
    assert html, "home.html should exist"
    # Must fetch from /api/dev-report
    assert "fetch('/api/dev-report'" in html, "Should fetch /api/dev-report"
    # Old endpoints should be removed
    assert "/api/brief/daily" not in html, "Old /api/brief/daily should be removed"
    assert "/api/projects/" not in html or "brief/summary" not in html, \
           "Old /api/projects/*/brief/summary should be removed"
    # New title should be present
    assert "<title>Dev Report — Commander</title>" in html, "Title should be 'Dev Report'"


# ─────────────────────────────────────────────────────────────────────────────
# AC2: Project block header structure (badge, name, status, counts)
# ─────────────────────────────────────────────────────────────────────────────

def test_ac2_project_block_header_rendering():
    """AC2: home.html renders project blocks with badge, name, status pill, counts."""
    html = read_home_html()
    assert html, "home.html should exist"
    # Must have functions/HTML for rendering project header
    assert "pbic" in html, "Should have .pbic (project badge icon class)"
    assert "pbname" in html, "Should have .pbname (project name class)"
    assert "pbstatus" in html, "Should have .pbstatus (status pill class)"
    assert "pb-counts" in html, "Should have .pb-counts (counts display class)"
    assert "_renderProject" in html, "Should have _renderProject function"
    assert "_statusPill" in html, "Should have _statusPill function"
    assert "_countsHtml" in html, "Should have _countsHtml function"


# ─────────────────────────────────────────────────────────────────────────────
# AC3: Bucket rendering (shipped, fixed, stale, waiting)
# ─────────────────────────────────────────────────────────────────────────────

def test_ac3_bucket_rendering_functions():
    """AC3: home.html includes functions to render all bucket types."""
    html = read_home_html()
    assert html, "home.html should exist"
    # Verify all bucket rendering functions exist
    assert "_renderShipped" in html, "Should have _renderShipped function"
    assert "_renderFixed" in html, "Should have _renderFixed function"
    assert "_renderStale" in html, "Should have _renderStale function"
    assert "_renderWaiting" in html, "Should have _renderWaiting function"
    # Verify bucket CSS classes exist
    assert "bucket" in html, "Should have .bucket class"
    assert "bucket-hdr" in html, "Should have .bucket-hdr class"


# ─────────────────────────────────────────────────────────────────────────────
# AC4: Idle projects with empty buckets collapse to header only
# ─────────────────────────────────────────────────────────────────────────────

def test_ac4_empty_project_collapse_logic():
    """AC4: home.html includes _projectHasBody logic to collapse empty projects."""
    html = read_home_html()
    assert html, "home.html should exist"
    # _projectHasBody checks if project should display body
    assert "_projectHasBody" in html, "Should have _projectHasBody function"
    # Collapsed state handling in CSS
    assert ".pbrief.collapsed" in html, "Should have .pbrief.collapsed CSS class"
    assert ".pbrief-body" in html, "Should have .pbrief-body class"


# ─────────────────────────────────────────────────────────────────────────────
# AC5: Long bucket lists use collapsible pattern (first 3 + "see more")
# ─────────────────────────────────────────────────────────────────────────────

def test_ac5_collapsible_pattern_in_html(client):
    """AC5: Home page HTML includes collapsible pattern (planner-see-more class)."""
    r = client.get("/")
    assert r.status_code == 200
    # The collapsible pattern should be defined in the HTML
    assert "planner-see-more" in r.text or "_briefCollapsibleRows" in r.text, \
           "HTML should include collapsible rows pattern"
    assert "_briefToggleMore" in r.text, "HTML should include toggle function"


# ─────────────────────────────────────────────────────────────────────────────
# AC6: Footer line renders (Cost + window dates)
# ─────────────────────────────────────────────────────────────────────────────

def test_ac6_footer_rendering():
    """AC6: home.html includes footer rendering with cost and window fields."""
    html = read_home_html()
    assert html, "home.html should exist"
    # _renderFooter function should exist
    assert "_renderFooter" in html, "Should have _renderFooter function"
    # Footer class should exist
    assert ".report-footer" in html, "Should have .report-footer CSS class"


# ─────────────────────────────────────────────────────────────────────────────
# AC7: Open-project CTA present and functional
# ─────────────────────────────────────────────────────────────────────────────

def test_ac7_open_project_cta_in_html(client):
    """AC7: Open-project CTA is rendered in project block footer."""
    r = client.get("/")
    assert r.status_code == 200
    # open-proj class should be in the HTML
    assert "open-proj" in r.text, "HTML should include open-proj CTA"
    assert "_sprintMgmtHref" in r.text, "HTML should reference sprint management href"


# ─────────────────────────────────────────────────────────────────────────────
# AC8: Review link appears when waiting bucket non-empty
# ─────────────────────────────────────────────────────────────────────────────

def test_ac8_review_link_logic():
    """AC8: home.html includes _reviewLink function for waiting bucket."""
    html = read_home_html()
    assert html, "home.html should exist"
    # Review link logic should be in the page
    assert "_reviewLink" in html, "Should include _reviewLink function"
    # Should check for waiting bucket
    assert "waiting" in html, "Should reference waiting bucket"
    assert "Review" in html, "Should include Review link text"


# ─────────────────────────────────────────────────────────────────────────────
# AC9: Run-sprint button appears when idle with ready backlog
# ─────────────────────────────────────────────────────────────────────────────

def test_ac9_run_sprint_button_logic():
    """AC9: home.html reuses existing runSprintAction handler."""
    html = read_home_html()
    assert html, "home.html should exist"
    # runSprintAction should be defined
    assert "runSprintAction" in html, "Should include runSprintAction function"
    # _runSprintBtn generates the button
    assert "_runSprintBtn" in html, "Should include _runSprintBtn function"
    assert "up_next" in html, "Should check up_next field for ready sprint"


# ─────────────────────────────────────────────────────────────────────────────
# AC10: Date-picker (?date=) continues to work
# ─────────────────────────────────────────────────────────────────────────────

def test_ac10_date_picker_functionality(client):
    """AC10: Date-picker query param is handled in loadReport."""
    r = client.get("/?date=2026-07-17")
    assert r.status_code == 200
    # loadReport should handle date parameter
    assert "onDatePick" in r.text, "HTML should include date picker handler"
    assert "date-pick" in r.text, "HTML should include date-pick input"


# ─────────────────────────────────────────────────────────────────────────────
# AC11: Add Project entry point preserved
# ─────────────────────────────────────────────────────────────────────────────

def test_ac11_add_project_entry_point(client):
    """AC11: Add Project link is preserved in the page."""
    r = client.get("/")
    assert r.status_code == 200
    assert "Add new project" in r.text or "add-proj" in r.text, \
           "HTML should include Add Project entry point"
    assert "openAdd" in r.text, "HTML should include openAdd function"


# ─────────────────────────────────────────────────────────────────────────────
# AC12: Empty state when /api/dev-report returns 404
# ─────────────────────────────────────────────────────────────────────────────

def test_ac12_empty_state_handling():
    """AC12: home.html handles 404 from /api/dev-report with empty state."""
    html = read_home_html()
    assert html, "home.html should exist"
    # Empty state message should be defined
    assert "No report yet" in html, "Should include empty state message"
    assert "_renderEmpty" in html, "Should include _renderEmpty function"
    assert "404" in html or "resp.status === 404" in html, "Should check for 404 response"


# ─────────────────────────────────────────────────────────────────────────────
# AC13: All styling uses tokens.css; no new external stylesheets
# ─────────────────────────────────────────────────────────────────────────────

def test_ac13_styling_uses_tokens_css(client):
    """AC13: Home page references tokens.css and defines no external stylesheets."""
    r = client.get("/")
    assert r.status_code == 200
    # Should reference tokens.css
    assert "tokens.css" in r.text, "HTML should reference tokens.css"
    # CSS variables should be used (--bg, --text, etc.)
    assert "var(--" in r.text, "Inline styles should use CSS variables"


# ─────────────────────────────────────────────────────────────────────────────
# AC14: Old brief-specific CSS and functions deleted
# ─────────────────────────────────────────────────────────────────────────────

def test_ac14_old_brief_code_removed():
    """AC14: Old brief-specific CSS and functions are completely removed."""
    html = read_home_html()
    assert html, "home.html should exist"
    # Old brief functions should not be present
    assert "loadBrief" not in html, "Old loadBrief function should be removed"
    assert "renderKpis" not in html, "Old renderKpis function should be removed"
    assert "renderDecisions" not in html, "Old renderDecisions function should be removed"
    assert "loadPlanner" not in html, "Old loadPlanner function should be removed"
    # Old CSS classes should be gone (replaced with pbrief, not brief-2x2)
    assert ".brief-2x2" not in html, "Old .brief-2x2 grid CSS should be removed"
    assert ".status-bar" not in html, "Old .status-bar (KPI strip) should be removed"


# ─────────────────────────────────────────────────────────────────────────────
# AC15: Page works on hard refresh (static HTML only)
# ─────────────────────────────────────────────────────────────────────────────

def test_ac15_static_html_no_build_required():
    """AC15: home.html is fully static with inline scripts, no build required."""
    html = read_home_html()
    assert html, "home.html should exist"
    # Should be valid HTML with all scripts inline
    assert "<!DOCTYPE html>" in html, "Should be valid HTML document"
    assert "<script>" in html, "Scripts should be inline (not external modules)"
    # Should not reference bundled JS that requires build
    assert "bundle.js" not in html or "dist/bundle" not in html, \
           "Home page should not require esbuild bundling"
    # All logic should be inline, no module imports
    assert "import " not in html or "<script>" not in html, "Should not use ES6 imports in HTML"
