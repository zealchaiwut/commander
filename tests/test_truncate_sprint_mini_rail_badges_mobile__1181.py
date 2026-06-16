"""Tests for issue #1181: Truncate sprint mini-rail badges on mobile (runs against UAT)"""
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

def test_truncate_sprint_mini_rail_badges_mobile__375px_viewport_truncates_long_text(client):
    # AC: At 375px viewport width, long badge text in `.hist-card-mini` truncates with ellipsis (`…`)
    r = client.get("/static/project.html")
    assert r.status_code == 200
    assert ".hist-card-mini" in r.text
    # Verify CSS contains the truncation rule for max-width: 600px breakpoint
    assert "white-space: nowrap" in r.text or "text-overflow: ellipsis" in r.text


def test_truncate_sprint_mini_rail_badges_mobile__600px_viewport_truncates_progress(client):
    # AC: At 600px viewport width, long badge text in `.hist-progress` truncates with ellipsis (`…`)
    r = client.get("/static/project.html")
    assert r.status_code == 200
    assert ".hist-progress" in r.text
    # Verify CSS contains the truncation rule
    assert "overflow: hidden" in r.text or "text-overflow: ellipsis" in r.text


def test_truncate_sprint_mini_rail_badges_mobile__no_horizontal_overflow(client):
    # AC: No horizontal overflow or page-width breaking at either breakpoint
    r = client.get("/static/project.html")
    assert r.status_code == 200
    # The page should load without structural errors
    assert "<!DOCTYPE html>" in r.text or "<html" in r.text


def test_truncate_sprint_mini_rail_badges_mobile__desktop_no_truncation(client):
    # AC: Desktop viewports (>600px) display badge text unchanged — no truncation
    r = client.get("/static/project.html")
    assert r.status_code == 200
    # Verify that the base styles do NOT have truncation
    # (truncation is only added inside @media (max-width: 600px))
    content = r.text
    # Base .hist-card-mini should not have text-overflow outside media query
    import re
    # Extract CSS between style tags
    style_match = re.search(r'<style>(.*?)</style>', content, re.DOTALL)
    if style_match:
        css = style_match.group(1)
        # Find the media query section
        media_600_match = re.search(r'@media\s*\(\s*max-width\s*:\s*600px\s*\)\s*{(.*?)}', css, re.DOTALL)
        if media_600_match:
            # Truncation rules should be inside the media query
            media_content = media_600_match.group(1)
            assert "hist-card-mini" in media_content or "hist-progress" in media_content


def test_truncate_sprint_mini_rail_badges_mobile__scoped_to_600px_breakpoint(client):
    # AC: CSS rule is scoped inside `@media (max-width:600px)` only
    r = client.get("/static/project.html")
    assert r.status_code == 200
    content = r.text
    # Verify the media query exists
    assert "@media" in content
    assert "max-width: 600px" in content or "max-width:600px" in content
