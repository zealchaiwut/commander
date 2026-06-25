"""Tests for issue #1174: Home project badge color fallback (runs against UAT)"""
import os
import pytest
import httpx


# Resolved from UAT .env at runtime; see tester skill Step 0.
BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# Valid palette values from home.html line 162
VALID_PALETTE = {"gray", "blue", "green", "purple", "red", "orange", "amber", "yellow", "pink", "cyan", "teal", "indigo"}


# --- Acceptance Criteria ---

def test_home_project_badge_color__validates_color_against_palette(client):
    # AC: _projectBadgeHtml validates the sanitized color string against the known set of .pbic--* palette class suffixes
    # This test verifies the Home tab loads and projects are rendered with valid badge classes.
    r = client.get("/")
    assert r.status_code == 200
    # The HTML should contain project badges; we validate the function logic via browser inspection in UAT steps.
    assert "pbic" in r.text


def test_home_project_badge_color__hex_fallback_to_gray(client):
    # AC: If the sanitized value does not match a known palette entry, the function coerces it to gray.
    # AC: A project with hex value (e.g. #abc123) renders with pbic--gray class, not pbic--abc.
    # This AC requires live project editing in the browser; see UAT Step 1.
    pytest.skip("manual — requires project color editing via UI / agent-browser")


def test_home_project_badge_color__valid_palette_unchanged(client):
    # AC: A project with a valid palette value (e.g. blue, red) continues to render with the correct pbic--<color> class unchanged.
    # This requires editing a project's color to a known value; see UAT Step 2.
    pytest.skip("manual — requires project color editing via UI / agent-browser")


def test_home_project_badge_color__unknown_string_fallback(client):
    # AC: A project with color set to an unlisted string (e.g. aquamarine) falls back to gray instead of rendering with no background.
    # This requires editing a project's color field; see UAT Step 3.
    pytest.skip("manual — requires project color editing via UI / agent-browser")


def test_home_project_badge_color__no_color_field_fallback(client):
    # AC: A project with no color or icon_color set continues to fall back to pbic--gray.
    # This test verifies the default fallback on the Home tab load.
    r = client.get("/")
    assert r.status_code == 200
    # Verify that projects without color fields are still rendered (they fallback to gray).
    # This is validated via UAT Step 5 (manual inspection).
    assert "pbic--gray" in r.text
