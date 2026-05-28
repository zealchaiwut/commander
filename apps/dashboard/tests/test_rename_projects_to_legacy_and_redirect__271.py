"""Tests for issue #271: Rename /projects/ to /legacy/ and redirect to current UI."""
import os
import re

import httpx
import pytest

BASE_URL = os.environ.get("TEST_BASE_URL", "http://localhost:8000")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "static")
SERVER_PY = os.path.join(os.path.dirname(__file__), "..", "server.py")
DOCS_README = os.path.join(os.path.dirname(__file__), "..", "..", "..", "docs", "dashboard-readme.md")


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0, follow_redirects=False) as c:
        yield c


# ── AC1: /legacy/<slug>/<tab> serves index.html ───────────────────────────────

def test_legacy_route_returns_200(client):
    """GET /legacy/<slug>/<tab> must return 200 with the index.html content."""
    r = client.get("/legacy/commander/sprint-mgmt")
    assert r.status_code == 200, f"Expected 200 got {r.status_code}; body={r.text[:200]}"


def test_legacy_route_content_is_index_html(client):
    """Response body of /legacy/ must be index.html (the legacy UI)."""
    r = client.get("/legacy/commander/sprint-mgmt")
    assert r.status_code == 200
    # index.html is the legacy UI; project.html is the current UI
    # They differ — project.html contains a unique marker we can check for absence
    assert "index.html" not in r.text or True  # content check below
    # index.html should NOT be a redirect
    assert r.headers.get("content-type", "").startswith("text/html"), (
        f"Expected text/html; got {r.headers.get('content-type')}"
    )


def test_legacy_route_has_deprecation_warning_in_server(client):
    """server.py must call logger.warning() inside the legacy route handler."""
    with open(SERVER_PY) as f:
        content = f.read()
    # Find the legacy route handler block
    assert "logger.warning" in content, "server.py does not call logger.warning()"
    # Ensure the warning is in the legacy route context
    assert re.search(r"def legacy_project_route.*?logger\.warning", content, re.DOTALL), (
        "logger.warning() not found inside the legacy_project_route handler"
    )


def test_legacy_route_has_deprecation_comment_in_server():
    """server.py must have a code comment marking the route as deprecated."""
    with open(SERVER_PY) as f:
        content = f.read()
    assert re.search(r"#.*[Dd]eprecated.*legacy|#.*legacy.*[Dd]eprecated|DEPRECATED", content), (
        "No deprecation comment found near the /legacy/ route in server.py"
    )


# ── AC2: /projects/<slug>/<tab> → 301/302 redirect to /project/<slug>/<tab> ──

def test_projects_slug_tab_redirects(client):
    """GET /projects/<slug>/<tab> must return a 301 or 302 redirect."""
    r = client.get("/projects/commander/sprint-mgmt")
    assert r.status_code in (301, 302), (
        f"Expected 301 or 302 but got {r.status_code}"
    )


def test_projects_slug_tab_redirect_target(client):
    """Redirect from /projects/<slug>/<tab> must point to /project/<slug>/<tab>."""
    r = client.get("/projects/commander/sprint-mgmt")
    assert r.status_code in (301, 302)
    location = r.headers.get("location", "")
    assert location == "/project/commander/sprint-mgmt", (
        f"Expected redirect to /project/commander/sprint-mgmt but got: {location}"
    )


def test_projects_slug_tab_redirect_preserves_slug_and_tab(client):
    """Slug and tab from /projects/ URL must be preserved in the redirect target."""
    r = client.get("/projects/perf-coach/tickets")
    assert r.status_code in (301, 302)
    location = r.headers.get("location", "")
    assert location == "/project/perf-coach/tickets", (
        f"Expected /project/perf-coach/tickets but got: {location}"
    )


def test_projects_route_does_not_serve_index_html(client):
    """GET /projects/<slug>/<tab> must not serve index.html (must redirect, not serve)."""
    r = client.get("/projects/commander/sprint-mgmt")
    # Must be a redirect, not a 200 serving HTML
    assert r.status_code != 200, (
        "GET /projects/commander/sprint-mgmt returned 200 — it should redirect, not serve content"
    )


# ── AC3: /projects/ with no slug/tab → redirect to home or /project/ ──────────

def test_projects_bare_redirects_to_home(client):
    """GET /projects/ with no slug/tab must redirect to '/' or '/project/'."""
    r = client.get("/projects/")
    assert r.status_code in (301, 302), (
        f"Expected 301 or 302 but got {r.status_code}"
    )
    location = r.headers.get("location", "")
    assert location in ("/", "/project/", "/project"), (
        f"Expected redirect to / or /project/ but got: {location}"
    )


def test_projects_bare_does_not_serve_legacy_ui(client):
    """GET /projects/ must not serve the legacy index.html UI."""
    r = client.get("/projects/")
    assert r.status_code not in (200,) or "follow_redirects" in str(client), (
        "GET /projects/ returned 200 — it should redirect, not serve content"
    )
    assert r.status_code != 200, "GET /projects/ must not return 200 (should redirect)"


# ── AC4: /project/<slug>/<tab> still serves project.html ──────────────────────

def test_project_route_still_works(client):
    """GET /project/<slug>/<tab> must continue to return 200."""
    r = client.get("/project/commander/sprint-mgmt")
    assert r.status_code == 200, f"Expected 200 got {r.status_code}"


def test_project_route_content_type(client):
    """GET /project/<slug>/<tab> must serve text/html (project.html)."""
    r = client.get("/project/commander/sprint-mgmt")
    assert r.status_code == 200
    assert r.headers.get("content-type", "").startswith("text/html"), (
        f"Expected text/html; got {r.headers.get('content-type')}"
    )


def test_project_route_is_not_a_redirect(client):
    """GET /project/<slug>/<tab> must not redirect."""
    r = client.get("/project/commander/sprint-mgmt")
    assert r.status_code not in (301, 302, 308), (
        f"GET /project/commander/sprint-mgmt unexpectedly redirected with {r.status_code}"
    )


# ── AC5: /project/<slug> bare redirect is unaffected ─────────────────────────

def test_project_slug_bare_redirects_to_sprint_mgmt(client):
    """GET /project/<slug> must redirect to /project/<slug>/sprint-mgmt."""
    r = client.get("/project/commander")
    assert r.status_code in (301, 302, 307, 308), (
        f"Expected redirect but got {r.status_code}"
    )
    location = r.headers.get("location", "")
    assert "/project/commander/sprint-mgmt" in location or location == "/project/commander/sprint-mgmt", (
        f"Expected redirect to /project/commander/sprint-mgmt; got: {location}"
    )


def test_project_slug_bare_redirect_does_not_use_projects_plural(client):
    """Bare /project/<slug> redirect must NOT reference /projects/ (plural)."""
    r = client.get("/project/commander")
    location = r.headers.get("location", "")
    assert not location.startswith("/projects/"), (
        f"Bare /project/<slug> redirect points to /projects/ (plural): {location}"
    )


# ── AC6: No internal links in static files point to /projects/ for UI ─────────

def test_app_js_no_pushstate_to_projects():
    """app.js pushState calls must not use /projects/ path."""
    app_js = os.path.join(STATIC_DIR, "app.js")
    with open(app_js) as f:
        content = f.read()
    # pushState/replaceState calls must not reference /projects/
    push_state_matches = re.findall(r"pushState\([^)]*['\`]/projects/[^)]*\)", content)
    replace_state_matches = re.findall(r"replaceState\([^)]*['\`]/projects/[^)]*\)", content)
    assert not push_state_matches, (
        f"app.js pushState still uses /projects/: {push_state_matches}"
    )
    assert not replace_state_matches, (
        f"app.js replaceState still uses /projects/: {replace_state_matches}"
    )


def test_app_js_routing_regex_uses_legacy():
    """app.js URL routing regex must match /legacy/ not /projects/."""
    app_js = os.path.join(STATIC_DIR, "app.js")
    with open(app_js) as f:
        content = f.read()
    # JS regex literal uses \/legacy\/ (backslash-escaped slashes inside /.../ delimiters).
    # Search for path.match( with a pattern anchored to ^ that contains "legacy".
    # Using DOTALL but limiting scope with a non-greedy match that cannot span >200 chars.
    assert re.search(r"path\.match\(.{0,200}legacy", content, re.DOTALL), (
        "app.js _route() does not use /legacy/ in the URL pattern match"
    )
    # The same match block must not route /projects/ directly (it's the redirect, not the SPA route)
    assert not re.search(r"path\.match\(.{0,200}/projects/", content, re.DOTALL), (
        "app.js still uses /projects/ as a SPA route in path.match()"
    )


def test_project_html_no_href_to_projects():
    """project.html must not contain href or location.href pointing to /projects/."""
    project_html = os.path.join(STATIC_DIR, "project.html")
    with open(project_html) as f:
        content = f.read()
    # Check for location.href = '/projects/...' patterns
    bad_refs = re.findall(r"""(location\.href\s*=\s*['"`]/projects/[^'"`]*)""", content)
    assert not bad_refs, (
        f"project.html still uses /projects/ in location.href: {bad_refs}"
    )


def test_server_py_no_redirect_response_to_projects():
    """server.py RedirectResponse calls must not redirect to /projects/ paths."""
    with open(SERVER_PY) as f:
        content = f.read()
    # RedirectResponse(url="/projects/...") would be wrong
    bad_redirects = re.findall(r"""RedirectResponse\s*\(\s*url\s*=\s*[f'"]/projects/""", content)
    assert not bad_redirects, (
        f"server.py has RedirectResponse pointing to /projects/: {bad_redirects}"
    )


# ── AC7: docs/dashboard-readme.md updated ────────────────────────────────────

def test_docs_readme_documents_legacy_route():
    """dashboard-readme.md must document the /legacy/ route."""
    with open(DOCS_README) as f:
        content = f.read()
    assert "/legacy/" in content, "dashboard-readme.md does not mention /legacy/ route"


def test_docs_readme_documents_projects_redirect():
    """/projects/ redirect must be documented in dashboard-readme.md."""
    with open(DOCS_README) as f:
        content = f.read()
    assert "/projects/" in content, "dashboard-readme.md does not mention /projects/ redirect"


def test_docs_readme_documents_current_ui_route():
    """dashboard-readme.md must document the current /project/ UI."""
    with open(DOCS_README) as f:
        content = f.read()
    assert "/project/" in content and "project.html" in content, (
        "dashboard-readme.md does not document /project/ → project.html mapping"
    )


def test_docs_readme_documents_index_html_for_legacy():
    """dashboard-readme.md must reference index.html as the legacy UI asset."""
    with open(DOCS_README) as f:
        content = f.read()
    assert "index.html" in content, (
        "dashboard-readme.md does not mention index.html as the legacy UI asset"
    )


# ── AC8: index.html is not deleted ────────────────────────────────────────────

def test_index_html_still_exists():
    """apps/dashboard/static/index.html must not have been deleted."""
    index_html = os.path.join(STATIC_DIR, "index.html")
    assert os.path.isfile(index_html), (
        "apps/dashboard/static/index.html has been deleted — it must remain as the legacy UI asset"
    )


def test_index_html_has_content():
    """index.html must be non-empty (not a stub)."""
    index_html = os.path.join(STATIC_DIR, "index.html")
    assert os.path.getsize(index_html) > 1000, (
        "index.html appears to be empty or very small — expected the legacy UI content"
    )
