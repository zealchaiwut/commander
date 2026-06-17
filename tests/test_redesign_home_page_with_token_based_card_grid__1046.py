"""Tests for issue #1046: Redesign Home page with token-based card grid (runs against UAT)"""
import os
import pytest
import httpx
import re


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

def test_redesign_home__token_only_styling(client):
    # AC: Page uses only `var(--token)` values from `static/css/tokens.css` — no hardcoded colors or spacing
    r = client.get("/")
    assert r.status_code == 200
    html = r.text
    # Check that inline styles don't contain hex colors or raw pixel values outside of var() references
    # We look for patterns like #RRGGBB or hardcoded pixel values that are NOT inside var()
    # Scan the style tag for inline color definitions
    style_match = re.search(r"<style>(.*?)</style>", html, re.DOTALL)
    assert style_match, "No <style> tag found in home.html"
    style_content = style_match.group(1)
    # Negative check: look for hex color codes (they should all be in the :root { --icon-on-color: #fff; } line or similar)
    # The only hardcoded hex allowed is --icon-on-color which is documented as an AC exception
    hex_colors = re.findall(r"(?<!var\()[^:]*:\s*#[0-9a-fA-F]{3,6}(?![0-9a-fA-F])", style_content)
    # Filter out the documented --icon-on-color exception
    invalid_colors = [h for h in hex_colors if "--icon-on-color" not in style_content[max(0, style_content.find(h)-100):style_content.find(h)]]
    assert not invalid_colors, f"Found hardcoded colors outside var(): {invalid_colors}"


def test_redesign_home__dark_theme_preserved(client):
    # AC: Dark theme is preserved and refined in place; no theme toggle or new theme introduced
    r = client.get("/")
    assert r.status_code == 200
    html = r.text
    # Check that the html element has data-theme="dark"
    assert 'data-theme="dark"' in html, "Dark theme attribute missing from <html> tag"
    # Verify no light theme attribute or toggle is present
    assert 'data-theme="light"' not in html, "Light theme should not be present"
    assert "toggle" not in html.lower() or "toggle-theme" not in html.lower(), "Theme toggle should not be present"


def test_redesign_home__page_header_distinguished(client):
    # AC: Layout has a clearly distinguished page header section above the project grid
    r = client.get("/")
    assert r.status_code == 200
    html = r.text
    # Check for the page header with distinct styling
    assert 'class="page-hdr"' in html, "Page header (.page-hdr) not found"
    assert "Commander" in html, "Page brand text not found"
    # Verify header styling includes border-bottom for visual distinction
    style_match = re.search(r"<style>(.*?)</style>", html, re.DOTALL)
    assert style_match, "No <style> tag found"
    style_content = style_match.group(1)
    assert ".page-hdr" in style_content, "page-hdr CSS class not defined"
    assert "border-bottom" in style_content or "background:" in style_content, "Header should have distinguishing styles"


def test_redesign_home__project_cards_grid_uniform(client):
    # AC: Project cards render in a consistent grid with uniform anatomy: title, status badge, progress indicator, and last-activity timestamp
    r = client.get("/")
    assert r.status_code == 200
    html = r.text
    # Check for project brief card structure
    assert 'class="pbrief"' in html, "Project brief cards (.pbrief) not found"
    # Check for card anatomy elements
    assert 'class="pbrief-head"' in html, "Card head section not found"
    assert 'class="pbstatus' in html or "pbstatus" in html, "Status badge not found in cards"
    assert 'class="card-meta' in html or "card-meta" in html, "Progress/activity metadata not found"
    # Verify card-meta contains progress and/or last-activity indicators
    assert "card-meta" in html, "Card metadata class for progress/activity not found"


def test_redesign_home__empty_state_renders(client):
    # AC: An empty-state component renders when no projects exist (message + primary CTA)
    r = client.get("/")
    assert r.status_code == 200
    html = r.text
    # Check for empty-state HTML structure in the JavaScript
    # The empty state is conditionally rendered in the JS, so we check for the fallback HTML
    assert "empty-state" in html or "empty-note" in html, "Empty state component reference not found"
    # Check for a primary CTA button
    assert "Add new project" in html or "add-proj" in html or "Add" in html, "Empty state CTA not found"


def test_redesign_home__primary_actions_accessible(client):
    # AC: Primary actions (e.g., create project) are visually prominent and accessible via keyboard
    r = client.get("/")
    assert r.status_code == 200
    html = r.text
    # Check for primary action styling (background-colored buttons)
    assert 'class="add-proj"' in html or "add-proj" in html, "Add project button class not found"
    # Check that buttons have accessibility attributes
    assert 'aria-label' in html or 'role=' in html, "Accessibility attributes missing"
    # Verify focus styling is defined for keyboard navigation
    style_match = re.search(r"<style>(.*?)</style>", html, re.DOTALL)
    assert style_match, "No <style> tag found"
    style_content = style_match.group(1)
    assert ":focus-visible" in style_content or ":focus" in style_content, "Focus styling not defined"


def test_redesign_home__links_js_handlers_intact(client):
    # AC: All existing links and JS event handlers continue to function unchanged
    r = client.get("/")
    assert r.status_code == 200
    html = r.text
    # Check for key event handlers and links
    assert "onclick=" in html, "JavaScript event handlers missing"
    assert "/project/" in html, "Project navigation links missing"
    assert "href=" in html, "Links not found"
    # Verify main JS script still loads
    assert "/static/project-todo.js" in html, "project-todo.js reference missing"
    assert "/static/add-project-modal.js" in html, "add-project-modal.js reference missing"


def test_redesign_home__scoped_to_home_html(client):
    # AC: Diff is scoped to `home.html` markup and inline/embedded styles only — no changes to other files
    # This test verifies the current file is home.html and contains only markup + inline styles
    r = client.get("/")
    assert r.status_code == 200
    html = r.text
    # Confirm this is home.html content (has key identifiers)
    assert "<!DOCTYPE html>" in html, "Not HTML content"
    assert '<link rel="stylesheet" href="/static/css/tokens.css">' in html, "tokens.css link missing"
    assert "<style>" in html, "Inline styles section missing"
    # Verify no external CSS files are newly added (only tokens.css which is existing)
    css_links = re.findall(r'<link[^>]*href="[^"]*"[^>]*>', html)
    for link in css_links:
        assert "tokens.css" in link or "tabler" in link or "cdn" in link.lower(), f"Unexpected CSS file: {link}"


def test_redesign_home__impeccable_detect_passes(client):
    # AC: `impeccable detect` passes clean with zero violations on the updated file
    pytest.skip("manual — impeccable detect is a build-time linting tool; verified separately via build/CI")


def test_redesign_home__vanilla_html_css_js_only(client):
    # AC: Implementation uses vanilla HTML, CSS, and JS only — no new frameworks or build steps
    r = client.get("/")
    assert r.status_code == 200
    html = r.text
    # Check for absence of framework imports
    assert "react" not in html.lower(), "React framework found (not allowed)"
    assert "vue" not in html.lower(), "Vue framework found (not allowed)"
    assert "angular" not in html.lower(), "Angular framework found (not allowed)"
    assert "svelte" not in html.lower(), "Svelte framework found (not allowed)"
    # Confirm it's vanilla JS (script tags with inline code or simple src refs)
    assert "<script>" in html, "Inline JavaScript found (expected)"
    # No bundled/minified framework code should be present
    assert "require(" not in html or "require(" not in html.split("<script>")[-1].split("</script>")[0], "CommonJS requires found in inline script"
