"""Tests for issue #1155: smgmtToggleAncestor localStorage persistence (runs against UAT)"""
import os
import pytest
import httpx


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

def test_smgmt_ancestor_localstorage__reads_localstorage_on_render(client):
    # AC: `_smgmtAncestorRowHtml` reads `localStorage.getItem('slp_ancestor_<label>')`
    # on render and omits the `hidden` attribute on the ancestor body element when
    # the stored value is "1".
    # This is a frontend behavior test; we verify it by checking the generated HTML
    # reflects the localStorage state.
    pytest.skip("browser step — verified via agent-browser UAT step 2, not HTTP")


def test_smgmt_ancestor_localstorage__chevron_down_when_expanded(client):
    # AC: The chevron icon renders in the "down" (expanded) state when `localStorage`
    # indicates the ancestor was previously expanded.
    pytest.skip("browser step — verified via agent-browser UAT step 2, not HTTP")


def test_smgmt_ancestor_localstorage__restores_expanded_on_refresh(client):
    # AC: Toggling an ancestor open, then refreshing the page, restores that ancestor
    # to its expanded state without any user interaction.
    pytest.skip("browser step — verified via agent-browser UAT steps 1-2, not HTTP")


def test_smgmt_ancestor_localstorage__restores_collapsed_on_refresh(client):
    # AC: Toggling an ancestor closed, then refreshing the page, restores that ancestor
    # to its collapsed state (the default `hidden` behavior is preserved).
    pytest.skip("browser step — verified via agent-browser UAT step 3, not HTTP")


def test_smgmt_ancestor_localstorage__state_isolated_per_sprint_label(client):
    # AC: Ancestor expand/collapse state is isolated per sprint label — expanding one
    # ancestor does not affect the stored or rendered state of another.
    pytest.skip("browser step — verified via agent-browser UAT step 5, not HTTP")
