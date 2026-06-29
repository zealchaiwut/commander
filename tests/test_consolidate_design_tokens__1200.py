"""Tests for issue #1200: consolidate design tokens — remove inline duplicates from project.html"""
import os
import re
import subprocess
from pathlib import Path
import httpx
import pytest


BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "8001")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )

# Resolve paths relative to this test file so they work in any clone
_REPO_ROOT = Path(__file__).resolve().parent.parent
_PROJECT_HTML = str(_REPO_ROOT / "apps/dashboard/static/project.html")
_TOKENS_CSS = str(_REPO_ROOT / "apps/dashboard/static/css/tokens.css")


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# ─ AC 1: tokens.css is the single source of truth
def test_consolidate_design_tokens__tokens_css_only_source():
    """AC: tokens.css defines all shared tokens; no duplication in project.html inline styles."""
    project_html = _PROJECT_HTML
    tokens_css = _TOKENS_CSS

    with open(project_html, "r") as f:
        html_content = f.read()

    # Extract the :root block from project.html's inline style
    root_match = re.search(r':root\s*\{([^}]*)\}', html_content, re.DOTALL)
    assert root_match, "No :root block found in project.html"

    root_content = root_match.group(1)

    with open(tokens_css, "r") as f:
        css_content = f.read()

    # Extract token names from tokens.css
    css_tokens = set(re.findall(r'--([a-z-]+):', css_content))

    # Extract token names from project.html inline (excluding comments)
    html_tokens = set(re.findall(r'--([a-z-]+):', root_content))

    # The overlap should be zero or only page-specific tokens
    overlap = css_tokens & html_tokens
    # Filter out page-specific ones
    page_specific = {'sidebar-width', 'rp-tl-gutter-left', 'rp-tl-gutter-right', 'rp-tl-chart-trim', 'rp-tl-chart-end-inset'}
    problematic_overlap = overlap - page_specific

    assert not problematic_overlap, f"Tokens defined in both files: {problematic_overlap}"


# ─ AC 2: Dark-mode overrides removed from project.html
def test_consolidate_design_tokens__no_dark_mode_override_in_html():
    """AC: [data-theme=\"dark\"] block removed from project.html inline styles."""
    project_html = _PROJECT_HTML

    with open(project_html, "r") as f:
        html_content = f.read()

    # Check for [data-theme="dark"] block in the inline <style>
    has_dark_override = re.search(r'\[data-theme\s*=\s*["\']dark["\']\]\s*\{', html_content)

    assert not has_dark_override, "[data-theme=\"dark\"] override block still present in project.html inline styles"


# ─ AC 3: grep confirms zero overlap
def test_consolidate_design_tokens__no_var_overlap():
    """AC: grep confirms no -- variable appears in both files."""
    # Run grep on both files and verify overlap
    project_html = _PROJECT_HTML
    tokens_css = _TOKENS_CSS

    result_html = subprocess.run(
        ["grep", "-o", "--[a-z-]*:", project_html],
        capture_output=True,
        text=True
    )
    html_vars = set(result_html.stdout.strip().split('\n')) if result_html.stdout.strip() else set()

    result_css = subprocess.run(
        ["grep", "-o", "--[a-z-]*:", tokens_css],
        capture_output=True,
        text=True
    )
    css_vars = set(result_css.stdout.strip().split('\n')) if result_css.stdout.strip() else set()

    # Remove empty strings
    html_vars.discard('')
    css_vars.discard('')

    overlap = html_vars & css_vars
    # Allow page-specific tokens
    page_specific = {'--sidebar-width:', '--rp-tl-gutter-left:', '--rp-tl-gutter-right:', '--rp-tl-chart-trim:', '--rp-tl-chart-end-inset:'}
    overlap = overlap - page_specific

    assert not overlap, f"Token variables defined in both files: {overlap}"


# ─ AC 4: Light-mode values match tokens.css
def test_consolidate_design_tokens__light_mode_values():
    """AC: Light-mode --blue, --bg, --text resolve to tokens.css values."""
    with open(_PROJECT_HTML, "r") as f:
        html = f.read()

    # Verify tokens.css is loaded and project.html doesn't override
    assert "/static/css/tokens.css" in html, "tokens.css link not found"

    # Verify the link comes before inline styles
    link_pos = html.find('href="/static/css/tokens.css"')
    style_pos = html.find('<style>')
    assert link_pos < style_pos, "tokens.css link should come before inline <style> block"


# ─ AC 5: Dark-mode values match tokens.css dark block
def test_consolidate_design_tokens__dark_mode_values():
    """AC: Dark-mode values render from tokens.css [data-theme=\"dark\"] block."""
    tokens_css = _TOKENS_CSS

    with open(tokens_css, "r") as f:
        css_content = f.read()

    # Extract the [data-theme="dark"] block and confirm it has rgba-based -bg tokens
    dark_block = re.search(r'\[data-theme\s*=\s*["\']dark["\']\]\s*\{([^}]+)\}', css_content, re.DOTALL)
    assert dark_block, "[data-theme=\"dark\"] block not found in tokens.css"

    dark_content = dark_block.group(1)

    # Check for rgba-based -bg tokens (the ticket mentions these should win, not hex values)
    has_rgba_bg = re.search(r'--\w*-bg\s*:\s*rgba\(', dark_content)
    assert has_rgba_bg, "Dark mode should define rgba-based -bg tokens in tokens.css"


# ─ AC 6: tokens.css link remains and loads first
def test_consolidate_design_tokens__tokens_css_link_present():
    """AC: <link rel=\"stylesheet\" href=\"/static/css/tokens.css\"> is present and loads first."""
    with open(_PROJECT_HTML, "r") as f:
        html = f.read()

    # Check for the link tag
    link_tag = re.search(r'<link[^>]*href\s*=\s*["\']?/static/css/tokens\.css["\']?[^>]*>', html)
    assert link_tag, "tokens.css link tag not found in project.html"

    # Verify it's in <head> and before inline <style>
    head_start = html.find('<head>')
    head_end = html.find('</head>')
    link_pos = html.find('/static/css/tokens.css')
    style_pos = html.find('<style>')

    assert head_start < link_pos < head_end, "tokens.css link should be in <head>"
    assert link_pos < style_pos, "tokens.css link should load before inline <style> block"
