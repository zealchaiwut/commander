"""Deck deep-link routing (issue: deck jump keys).

The dashboard frontend is vanilla HTML/JS served from disk, so — per the repo
convention (see test_798__*) — these assert against the static source that a cold
load of each deck URL selects the right project + view, applies the errors
filter, and that the home page redirects the `/?project=` scheme to the project
page. Final URLs are documented in docs/deck-urls.md.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_STATIC = _REPO_ROOT / "apps" / "dashboard" / "static"
_PROJECT_HTML = _STATIC / "project.html"
_HOME_HTML = _STATIC / "home.html"
_DECK_DOCS = _REPO_ROOT / "docs" / "deck-urls.md"


@pytest.fixture(scope="module")
def project() -> str:
    return _PROJECT_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def home() -> str:
    return _HOME_HTML.read_text(encoding="utf-8")


# ── home redirect: /?project= -> /project/<P>/<tab>?view&filter ────────────────

def test_home_redirects_project_query(home):
    assert "_deckRedirect" in home, "home must translate the deck ?project= URL"
    assert "p.get('project')" in home or 'get("project")' in home
    # view -> tab mapping
    assert "'logs'" in home and "'tickets'" in home and "'sprint'" in home
    assert "window.location.replace(" in home
    # called before the normal home boot
    assert "if (_deckRedirect()) return;" in home


def test_home_carries_view_and_filter(home):
    assert "q.set('view'" in home
    assert "filter" in home and "q.set('filter'" in home


# ── project.html: parseUrl reads view + filter ────────────────────────────────

def test_parseurl_reads_view_and_filter(project):
    idx = project.find("function parseUrl()")
    body = project[idx:idx + 2200]
    assert "URLSearchParams(window.location.search)" in body
    assert "get('view')" in body and "get('filter')" in body
    assert "return { slug, tab, view, filter }" in body


# ── sub-view deep-link (running / board / history / summary->history) ─────────

def test_subview_mapping_summary_to_history(project):
    idx = project.find("function _deepLinkSprintSubView()")
    assert idx >= 0, "deep-link sub-view mapper must exist"
    body = project[idx:idx + 400]
    assert "'running'" in body and "'history'" in body and "'board'" in body
    # summary lands on History
    assert "case 'summary': return 'history'" in body


def test_switchtab_honors_deeplink_subview(project):
    idx = project.find("Open the deck-requested sub-view")
    assert idx >= 0, "switchTab must open the deck sub-view, not always Board"
    body = project[idx:idx + 600]
    assert "_applyDeepLinkSubView()" in body
    assert "_smgmtShowSubView('board')" in body  # still the default fallback


def test_coldload_reapplies_subview_after_repo_ready(project):
    # The first switchTab fires before _cachedFullRepo is populated; the loader
    # path must re-apply the sub-view so History/Running actually load.
    idx = project.find("await loadSprintMgmt();")
    assert idx >= 0
    body = project[idx:idx + 400]
    assert "_applyDeepLinkSubView();" in body


# ── activity errors filter ────────────────────────────────────────────────────

def test_errors_filter_state_and_predicate(project):
    assert "errorsOnly: false" in project, "evl state must carry errorsOnly"
    midx = project.find("function _evlMatchesFilter")
    mbody = project[midx:midx + 400]
    assert "_evlState.errorsOnly && !_evlIsError(ev)" in mbody, \
        "the errors overlay must gate the filter on _evlIsError"


def test_errors_filter_pill_and_toggle(project):
    assert 'id="evl-filter-errors"' in project, "an Errors pill must exist"
    assert "evlToggleErrorsOnly" in project
    assert "filter === 'errors'" in project, "deck filter=errors must seed errorsOnly"


# ── canonical URL preserves the query (refresh / back-forward) ────────────────

def test_canonical_url_preserves_query(project):
    assert "expected + window.location.search" in project, \
        "replaceState must keep ?view=/?filter= so refresh re-lands on the pane"


def test_popstate_reparses_url(project):
    idx = project.find("window.addEventListener('popstate'")
    body = project[idx:idx + 500]
    assert "parseUrl()" in body, "back/forward must re-read the URL (view/filter)"


# ── docs ──────────────────────────────────────────────────────────────────────

def test_deck_urls_doc_lists_every_key():
    assert _DECK_DOCS.exists(), "docs/deck-urls.md must document the final URLs"
    doc = _DECK_DOCS.read_text(encoding="utf-8")
    for view in ("running", "activity", "summary", "board", "history", "tickets"):
        assert f"view={view}" in doc, f"deck-urls.md must list view={view}"
    assert "filter=errors" in doc
