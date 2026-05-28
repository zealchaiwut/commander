"""Tests for issue #219: Home — Add Activity feed (last 5 events)"""
import os
import re
import pytest
import httpx

BASE_URL = os.environ.get("TEST_BASE_URL", "http://localhost:8010")


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# ──────────────────────────────────────────────────────────────────────────────
# AC: Activity is fetched as part of GET /api/home — no separate request
# ──────────────────────────────────────────────────────────────────────────────

def test_activity_feed__included_in_home_endpoint(client):
    """AC: Activity field is part of GET /api/home response (no separate request)."""
    r = client.get("/api/home")
    assert r.status_code == 200
    data = r.json()
    assert "activity" in data, "GET /api/home must include 'activity' key"


def test_activity_feed__is_list(client):
    """AC: activity value is a list."""
    r = client.get("/api/home")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data["activity"], list)


def test_activity_feed__max_5_items(client):
    """AC: At most 5 activity items returned."""
    r = client.get("/api/home")
    assert r.status_code == 200
    data = r.json()
    assert len(data["activity"]) <= 5, f"Expected ≤5 activity items, got {len(data['activity'])}"


# ──────────────────────────────────────────────────────────────────────────────
# AC: Each item has required fields
# ──────────────────────────────────────────────────────────────────────────────

def test_activity_feed__items_have_type_field(client):
    """AC: Each activity item has a 'type' field."""
    r = client.get("/api/home")
    assert r.status_code == 200
    items = r.json().get("activity", [])
    for item in items:
        assert "type" in item, f"Activity item missing 'type' field: {item}"


def test_activity_feed__items_have_title_field(client):
    """AC: Each activity item has a 'title' field."""
    r = client.get("/api/home")
    assert r.status_code == 200
    items = r.json().get("activity", [])
    for item in items:
        assert "title" in item, f"Activity item missing 'title' field: {item}"
        assert item["title"], f"Activity item has empty 'title': {item}"


def test_activity_feed__items_have_timestamp_field(client):
    """AC: Each activity item has a 'timestamp' field (ISO 8601)."""
    r = client.get("/api/home")
    assert r.status_code == 200
    items = r.json().get("activity", [])
    iso_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
    for item in items:
        assert "timestamp" in item, f"Activity item missing 'timestamp': {item}"
        assert iso_pattern.match(item["timestamp"]), (
            f"'timestamp' not ISO format: {item['timestamp']}"
        )


def test_activity_feed__items_have_link_field(client):
    """AC: Each activity item has a 'link' field (may be empty string but key must exist)."""
    r = client.get("/api/home")
    assert r.status_code == 200
    items = r.json().get("activity", [])
    for item in items:
        assert "link" in item, f"Activity item missing 'link' field: {item}"


# ──────────────────────────────────────────────────────────────────────────────
# AC: Items render in reverse chronological order
# ──────────────────────────────────────────────────────────────────────────────

def test_activity_feed__reverse_chronological_order(client):
    """AC: Activity items are sorted in reverse chronological order (newest first)."""
    r = client.get("/api/home")
    assert r.status_code == 200
    items = r.json().get("activity", [])
    if len(items) < 2:
        pytest.skip("Need 2+ items to verify ordering")
    timestamps = [item["timestamp"] for item in items]
    assert timestamps == sorted(timestamps, reverse=True), (
        f"Items not in reverse chronological order: {timestamps}"
    )


# ──────────────────────────────────────────────────────────────────────────────
# AC: Known event types
# ──────────────────────────────────────────────────────────────────────────────

KNOWN_EVENT_TYPES = {
    "sprint_started",
    "sprint_completed",
    "ticket_moved_to_uat",
    "ticket_needs_rework",
    "sprint_failed",
}


def test_activity_feed__event_types_are_known(client):
    """AC: All activity item types are one of the five known event types."""
    r = client.get("/api/home")
    assert r.status_code == 200
    items = r.json().get("activity", [])
    for item in items:
        assert item["type"] in KNOWN_EVENT_TYPES, (
            f"Unknown event type '{item['type']}' — expected one of {KNOWN_EVENT_TYPES}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# AC: HTML structure — section header, list container, CSS variables
# ──────────────────────────────────────────────────────────────────────────────

def test_activity_feed__html_section_header_activity(client):
    """AC: Section title 'Activity' left-aligned."""
    r = client.get("/static/home-preview.html")
    assert r.status_code == 200
    html = r.text
    assert "Activity" in html, "Section heading 'Activity' not found in HTML"


def test_activity_feed__html_section_header_last5_label(client):
    """AC: 'last 5 events' label present in section header."""
    r = client.get("/static/home-preview.html")
    assert r.status_code == 200
    html = r.text
    assert "last 5 events" in html, "'last 5 events' label not found in HTML"


def test_activity_feed__html_activity_list_container(client):
    """AC: activity-list container element present in HTML."""
    r = client.get("/static/home-preview.html")
    assert r.status_code == 200
    html = r.text
    assert 'id="activity-list"' in html, "'activity-list' container missing from HTML"


def test_activity_feed__html_css_variable_sprint_started(client):
    """AC: CSS variable for sprint_started badge color defined."""
    r = client.get("/static/home-preview.html")
    assert r.status_code == 200
    html = r.text
    assert "--activity-sprint-started-badge" in html
    assert "--activity-sprint-started-icon" in html


def test_activity_feed__html_css_variable_sprint_completed(client):
    """AC: CSS variable for sprint_completed badge color defined."""
    r = client.get("/static/home-preview.html")
    assert r.status_code == 200
    html = r.text
    assert "--activity-sprint-completed-badge" in html
    assert "--activity-sprint-completed-icon" in html


def test_activity_feed__html_css_variable_ticket_uat(client):
    """AC: CSS variable for ticket_moved_to_uat badge color defined (amber)."""
    r = client.get("/static/home-preview.html")
    assert r.status_code == 200
    html = r.text
    assert "--activity-ticket-uat-badge" in html
    assert "--activity-ticket-uat-icon" in html


def test_activity_feed__html_css_variable_ticket_rework(client):
    """AC: CSS variable for ticket_needs_rework badge color defined (red)."""
    r = client.get("/static/home-preview.html")
    assert r.status_code == 200
    html = r.text
    assert "--activity-ticket-rework-badge" in html
    assert "--activity-ticket-rework-icon" in html


def test_activity_feed__html_css_variable_sprint_failed(client):
    """AC: CSS variable for sprint_failed badge color defined (red)."""
    r = client.get("/static/home-preview.html")
    assert r.status_code == 200
    html = r.text
    assert "--activity-sprint-failed-badge" in html
    assert "--activity-sprint-failed-icon" in html


# ──────────────────────────────────────────────────────────────────────────────
# AC: Activity card CSS — border, radius, gap, badge size
# ──────────────────────────────────────────────────────────────────────────────

def test_activity_feed__html_card_border_radius(client):
    """AC: Activity item card has 8px border radius."""
    r = client.get("/static/home-preview.html")
    assert r.status_code == 200
    html = r.text
    assert "border-radius: 8px" in html or "border-radius:8px" in html, (
        "8px border radius for activity item card not found in CSS"
    )


def test_activity_feed__html_list_gap(client):
    """AC: Activity list has 8px gap between items."""
    r = client.get("/static/home-preview.html")
    assert r.status_code == 200
    html = r.text
    # Check that activity-list has gap: 8px
    assert "gap: 8px" in html, "8px gap in activity-list not found"


def test_activity_feed__html_badge_size_34(client):
    """AC: Icon badge is 34×34px."""
    r = client.get("/static/home-preview.html")
    assert r.status_code == 200
    html = r.text
    assert "width: 34px" in html and "height: 34px" in html, (
        "34×34 badge dimensions not found in CSS"
    )


# ──────────────────────────────────────────────────────────────────────────────
# AC: Icon config — Tabler icon classes per event type
# ──────────────────────────────────────────────────────────────────────────────

def test_activity_feed__html_icon_sprint_started(client):
    """AC: sprint_started uses a play icon (ti-player-play)."""
    r = client.get("/static/home-preview.html")
    assert r.status_code == 200
    html = r.text
    assert "ti-player-play" in html, "ti-player-play icon for sprint_started not found"


def test_activity_feed__html_icon_sprint_completed(client):
    """AC: sprint_completed uses a check icon."""
    r = client.get("/static/home-preview.html")
    assert r.status_code == 200
    html = r.text
    # ti-circle-check or ti-check
    assert "ti-circle-check" in html or "ti-check" in html, (
        "Check icon for sprint_completed not found"
    )


def test_activity_feed__html_icon_ticket_uat(client):
    """AC: ticket_moved_to_uat uses an eye-check icon (ti-eye-check)."""
    r = client.get("/static/home-preview.html")
    assert r.status_code == 200
    html = r.text
    assert "ti-eye-check" in html, "ti-eye-check icon for ticket_moved_to_uat not found"


def test_activity_feed__html_icon_ticket_rework(client):
    """AC: ticket_needs_rework uses an x/close icon."""
    r = client.get("/static/home-preview.html")
    assert r.status_code == 200
    html = r.text
    assert "ti-x" in html, "ti-x icon for ticket_needs_rework not found"


def test_activity_feed__html_icon_sprint_failed(client):
    """AC: sprint_failed uses an alert icon (ti-alert-triangle)."""
    r = client.get("/static/home-preview.html")
    assert r.status_code == 200
    html = r.text
    assert "ti-alert-triangle" in html, "ti-alert-triangle icon for sprint_failed not found"


# ──────────────────────────────────────────────────────────────────────────────
# AC: Hover state CSS — border darkens, background tint
# ──────────────────────────────────────────────────────────────────────────────

def test_activity_feed__html_hover_state_on_clickable(client):
    """AC: Hover state applied only to clickable items (is-clickable class)."""
    r = client.get("/static/home-preview.html")
    assert r.status_code == 200
    html = r.text
    assert "is-clickable" in html, "is-clickable class missing from HTML"
    # Check hover uses is-clickable selector
    assert ".activity-item.is-clickable:hover" in html, (
        "Hover rule scoped to .is-clickable not found"
    )


def test_activity_feed__html_hover_border_darkens(client):
    """AC: Hover state darkens border (border-color: var(--text-sub))."""
    r = client.get("/static/home-preview.html")
    assert r.status_code == 200
    html = r.text
    # The hover rule should set border-color to text-sub or similar
    assert "border-color: var(--text-sub)" in html, (
        "Hover border-color: var(--text-sub) not found in activity item hover rule"
    )


def test_activity_feed__html_hover_bg_tint(client):
    """AC: Hover state applies a light background tint."""
    r = client.get("/static/home-preview.html")
    assert r.status_code == 200
    html = r.text
    assert "var(--surface-hover)" in html, (
        "Surface hover background tint (var(--surface-hover)) missing from hover rule"
    )


# ──────────────────────────────────────────────────────────────────────────────
# AC: Clickable vs non-clickable items
# ──────────────────────────────────────────────────────────────────────────────

def test_activity_feed__html_clickable_uses_anchor(client):
    """AC: Clickable items (with link) use <a> element for native keyboard nav."""
    r = client.get("/static/home-preview.html")
    assert r.status_code == 200
    html = r.text
    # renderActivity builds <a> when hasLink; check JS code
    assert "document.createElement(hasLink ? 'a' : 'div')" in html, (
        "Activity items must use <a> for clickable and <div> for non-clickable"
    )


def test_activity_feed__html_non_clickable_no_hover(client):
    """AC: Non-clickable items (no link) have no hover effect (is-clickable class absent)."""
    r = client.get("/static/home-preview.html")
    assert r.status_code == 200
    html = r.text
    # Hover is scoped only to .is-clickable
    assert ".activity-item.is-clickable:hover" in html
    # And there's no hover on plain .activity-item without .is-clickable
    assert ".activity-item:hover" not in html.replace(
        ".activity-item.is-clickable:hover", ""
    ), "Hover must only apply to .activity-item.is-clickable, not all .activity-item"


# ──────────────────────────────────────────────────────────────────────────────
# AC: Empty state
# ──────────────────────────────────────────────────────────────────────────────

def test_activity_feed__html_empty_state_text(client):
    """AC: Empty state text: 'Nothing recent — try running a sprint from a project'."""
    r = client.get("/static/home-preview.html")
    assert r.status_code == 200
    html = r.text
    assert "Nothing recent" in html, "Empty state message 'Nothing recent' not found"
    assert "try running a sprint from a project" in html, (
        "Empty state message incomplete — missing 'try running a sprint from a project'"
    )


def test_activity_feed__html_empty_state_small_icon(client):
    """AC: Empty state uses a small icon, no large graphic."""
    r = client.get("/static/home-preview.html")
    assert r.status_code == 200
    html = r.text
    assert "activity-empty" in html, ".activity-empty class missing from HTML"


def test_activity_feed__html_empty_state_centered(client):
    """AC: Empty state is center-aligned."""
    r = client.get("/static/home-preview.html")
    assert r.status_code == 200
    html = r.text
    # The .activity-empty rule should have align-items: center
    assert "align-items: center" in html, (
        "Empty state should be center-aligned (align-items: center)"
    )


# ──────────────────────────────────────────────────────────────────────────────
# AC: Sub-line timestamps as humanized relative time
# ──────────────────────────────────────────────────────────────────────────────

def test_activity_feed__html_relative_time_formatter(client):
    """AC: Client-side relative time formatter exists (no moment.js)."""
    r = client.get("/static/home-preview.html")
    assert r.status_code == 200
    html = r.text
    assert "fmtRelative" in html, "fmtRelative function missing from JS"
    # Must NOT add moment.js
    assert "moment.js" not in html and "moment.min.js" not in html, (
        "moment.js must not be added as a dependency"
    )


def test_activity_feed__html_relative_time_units(client):
    """AC: Relative time formatter produces human units (m ago, h ago, d ago)."""
    r = client.get("/static/home-preview.html")
    assert r.status_code == 200
    html = r.text
    # The formatter should produce strings like "m ago", "h ago", "d ago"
    assert "'m ago'" in html or "m ago" in html, "Relative time 'm ago' format missing"
    assert "'h ago'" in html or "h ago" in html, "Relative time 'h ago' format missing"
    assert "'d ago'" in html or "d ago" in html, "Relative time 'd ago' format missing"


def test_activity_feed__html_dayjs_optional(client):
    """AC: dayjs is used if already loaded; otherwise hand-rolled formatter is used."""
    r = client.get("/static/home-preview.html")
    assert r.status_code == 200
    html = r.text
    assert "dayjs" in html, "dayjs fallback check missing"
    assert "hand-rolled" in html.lower() or "fallback" in html.lower() or "typeof dayjs" in html, (
        "dayjs optional fallback logic missing"
    )


# ──────────────────────────────────────────────────────────────────────────────
# AC: Keyboard accessibility
# ──────────────────────────────────────────────────────────────────────────────

def test_activity_feed__html_items_keyboard_focusable(client):
    """AC: All activity items are keyboard-focusable (tabindex='0')."""
    r = client.get("/static/home-preview.html")
    assert r.status_code == 200
    html = r.text
    # renderActivity sets tabindex="0" on each item
    assert "tabindex" in html and "'0'" in html or 'tabindex="0"' in html, (
        "Activity items must have tabindex='0' for keyboard focus"
    )


def test_activity_feed__html_anchor_keyboard_nav(client):
    """AC: Clickable items use <a> for native Enter-key navigation."""
    r = client.get("/static/home-preview.html")
    assert r.status_code == 200
    html = r.text
    # <a> elements handle Enter natively; the code creates <a> when hasLink is true
    assert "createElement(hasLink ? 'a' : 'div')" in html or \
           "document.createElement(hasLink ? 'a' : 'div')" in html, (
        "<a> element for clickable items required for native keyboard navigation"
    )


# ──────────────────────────────────────────────────────────────────────────────
# AC: Chevron-right icon on clickable items
# ──────────────────────────────────────────────────────────────────────────────

def test_activity_feed__html_chevron_right_icon(client):
    """AC: Each clickable activity item shows a chevron-right icon on the right."""
    r = client.get("/static/home-preview.html")
    assert r.status_code == 200
    html = r.text
    assert "ti-chevron-right" in html, "ti-chevron-right icon for clickable items not found"
    # Chevron only on clickable items
    assert "hasLink ? '<i class=" in html or "activity-chevron" in html, (
        "Chevron should only render on clickable items"
    )


# ──────────────────────────────────────────────────────────────────────────────
# AC: Title and sub-line typography
# ──────────────────────────────────────────────────────────────────────────────

def test_activity_feed__html_title_font_size(client):
    """AC: Activity title is 14px bold."""
    r = client.get("/static/home-preview.html")
    assert r.status_code == 200
    html = r.text
    # Check .activity-title has font-size: 14px and font-weight: 700 (bold)
    assert "font-size: 14px" in html, "14px font size for activity-title not found"
    assert "font-weight: 700" in html, "Bold (700) font-weight for activity-title not found"


def test_activity_feed__html_sub_font_size(client):
    """AC: Sub-line is 12px in mono font, muted color."""
    r = client.get("/static/home-preview.html")
    assert r.status_code == 200
    html = r.text
    assert "font-size: 12px" in html, "12px font size for activity sub-line not found"
    assert "font-family: var(--mono)" in html, "Mono font family for sub-line not found"


# ──────────────────────────────────────────────────────────────────────────────
# AC: renderActivity slices to max 5 items
# ──────────────────────────────────────────────────────────────────────────────

def test_activity_feed__html_slice_5_items(client):
    """AC: renderActivity slices activity array to at most 5 items."""
    r = client.get("/static/home-preview.html")
    assert r.status_code == 200
    html = r.text
    assert ".slice(0, 5)" in html, "renderActivity must slice to 5 items: .slice(0, 5)"


# ──────────────────────────────────────────────────────────────────────────────
# AC: Hover/non-hover tests (manual — UI rendering)
# ──────────────────────────────────────────────────────────────────────────────

def test_activity_feed__hover_ui_rendering():
    """AC: Card border darkens and background tint appears on hover (manual UI check)."""
    pytest.skip("manual — requires visual inspection in a browser")


def test_activity_feed__color_treatment_by_type_visual():
    """AC: Icon colors per event type match spec (green/grey/amber/red) — visual check."""
    pytest.skip("manual — requires visual inspection in a browser")


def test_activity_feed__link_navigation():
    """AC: Click navigates to link field; missing link makes item non-clickable — browser only."""
    pytest.skip("manual — requires browser interaction")
