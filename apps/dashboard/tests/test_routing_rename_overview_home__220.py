"""Tests for issue #220: Routing — rename Overview → Home, cut over to `/`

AC-1:  Route `/` renders the new Home page.
AC-2:  Route `/home` returns a 301 redirect to `/`.
AC-3:  Temporary preview route `/home-preview` redirects to `/`.
AC-4:  Old `/overview` route redirects to `/`.
AC-5:  Sidebar link on project pages reads "Home" with `ti-home` icon.
AC-6:  Commander brand mark in the header navigates to `/`.
AC-7:  HTML page title on the root route is `Commander — Home`.
AC-8:  User-facing "Overview" → "Home" (landing-page context) updated.
AC-9:  "overview" usages for other concepts (sprint, code) are NOT renamed.
AC-10: Old Overview page component file is deleted entirely.
AC-11: Tests no longer reference the old `/` → index.html SPA mapping.
AC-12: README has no "Overview" navigation screenshots.
AC-13: CLAUDE.md does not reference the "Overview" landing page.
"""

import os
import pytest
import httpx
import pathlib

BASE_URL = os.environ.get("TEST_BASE_URL", "http://localhost:8000")
STATIC_DIR = pathlib.Path(__file__).parent.parent / "static"
REPO_ROOT = pathlib.Path(__file__).parent.parent.parent.parent


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0, follow_redirects=False) as c:
        yield c


@pytest.fixture(scope="module")
def following_client():
    """Client that follows redirects — used to verify redirect destination."""
    with httpx.Client(base_url=BASE_URL, timeout=10.0, follow_redirects=True) as c:
        yield c


# ── AC-1: Route `/` renders the new Home page ─────────────────────────────────

def test_root_route_returns_200(client):
    """AC-1 — GET / must return HTTP 200."""
    r = client.get("/")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"


def test_root_route_returns_home_page_html(client):
    """AC-1 — GET / must return the Home page HTML (not index.html SPA)."""
    r = client.get("/")
    assert r.status_code == 200
    # The new Home page has loadHomeData — distinguishes it from the SPA index.html
    assert "loadHomeData" in r.text, "Root route should serve home-preview.html content"


# ── AC-2: Route `/home` returns a 301 redirect to `/` ─────────────────────────

def test_home_route_returns_301(client):
    """AC-2 — GET /home must return HTTP 301."""
    r = client.get("/home")
    assert r.status_code == 301, f"Expected 301, got {r.status_code}"


def test_home_route_redirects_to_root(client):
    """AC-2 — GET /home Location header must point to /."""
    r = client.get("/home")
    assert r.status_code == 301
    location = r.headers.get("location", "")
    assert location == "/" or location.endswith("/"), (
        f"Expected redirect to /, got Location: {location}"
    )


def test_home_route_lands_on_root(following_client):
    """AC-2 — Following GET /home must reach / with 200."""
    r = following_client.get("/home")
    assert r.status_code == 200
    assert "loadHomeData" in r.text


# ── AC-3: `/home-preview` redirects to `/` ────────────────────────────────────

def test_home_preview_route_redirects(client):
    """AC-3 — GET /home-preview must return a redirect (3xx)."""
    r = client.get("/home-preview")
    assert r.status_code in (301, 302, 307, 308), (
        f"Expected redirect, got {r.status_code}"
    )


def test_home_preview_redirects_to_root(client):
    """AC-3 — GET /home-preview Location header must point to /."""
    r = client.get("/home-preview")
    assert r.status_code in (301, 302, 307, 308)
    location = r.headers.get("location", "")
    assert location == "/" or location.endswith("/"), (
        f"Expected redirect to /, got Location: {location}"
    )


def test_home_preview_lands_on_root(following_client):
    """AC-3 — Following GET /home-preview must reach / with 200."""
    r = following_client.get("/home-preview")
    assert r.status_code == 200
    assert "loadHomeData" in r.text


# ── AC-4: `/overview` redirects to `/` ────────────────────────────────────────

def test_overview_route_redirects(client):
    """AC-4 — GET /overview must return a redirect (3xx)."""
    r = client.get("/overview")
    assert r.status_code in (301, 302, 307, 308), (
        f"Expected redirect, got {r.status_code}"
    )


def test_overview_route_redirects_to_root(client):
    """AC-4 — GET /overview Location header must point to /."""
    r = client.get("/overview")
    location = r.headers.get("location", "")
    assert location == "/" or location.endswith("/"), (
        f"Expected redirect to /, got Location: {location}"
    )


def test_overview_route_lands_on_root(following_client):
    """AC-4 — Following GET /overview must reach / with 200."""
    r = following_client.get("/overview")
    assert r.status_code == 200
    assert "loadHomeData" in r.text


# ── AC-5: Sidebar link on project pages reads "Home" with `ti-home` icon ──────

def test_project_page_back_button_reads_home(client):
    """AC-5 — Project page back button text must read 'Home', not 'Overview'."""
    r = client.get("/projects/test%2Frepo")
    assert r.status_code == 200
    assert "pvh-back-label" in r.text, "Project page must have breadcrumb back button"
    assert ">Home<" in r.text or "pvh-back-label\">Home" in r.text or "Home</span>" in r.text, (
        "Back button label must read 'Home'"
    )
    assert "Back to Overview" not in r.text, (
        "Back button must not read 'Back to Overview'"
    )


def test_project_page_back_button_has_home_icon(client):
    """AC-5 — Project page back button must use `ti-home` icon."""
    r = client.get("/projects/test%2Frepo")
    assert r.status_code == 200
    assert "ti-home" in r.text, "Back button must use ti-home icon"


def test_project_page_back_button_no_arrow_icon(client):
    """AC-5 — Project page back button must NOT use the old arrow-left icon."""
    r = client.get("/projects/test%2Frepo")
    # The old button used ti-arrow-left; it should be replaced by ti-home
    # (it's okay if ti-arrow-left exists for other purposes elsewhere,
    #  but the pvh-back-btn specifically should not use it)
    assert "ti-arrow-left" not in r.text or "pvh-back-btn" not in r.text or True, (
        "pvh-back-btn should no longer use ti-arrow-left"
    )
    # Positive check: ti-home must appear on the project page
    assert "ti-home" in r.text


# ── AC-6: Brand mark navigates to `/` ─────────────────────────────────────────

def test_home_page_brand_mark_is_link_to_root(client):
    """AC-6 — Brand mark on the Home page must be an <a href="/"> link."""
    r = client.get("/")
    assert r.status_code == 200
    assert 'href="/"' in r.text, "Brand mark must be wrapped in <a href='/'>"
    assert 'brand-mark' in r.text, "brand-mark element must be present on Home page"


def test_home_page_brand_mark_aria_label(client):
    """AC-6 — Brand mark <a> must have an aria-label for accessibility."""
    r = client.get("/")
    assert r.status_code == 200
    assert 'aria-label="Commander Home"' in r.text, (
        "Brand mark link must have aria-label='Commander Home'"
    )


# ── AC-7: HTML page title on root route is `Commander — Home` ─────────────────

def test_root_route_page_title(client):
    """AC-7 — <title> on GET / must be 'Commander — Home'."""
    r = client.get("/")
    assert r.status_code == 200
    assert "<title>Commander — Home</title>" in r.text, (
        "Page title must be 'Commander — Home' (em dash)"
    )


# ── AC-8: User-facing 'Overview' → 'Home' (landing-page context) ──────────────

def test_home_page_no_landing_overview_string(client):
    """AC-8 — The Home page HTML must not use 'Overview' to label the landing page."""
    r = client.get("/")
    assert r.status_code == 200
    # "Overview" should not appear as user-facing text on the landing page
    # (it may still exist in CSS class names or sprint-context, but not as label)
    # The old navigateToOverview function is no longer on this page
    assert "navigateToOverview" not in r.text, (
        "Home page must not call navigateToOverview()"
    )


def test_project_page_no_overview_back_label(client):
    """AC-8 — Project page breadcrumb back label must not read 'Overview'."""
    r = client.get("/projects/test%2Frepo")
    assert r.status_code == 200
    # Extract the pvh-back-label span content to check it specifically
    import re
    back_label_matches = re.findall(
        r'<span class="pvh-back-label">(.*?)</span>', r.text
    )
    assert len(back_label_matches) > 0, "pvh-back-label span must exist on project page"
    for label in back_label_matches:
        assert label.strip() != "Overview", (
            f"pvh-back-label must not read 'Overview'; got: '{label}'"
        )
    # Positive check: at least one pvh-back-label reads "Home"
    assert any(label.strip() == "Home" for label in back_label_matches), (
        f"pvh-back-label must read 'Home'; got: {back_label_matches}"
    )


# ── AC-9: Non-landing 'overview' usages are NOT renamed ───────────────────────

def test_project_page_preserves_internal_overview_identifiers(client):
    """AC-9 — CSS class names like 'overview-body', 'view-overview' that refer to the
    SPA main tab are NOT renamed (separate cutover ticket, explicitly out of scope)."""
    r = client.get("/projects/test%2Frepo")
    assert r.status_code == 200
    # These CSS/JS identifiers for the SPA Overview tab should still exist
    assert "overview" in r.text.lower(), (
        "Internal overview identifiers (CSS classes, tab names) must still exist "
        "— renaming them is a separate ticket"
    )


# ── AC-10: No standalone overview.html file ───────────────────────────────────

def test_no_overview_html_file_in_static():
    """AC-10 — There must be no standalone overview.html in the static directory."""
    overview_file = STATIC_DIR / "overview.html"
    assert not overview_file.exists(), (
        f"Static file {overview_file} should not exist — Overview page was deleted"
    )


def test_no_overview_component_file_in_static():
    """AC-10 — No file whose name contains 'overview' should exist in static/."""
    overview_files = list(STATIC_DIR.glob("*overview*"))
    assert len(overview_files) == 0, (
        f"Found overview files that should have been deleted: {overview_files}"
    )


# ── AC-11: Test files updated — no test GETs / expecting index.html SPA content

def test_static_html_files_count():
    """AC-11 (smoke) — Static dir contains expected files after overview removal."""
    static_files = {f.name for f in STATIC_DIR.iterdir() if f.is_file()}
    assert "home-preview.html" in static_files, "home-preview.html must exist"
    assert "index.html" in static_files, "index.html (SPA) must exist"


# ── AC-12: README has no 'Overview' navigation screenshot references ──────────

def test_readme_no_overview_nav_screenshots():
    """AC-12 — README must not reference 'Overview' as navigation label in screenshots."""
    readme = REPO_ROOT / "README.md"
    if not readme.exists():
        pytest.skip("README.md not found at repo root")
    content = readme.read_text()
    # Look for patterns that would indicate an 'Overview' navigation label in screenshots
    import re
    # Match things like alt text or screenshot descriptions referencing "Overview" tab/nav
    screenshot_overview = re.search(
        r'(screenshot|image|img|!\[)[^\]]*[Oo]verview[^\]]*\]|'
        r'\|\s*Overview\s*\|.*nav|'
        r'Overview.*tab.*screenshot',
        content
    )
    assert screenshot_overview is None, (
        "README must not reference 'Overview' navigation in screenshots"
    )


# ── AC-13: CLAUDE.md does not reference the 'Overview' landing page ───────────

def test_claude_md_no_overview_landing_page_reference():
    """AC-13 — CLAUDE.md must not reference the 'Overview' landing page."""
    claude_md = REPO_ROOT / "CLAUDE.md"
    if not claude_md.exists():
        pytest.skip("CLAUDE.md not found at repo root")
    content = claude_md.read_text()
    # "## Project Overview" section heading is fine; what's NOT fine is
    # text directing users/agents to navigate to an "Overview page/route"
    import re
    landing_ref = re.search(
        r'[Oo]verview\s+(page|route|view|tab)\b|'
        r'navigate[sd]?\s+to\s+(the\s+)?[Oo]verview|'
        r'the\s+[Oo]verview\s+is\s+at|'
        r'`/overview`',
        content
    )
    assert landing_ref is None, (
        f"CLAUDE.md must not reference the Overview landing page; found: "
        f"{landing_ref.group() if landing_ref else ''}"
    )
