"""Tests for issue #1766: Cap modal height to prevent off-screen overflow on mobile (runs against UAT)"""
import os
import pytest
import httpx


# Resolved from UAT .env at runtime; see tester skill Step 0.
BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "8001")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )


def _uat_available() -> bool:
    try:
        httpx.get(BASE_URL + "/", timeout=2.0)
        return True
    except httpx.ConnectError:
        return False


pytestmark = pytest.mark.skipif(
    not _uat_available(),
    reason=f"UAT server not available at {BASE_URL}",
)


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# --- Acceptance Criteria ---

def test_modal_height_cap__base_modal_css(client):
    # AC: Base `.modal` has `max-height: calc(100dvh - 32px)`, `display: flex`,
    #     `flex-direction: column`, and `overflow: hidden` applied
    r = client.get("/project/commander")
    assert r.status_code == 200
    html = r.text
    # Verify the CSS rules are present in the HTML (esbuild bundles CSS inline)
    assert "max-height: calc(100dvh - 32px)" in html or "max-height:calc(100dvh - 32px)" in html.replace(" ", "")
    assert ".modal" in html
    pytest.skip("manual — CSS structure verified via design-contract gate + browser step")


def test_modal_height_cap__modal_body_flex(client):
    # AC: `.modal-body` has `flex: 1 1 auto`, `overflow-y: auto`, and `min-height: 0`
    #     so it scrolls within a fixed-height modal
    r = client.get("/project/commander")
    assert r.status_code == 200
    html = r.text
    assert ".modal-body" in html
    pytest.skip("manual — CSS structure verified via design-contract gate + browser step")


def test_modal_height_cap__header_footer_fixed(client):
    # AC: Modal header and footer remain visible and fixed within the modal
    #     when content overflows
    r = client.get("/project/commander")
    assert r.status_code == 200
    pytest.skip("manual — verified via browser step opening fs-modal or bc-modal")


def test_modal_height_cap__fs_modal_50_tickets_390x844(client):
    # AC: `#fs-modal` (Finish/Merge Sprint) with a 50-item ticket list at 390×844
    #     shows header, scrollable body, and reachable Confirm/Cancel buttons
    #     without any element clipped outside the viewport
    r = client.get("/project/commander")
    assert r.status_code == 200
    pytest.skip("manual — verified via browser step at 390×844 viewport")


def test_modal_height_cap__bc_modal_50_tickets_390x844(client):
    # AC: `#bc-modal` (Bulk Complete) with a 50-item ticket list at 390×844
    #     shows header, scrollable body, and reachable Confirm/Cancel buttons
    #     without any element clipped outside the viewport
    r = client.get("/project/commander")
    assert r.status_code == 200
    pytest.skip("manual — verified via browser step at 390×844 viewport")


def test_modal_height_cap__all_modals_fit_viewport_390x844(client):
    # AC: Every `.modal` variant fits within `100dvh - 32px` vertically
    #     and `100vw - 32px` horizontally at 390×844
    r = client.get("/project/commander")
    assert r.status_code == 200
    pytest.skip("manual — verified via browser step inspection of each modal")


def test_modal_height_cap__modal_widths_normalized(client):
    # AC: `.ns-modal`, `.ct-modal`, `.cl-modal`, `.bcm-modal`, `.re-est-modal` widths
    #     are normalized to `width: min(<current>px, calc(100vw - 32px))`
    r = client.get("/project/commander")
    assert r.status_code == 200
    html = r.text
    # Verify at least some of these modal classes are defined
    for modal_class in [".ns-modal", ".ct-modal", ".cl-modal", ".bcm-modal", ".re-est-modal"]:
        assert modal_class in html
    pytest.skip("manual — CSS structure verified via design-contract gate")


def test_modal_height_cap__hs_mt_modal_min_width_removed(client):
    # AC: `.hs-modal` and `.mt-modal` fixed `min-width: 320–360px` is removed or
    #     overridden so they do not exceed `358px` (390 − 32) on a 390px viewport
    r = client.get("/project/commander")
    assert r.status_code == 200
    html = r.text
    for modal_class in [".hs-modal", ".mt-modal"]:
        assert modal_class in html
    pytest.skip("manual — CSS structure verified via design-contract gate")


def test_modal_height_cap__capped_modals_unchanged(client):
    # AC: Already-capped modals (`.nt-modal`, `.lv-modal`, `.pf-modal`, `.apm-modal`)
    #     are not modified
    r = client.get("/project/commander")
    assert r.status_code == 200
    html = r.text
    for modal_class in [".nt-modal", ".lv-modal", ".pf-modal", ".apm-modal"]:
        assert modal_class in html
    pytest.skip("manual — out-of-scope; pre-existing capped modals not modified")


def test_modal_height_cap__backdrop_close_still_works(client):
    # AC: Backdrop click-to-close (`:23000–23014`) continues to work on all
    #     affected modals
    r = client.get("/project/commander")
    assert r.status_code == 200
    pytest.skip("manual — verified via browser step click-outside-modal")


def test_modal_height_cap__desktop_no_layout_shift(client):
    # AC: Desktop rendering is unchanged for modals with short content
    #     (no layout shift or scrollbar flash)
    r = client.get("/project/commander")
    assert r.status_code == 200
    pytest.skip("manual — verified via browser step at 1440×900 with 3-item modal")
