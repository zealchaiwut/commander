"""Tests for issue #842 — Build home page: daily brief with project blocks.

This is a frontend ticket: the home route (`/`) renders a CEO-level "Daily
Brief" that is assembled client-side from the already-existing data layer
(`/api/brief/daily` from #841 and the stored per-project summaries from #840).

Each test is anchored to a specific acceptance criterion. The page is vanilla
HTML + JS served from disk, so the tests assert on the served file's structure,
its design tokens (which must match the attached mock), and the endpoints its
JavaScript wires up — there is no build step and no client-side runtime to drive
from pytest.

AC1  Home route renders a "Daily Brief" header with current date + "covering
     since …" subtitle derived from the roll-up.
AC2  Date navigation controls (prev day, Today, date picker) update the brief.
AC3  Global KPI strip renders four roll-up metrics.
AC4  "Needs Your Decision" section lists every aggregated decision.
AC5  Each decision exposes a one-tap action mapped to its correct endpoint.
AC6  One project brief block per project.
AC7  Each block header shows project name + status pill.
AC8  Each block displays the stored AI summary paragraph.
AC9  Each block shows a permanently-visible one-line status.
AC10 Show/Hide details toggle collapses only shipped-detail + timeline.
AC11 AI summary + one-line status stay visible when details collapsed.
AC12 Toggle state is independent per project.
AC13 "Add new project" entry below all project blocks.
AC14 Colours/typography/spacing match the mock's design tokens.
AC15 Page reads from the roll-up endpoint + stored summaries (no hardcoded data).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = REPO_ROOT / "apps" / "dashboard" / "static"
HOME_HTML = STATIC_DIR / "home.html"
SERVER_PY = REPO_ROOT / "apps" / "dashboard" / "server.py"


@pytest.fixture(scope="module")
def html() -> str:
    assert HOME_HTML.exists(), f"home page not found at {HOME_HTML}"
    return HOME_HTML.read_text()


@pytest.fixture(scope="module")
def server_src() -> str:
    return SERVER_PY.read_text()


# ── AC: route wiring ──────────────────────────────────────────────────────────

def test_root_route_serves_home_page(server_src):
    """The `/` route must serve the new daily-brief page (home.html)."""
    # Locate the GET "/" handler and assert it serves home.html.
    m = re.search(r'@app\.get\("/"\)\s*\n(?:.*\n){0,10}?.*?home\.html',
                  server_src, re.DOTALL)
    assert m, 'GET "/" handler must serve "home.html"'


# ── AC1: brief header ─────────────────────────────────────────────────────────

def test_ac1_daily_brief_header_present(html):
    assert "Daily Brief" in html, "brief kicker missing"
    assert 'class="brief-kicker"' in html
    # date element rendered from the roll-up date
    assert 'class="brief-date"' in html
    # "covering since" subtitle
    assert 'class="brief-cover"' in html
    assert "covering since" in html.lower()


def test_ac1_covering_since_derived_from_rollup(html):
    """The cover subtitle is populated from the artifact's covering_since field."""
    assert "covering_since" in html, "must read covering_since from the roll-up"


# ── AC2: date navigation ──────────────────────────────────────────────────────

def test_ac2_date_nav_controls_present(html):
    assert 'class="brief-nav"' in html
    # previous-day arrow
    assert "ti-chevron-left" in html, "previous-day arrow icon missing"
    # Today button
    assert ">Today<" in html, "Today button missing"
    # date picker
    assert 'type="date"' in html, "date picker input missing"


def test_ac2_date_nav_updates_brief(html):
    """Prev/Today/picker each re-load the brief for the selected date."""
    for fn in ("gotoPrevDay", "gotoToday", "onDatePick"):
        assert fn in html, f"date-nav handler {fn} missing"
    # navigation re-requests the dated brief rather than reloading the page
    assert "loadBrief" in html, "loadBrief() driver missing"


# ── AC3: global KPI strip ─────────────────────────────────────────────────────

def test_ac3_kpi_strip_four_metrics(html):
    assert 'class="kpis"' in html
    for label in ("Sprints shipped", "Tickets done", "In progress", "Need your call"):
        assert label in html, f"KPI label '{label}' missing"


def test_ac3_kpis_read_from_global_kpis(html):
    for field in ("sprints_shipped", "tickets_done", "in_progress", "needs_your_call"):
        assert field in html, f"global KPI field '{field}' not read"


# ── AC4: needs your decision ──────────────────────────────────────────────────

def test_ac4_needs_decision_section(html):
    assert "Needs your decision" in html, "decision section title missing"
    assert 'class="decisions"' in html
    # iterates the aggregated decisions list from the roll-up
    assert "decisions" in html
    assert "suggested_action" in html, "decision text not rendered from data"


# ── AC5: decision actions mapped to existing endpoints ────────────────────────

def test_ac5_run_sprint_action_endpoint(html):
    assert "/api/sprints/run" in html, "Run sprint must POST /api/sprints/run"
    assert "run_ready" in html, "run-ready decision type not handled"


def test_ac5_review_rework_action_endpoint(html):
    # Review rework reads the rerun preview for the project's sprint.
    assert "rerun-preview" in html, "Review rework must hit the rerun-preview endpoint"
    assert "rework" in html, "rework decision type not handled"


def test_ac5_cleanup_branches_action_endpoint(html):
    assert "/cleanup-stale-branches" in html, "Clean up must hit cleanup-stale-branches"
    assert "branch_cleanup" in html, "branch_cleanup decision type not handled"


def test_ac5_actions_no_full_page_reload(html):
    """Action handlers fetch + update button state; they don't reload the page."""
    for fn in ("runSprintAction", "reviewReworkAction", "cleanupBranchesAction"):
        assert fn in html, f"decision action handler {fn} missing"


# ── AC6: one project block per project ────────────────────────────────────────

def test_ac6_project_block_per_project(html):
    assert 'class="pbrief' in html, "per-project brief card missing"
    assert "renderProjects" in html or "renderProject" in html, "project render loop missing"
    # iterates the roll-up's projects list
    assert ".projects" in html or "brief.projects" in html or "['projects']" in html


# ── AC7: header name + status pill ────────────────────────────────────────────

def test_ac7_project_header_name_and_status_pill(html):
    assert 'class="pbname"' in html, "project name element missing"
    assert "pbstatus" in html, "status pill missing"
    # three pill states (active/running, idle, blocked)
    assert "pbstatus run" in html
    assert "pbstatus idle" in html
    assert "_projectBadgeHtml" in html, "project icon badge must use settings identity"
    assert "colorHex" in html, "project color must resolve like sprint mgmt"
    assert "ti-terminal" not in html, "generic terminal icon must not be hardcoded"
    assert "_projectTitleLinkHtml" in html, "project title must link to sprint mgmt"
    assert "pb-title-link" in html, "project title link class missing"
    assert "/sprint-mgmt" in html, "project brief must jump to sprint management"


# ── AC8: AI summary paragraph ─────────────────────────────────────────────────

def test_ac8_ai_summary_from_stored_summary_endpoint(html):
    assert "/brief/summary" in html, "must read stored per-project summary"
    assert "pb-sum-text" in html, "AI summary paragraph element missing"
    assert "pb-sum-tag" in html, "AI summary tag missing"
    assert "pb-sum-loading" in html, "AI summary block must show inline loading state"
    assert "summaryLoadingHtml" in html, "summary loading helper missing"


# ── AC9: permanent one-line status ────────────────────────────────────────────

def test_ac9_one_line_status_present(html):
    assert "pb-statusline" in html, "one-line status element missing"
    # idle fallback wording
    assert "last shipped" in html.lower(), "idle 'last shipped' status missing"
    # running-strip text is driven by in_progress data
    assert "in_progress" in html


# ── AC10: 2×2 project grid with separator lines ───────────────────────────────

def test_ac10_project_grid_layout(html):
    assert "pb-grid" in html, "2×2 project grid missing"
    for cell in ("pb-cell-summary", "pb-cell-todo", "pb-cell-shipped", "pb-cell-activity"):
        assert cell in html, f"grid cell {cell} missing"
    assert re.search(r"\.pb-grid\{[^}]*grid-template-columns", html), \
        "project grid must use two columns"
    assert "Shipped detail" in html
    assert "Recent activity" in html
    assert "HOME_ACTIVITY_LIMIT" in html, "recent activity list cap missing"


# ── AC11: TO-DO and Recent activity share the right column ─────────────────────

def test_ac11_todo_and_activity_aligned_right(html):
    """TO-DO (row 1) and Recent activity (row 2) sit in the right grid column."""
    todo_idx = html.index("pb-cell-todo")
    activity_idx = html.index("pb-cell-activity")
    summary_idx = html.index("pb-cell-summary")
    shipped_idx = html.index("pb-cell-shipped")
    assert todo_idx < activity_idx, "TO-DO must precede Recent activity in markup"
    assert summary_idx < shipped_idx, "AI summary must precede Shipped detail in markup"
    assert re.search(r"\.pb-cell-todo\{[^}]*grid-column\s*:\s*2", html), \
        "TO-DO must occupy the right column"
    assert re.search(r"\.pb-cell-activity\{[^}]*grid-column\s*:\s*2", html), \
        "Recent activity must occupy the right column"


# ── AC12: Open commander link in bottom-right footer ───────────────────────────

def test_ac12_open_commander_footer(html):
    assert "pb-foot" in html, "project card footer missing"
    assert "open-proj" in html, "Open commander link missing"
    assert re.search(r"\.pb-foot\{[^}]*justify-content\s*:\s*flex-end", html), \
        "Open commander must sit at the bottom right"


# ── AC13: add new project ─────────────────────────────────────────────────────

def test_ac13_add_new_project_entry(html):
    assert "Add new project" in html, "Add new project entry missing"
    assert "add-proj" in html


# ── AC14: design tokens match the mock ────────────────────────────────────────

def test_ac14_design_tokens_match_mock(html):
    # Core palette tokens from the attached mock (home_ceo_brief_mock.html).
    expected_tokens = {
        "--green:#16a34a",
        "--amber:#d97706",
        "--red:#dc2626",
        "--blue:#2563eb",
        "--purple:#7c3aed",
        "--bg:#f9fafb",
        "--surface:#fff",
        "--border:#e5e7eb",
        "--text:#111827",
    }
    collapsed = re.sub(r"\s+", "", html)
    for tok in expected_tokens:
        assert tok.replace(" ", "") in collapsed, f"design token {tok} missing"
    # dark theme support carried over from the mock
    assert '[data-theme="dark"]' in html, "dark theme tokens missing"
    # signature component classes from the mock
    for cls in ("brief-head", "kpis", "kpi-v", "decisions", "dec-act", "pbrief"):
        assert cls in html, f"mock component class '{cls}' missing"


def test_ac14_responsive_narrow_viewport(html):
    """Layout reflows on narrow (mobile/Tailscale) viewports — KPI strip wraps."""
    assert "@media" in html, "no responsive media query present"
    # a narrow breakpoint that reflows the kpi strip
    assert re.search(r"@media[^{]*max-width:\s*(?:[0-9]{2,3})px[^{]*\{[^}]*kpis",
                     re.sub(r"\s+", "", html)) or "flex-wrap" in html, \
        "KPI strip must reflow on narrow viewports"


# ── AC15: data-driven, no hardcoded sample data ───────────────────────────────

def test_ac15_reads_from_rollup_and_summaries(html):
    assert "/api/brief/daily" in html, "must read the home roll-up artifact"
    assert "/brief/summary" in html, "must read the stored project summaries"


def test_ac15_no_hardcoded_mock_content(html):
    """The mock's sample copy must not be baked into the page as static markup."""
    sample_literals = [
        "resilience sprint overnight",
        "metrics foundation",
        "_dispatch_reviewer step to sprint_manager.py",
        "building reviewer prompt with sprint_label",
        "launchd auto-restart on crash and boot",
    ]
    for lit in sample_literals:
        assert lit not in html, f"hardcoded mock sample content leaked: {lit!r}"
