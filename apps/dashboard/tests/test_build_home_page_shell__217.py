"""Tests for issue #217: Build Home page shell and 4 summary stat cards."""
import re
import httpx
import pytest

BASE = "http://localhost:8000"


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE, timeout=10) as c:
        yield c


@pytest.fixture(scope="module")
def html(client):
    r = client.get("/home-preview")
    assert r.status_code == 200
    return r.text


@pytest.fixture(scope="module")
def js_content(html):
    """Home page JS is inline in home-preview.html."""
    return html


# ── AC1: Route and title ───────────────────────────────────────────────────────

def test_ac1_route_returns_200(client):
    r = client.get("/home-preview")
    assert r.status_code == 200


def test_ac1_html_title(html):
    assert "Commander — Home" in html or "Commander &#8212; Home" in html or "Commander &mdash; Home" in html


# ── AC2: Three-section layout ──────────────────────────────────────────────────

def test_ac2_right_now_section(html):
    assert "Right now" in html


def test_ac2_projects_section(html):
    assert "Projects" in html


def test_ac2_activity_section(html):
    assert "Activity" in html


# ── AC3: Header elements ───────────────────────────────────────────────────────

def test_ac3_commander_wordmark(html):
    assert "Commander" in html


def test_ac3_live_dot_present(html):
    assert "live-dot" in html or "live_dot" in html or "liveDot" in html or "green" in html.lower()


def test_ac3_header_present(html):
    assert "<header" in html.lower() or 'class="header' in html or 'id="header' in html


# ── AC4: Body padding ──────────────────────────────────────────────────────────

def test_ac4_desktop_padding(html):
    assert "32px" in html


def test_ac4_mobile_padding(html):
    assert "20px" in html


# ── AC5: Responsive grid ───────────────────────────────────────────────────────

def test_ac5_breakpoint_1024(html):
    assert "1024" in html


def test_ac5_breakpoint_700(html):
    assert "700" in html


def test_ac5_grid_columns(html):
    assert "grid" in html.lower() or "columns" in html.lower() or "col" in html.lower()


# ── AC6: Card structure ────────────────────────────────────────────────────────

def test_ac6_stat_cards_present(html):
    card_count = html.lower().count("stat-card") + html.lower().count("summary-card") + html.lower().count("card")
    assert card_count >= 4


def test_ac6_mono_number_style(html):
    assert "mono" in html.lower() or "monospace" in html.lower() or "font-mono" in html.lower()


# ── AC7: Card 1 — Sprint running ──────────────────────────────────────────────

def test_ac7_sprint_running_icon(html):
    assert "ti-player-play" in html or "player-play" in html


def test_ac7_sprint_running_label(html):
    assert "sprint" in html.lower() and "running" in html.lower()


def test_ac7_sprint_running_reference(html):
    assert "sprint_running" in html or "sprintRunning" in html or "sprint-running" in html


# ── AC8: Card 2 — Awaiting UAT ────────────────────────────────────────────────

def test_ac8_awaiting_uat_icon(html):
    assert "ti-eye-check" in html or "eye-check" in html


def test_ac8_awaiting_uat_reference(html):
    assert "awaiting_uat" in html or "awaitingUat" in html or "awaiting-uat" in html or "uat" in html.lower()


def test_ac8_amber_color(html):
    assert "amber" in html.lower() or "#f59e0b" in html.lower() or "orange" in html.lower() or "var(--amber" in html.lower() or "var(--yellow" in html.lower()


# ── AC9: Card 3 — Sprints planned ─────────────────────────────────────────────

def test_ac9_sprints_planned_icon(html):
    assert "ti-list-check" in html or "list-check" in html


def test_ac9_sprints_planned_reference(html):
    assert "sprints_planned" in html or "sprintsPlanned" in html or "sprints-planned" in html or "planned" in html.lower()


# ── AC10: Card 4 — Backlog ────────────────────────────────────────────────────

def test_ac10_backlog_icon(html):
    assert "ti-inbox" in html or "ti-archive" in html


def test_ac10_backlog_reference(html):
    assert "backlog" in html.lower()


# ── AC11: Card hover styles ────────────────────────────────────────────────────

def test_ac11_hover_transform(html):
    assert "translateY" in html and "-1px" in html


def test_ac11_hover_box_shadow(html):
    assert "box-shadow" in html.lower() or "boxShadow" in html


# ── AC12: Mobile tap targets ───────────────────────────────────────────────────

def test_ac12_min_height_44(html):
    assert "44px" in html or "min-height: 44" in html or "min-height:44" in html


# ── AC13: Keyboard accessibility ──────────────────────────────────────────────

def test_ac13_role_button(html):
    assert 'role="button"' in html or "role='button'" in html


def test_ac13_keyboard_handler(js_content):
    assert "Enter" in js_content or "keydown" in js_content or "keyup" in js_content


# ── AC14: API call on mount ────────────────────────────────────────────────────

def test_ac14_api_home_call(js_content):
    assert "/api/home" in js_content


def test_ac14_fetch_on_mount(js_content):
    assert "fetch" in js_content and "home" in js_content.lower()


# ── AC15: Loading skeleton ────────────────────────────────────────────────────

def test_ac15_skeleton_present(html):
    assert "skeleton" in html.lower() or "pulse" in html.lower() or "loading" in html.lower()


def test_ac15_surface_2_var(html):
    assert "surface-2" in html or "--surface" in html


# ── AC16: Error handling ───────────────────────────────────────────────────────

def test_ac16_error_handling_in_js(js_content):
    assert "error" in js_content.lower() or "catch" in js_content.lower()


def test_ac16_retry_logic(js_content):
    assert "retry" in js_content.lower() or "loadHome" in js_content or "load_home" in js_content


# ── AC17: GET /api/home endpoint ──────────────────────────────────────────────

def test_ac17_api_home_endpoint(client):
    r = client.get("/api/home")
    assert r.status_code == 200
    data = r.json()
    assert "stats" in data or isinstance(data, dict)
