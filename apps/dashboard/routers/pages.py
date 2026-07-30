"""Page-serving handlers extracted from server.py (issue #1248).

All HTML page routes (GET /, /home, /overview, /project/*) and their
supporting helpers (_inject_version_into_html, _serve_html) live here.
server.py mounts this router; it contains no inline page-serving handler
definitions.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from services.logging import log as _slog

router = APIRouter(tags=["pages"])

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

_HTML_NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, must-revalidate",
    "Pragma": "no-cache",
}

_VALID_PROJECT_TABS = {
    "sprint-mgmt", "tickets", "sprint-history",
    "notes", "settings", "global-settings", "roadmap",
    "failures",
    # "logs", "metrics", "status" removed in #2025 — kept as redirects below
}


def _compute_build_hash() -> str:
    """Compute an 8-char MD5 hash over all JS and CSS files in _STATIC_DIR."""
    h = hashlib.md5()
    for ext in ("*.js", "*.css"):
        for f in sorted(_STATIC_DIR.glob(ext)):
            try:
                h.update(f.read_bytes())
            except OSError:
                pass
    return h.hexdigest()[:8]


_BUILD_HASH: str = _compute_build_hash()


def _inject_version_into_html(html: str) -> str:
    """Inject ?v=<hash> query string on local /static/*.js and /static/*.css URLs."""
    pattern = r'((?:src|href)="(/static/[^"?]+\.(?:js|css))")'

    def _replacer(m: re.Match) -> str:
        attr_name = m.group(1).split("=")[0]  # src or href
        url = m.group(2)
        return f'{attr_name}="{url}?v={_BUILD_HASH}"'

    return re.sub(pattern, _replacer, html)


def _serve_html(path: Path) -> HTMLResponse:
    """Read an HTML file, inject cache-busting version stamps, and serve with no-cache headers."""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        raise HTTPException(status_code=404, detail="Not found")
    content = _inject_version_into_html(content)
    from routers.auth import inject_auth_script  # noqa: PLC0415
    content = inject_auth_script(content)
    return HTMLResponse(content=content, headers=_HTML_NO_CACHE_HEADERS)


# ── Page routes ───────────────────────────────────────────────────────────────

@router.get("/")
async def root(request: Request):
    _slog.event("route.entry", project="dashboard", request_id=request.state.request_id, route="/", method="GET")
    return _serve_html(_STATIC_DIR / "home.html")


@router.get("/brief")
async def brief_redirect():
    """Legacy /brief bookmarks → daily brief home at /."""
    return RedirectResponse(url="/", status_code=301)


@router.get("/home")
async def home_redirect():
    return RedirectResponse(url="/", status_code=301)


@router.get("/overview")
async def overview_redirect():
    return RedirectResponse(url="/", status_code=301)


# ── /projects/ redirect — 301 to current /project/ UI ────────────────────────

@router.get("/projects/{path:path}")
async def projects_redirect(path: str):
    """Redirect /projects/<slug>/<tab> → /project/<slug>/<tab> (301)."""
    parts = [p for p in path.strip("/").split("/") if p]
    if len(parts) >= 2:
        slug, tab = parts[0], parts[1]
        return RedirectResponse(url=f"/project/{slug}/{tab}", status_code=301)
    elif len(parts) == 1:
        slug = parts[0]
        return RedirectResponse(url=f"/project/{slug}/sprint-mgmt", status_code=301)
    else:
        return RedirectResponse(url="/", status_code=301)


# ── Slug-based project routes (/project/<slug>/...) ──────────────────────────

@router.get("/project/{slug}")
async def project_slug_no_tab(slug: str):
    """Redirect bare /project/<slug> to /project/<slug>/sprint-mgmt."""
    return RedirectResponse(url=f"/project/{slug}/sprint-mgmt", status_code=302)


@router.get("/project/{slug}/analytics")
async def project_slug_analytics(slug: str):
    """Retired standalone analytics page — redirect to Failures inbox."""
    return RedirectResponse(url=f"/project/{slug}/failures", status_code=301)


# ── Removed-tab redirects (issue #2025) ──────────────────────────────────────
# Analytics (metrics), Logs, and Status deep-links redirect to Failures inbox.
# Backend routes for these areas are preserved in their respective routers.

@router.get("/project/{slug}/metrics")
async def project_slug_metrics(slug: str):
    """Analytics tab removed (#2025) — redirect to Failures inbox."""
    return RedirectResponse(url=f"/project/{slug}/failures", status_code=302)


@router.get("/project/{slug}/logs")
async def project_slug_logs(slug: str):
    """Logs tab removed (#2025) — redirect to Failures inbox."""
    return RedirectResponse(url=f"/project/{slug}/failures", status_code=302)


@router.get("/project/{slug}/status")
async def project_slug_status(slug: str):
    """Status deep-link removed (#2025) — redirect to Failures inbox."""
    return RedirectResponse(url=f"/project/{slug}/failures", status_code=302)


@router.get("/project/{slug}/{tab}")
async def project_slug_tab(slug: str, tab: str):
    """Serve the project chrome page for valid tabs; redirect invalid tabs to sprint-mgmt."""
    if tab not in _VALID_PROJECT_TABS:
        return RedirectResponse(url=f"/project/{slug}/sprint-mgmt", status_code=302)
    return _serve_html(_STATIC_DIR / "project.html")
