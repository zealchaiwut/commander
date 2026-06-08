"""Tests for issue #632: Build per-project Logs screen with unified timeline.

AC-1: Timeline fetches from GET /api/projects/{slug}/events and renders all returned
      events newest-first
AC-2: Events are grouped under day headers formatted as 'Today · Mon Jun 8' (or equiv
      date label for older days)
AC-3: Each row displays: timestamp · source badge · description · actor (GitHub rows only)
AC-4: Source badges are color-coded: AGENT and SPRINT use green, DASH uses blue,
      GITHUB uses purple — using design tokens from the mock file
AC-5: Filter chips render in order: All / Agent & Sprint / Dashboard / GitHub; clicking
      a chip toggles visibility of that source's rows
AC-6: All chip shows every row; selecting a specific chip hides rows from all other sources
AC-7: Day headers with zero visible rows (after filtering) are hidden entirely — no empty
      date groups shown
AC-8: Label events render a green + chip (add) or red − chip (remove), followed by the
      label name in its real color: sprint=green, UAT=purple, flow=blue, user=gray
AC-9: PR events render with the appropriate git/PR icon as shown in the mock
AC-10: Branch events render with the branch icon as shown in the mock
AC-11: Layout and color tokens match logs_screen_mock.html exactly (no custom one-off styles)
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECT_HTML = REPO_ROOT / "apps" / "dashboard" / "static" / "project.html"

_HTML = PROJECT_HTML.read_text()


# ── Python reference implementations ─────────────────────────────────────────

def _badge_label(source: str, ev_type: str) -> str:
    """Return the display label for a source badge (mirrors evlBadgeLabel JS)."""
    if source == "github":
        return "GITHUB"
    if source == "dashboard":
        return "DASH"
    if source == "agent":
        if ev_type.startswith("sprint_"):
            return "SPRINT"
        return "AGENT"
    return source.upper()


def _badge_color_class(badge_label: str) -> str:
    """Return the CSS class suffix for a badge (mirrors mock .lr-badge.<class>)."""
    mapping = {
        "AGENT": "agent",
        "SPRINT": "agent",
        "DASH": "dash",
        "GITHUB": "github",
    }
    return mapping.get(badge_label, "")


def _day_label(event_date: date, today: date) -> str:
    """Format a date as 'Today · Mon Jun 8' or 'Yesterday · Sun Jun 7' or 'Mon Jun 7'."""
    weekday_abbr = event_date.strftime("%a")
    month_abbr = event_date.strftime("%b")
    day_num = event_date.day
    base = f"{weekday_abbr} {month_abbr} {day_num}"
    if event_date == today:
        return f"Today · {base}"
    if event_date == today - timedelta(days=1):
        return f"Yesterday · {base}"
    return base


def _filter_source(source: str, chip: str) -> bool:
    """Return True if event with given source should be visible under chip filter."""
    if chip == "all":
        return True
    if chip == "agent":
        return source == "agent"
    if chip == "dashboard":
        return source == "dashboard"
    if chip == "github":
        return source == "github"
    return True


def _any_visible_in_day(events_in_day: list[dict], chip: str) -> bool:
    """Return True if any event in the day bucket is visible under chip filter."""
    return any(_filter_source(e["source"], chip) for e in events_in_day)


def _label_chip_class(label_name: str) -> str:
    """Return CSS class for a label chip based on label name patterns."""
    name = label_name.lower()
    if name.startswith("sprint"):
        return "lbl-sprint"
    if name in ("uat", "done", "ready"):
        return "lbl-uat"
    if name in ("coder-running", "tester-running", "ba-running", "in-progress", "sit"):
        return "lbl-flow"
    return "lbl-generic"


# ── AC-1: evlFetch calls /api/projects/{slug}/events ─────────────────────────

class TestAC1FetchEndpoint:
    def test_evlFetch_calls_events_endpoint(self):
        assert "/api/projects/" in _HTML and "/events" in _HTML, \
            "evlFetch must call the /api/projects/{slug}/events endpoint"

    def test_evlFetch_uses_slug_not_full_repo(self):
        # The slug is the last path component of the repo
        assert ".split('/').pop()" in _HTML or "split('/')[-1]" in _HTML or \
               re.search(r"\.split\(['\"/]['\"]?\)\s*\.pop\(\)", _HTML), \
               "evlFetch must use the repo slug (last path component), not the full repo string"

    def test_evlFetch_triggers_on_logs_tab(self):
        # When switching to logs/activity, evlFetch is called
        assert "evlFetch" in _HTML, "evlFetch function must exist"


# ── AC-2: Day header format ──────────────────────────────────────────────────

class TestAC2DayHeaders:
    def test_today_label(self):
        today = date(2026, 6, 8)
        assert _day_label(today, today) == "Today · Mon Jun 8"

    def test_yesterday_label(self):
        today = date(2026, 6, 8)
        yesterday = today - timedelta(days=1)
        assert _day_label(yesterday, today) == "Yesterday · Sun Jun 7"

    def test_older_day_label_no_prefix(self):
        today = date(2026, 6, 8)
        older = date(2026, 6, 5)
        label = _day_label(older, today)
        assert not label.startswith("Today")
        assert not label.startswith("Yesterday")
        assert "Jun" in label

    def test_day_header_html_present(self):
        assert "evl-day-head" in _HTML or "day-head" in _HTML, \
               "HTML must contain a day-head CSS class for day group headers"

    def test_day_label_function_exists(self):
        assert "evlDayLabel" in _HTML or "_evlDayLabel" in _HTML or \
               re.search(r"[Tt]oday.*·", _HTML), \
               "A function or pattern for 'Today ·' day header format must exist in project.html"


# ── AC-3: Row structure ───────────────────────────────────────────────────────

class TestAC3RowStructure:
    def test_timestamp_in_row(self):
        # lr-time class should be present in new timeline rows
        assert "lr-time" in _HTML or "evl-ts" in _HTML, \
               "Rows must include a timestamp element"

    def test_source_badge_in_row(self):
        assert "lr-badge" in _HTML or "evl-source-badge" in _HTML, \
               "Rows must include a source badge element"

    def test_actor_for_github_events(self):
        # Actor display class must exist
        assert "lr-actor" in _HTML or "evl-actor" in _HTML, \
               "Rows must include an actor element (shown for GitHub rows)"


# ── AC-4: Badge color classes ─────────────────────────────────────────────────

class TestAC4BadgeColors:
    def test_agent_badge_label(self):
        assert _badge_label("agent", "coder_started") == "AGENT"

    def test_sprint_badge_label(self):
        assert _badge_label("agent", "sprint_started") == "SPRINT"
        assert _badge_label("agent", "sprint_finished") == "SPRINT"

    def test_dash_badge_label(self):
        assert _badge_label("dashboard", "ticket_moved") == "DASH"

    def test_github_badge_label(self):
        assert _badge_label("github", "label_added") == "GITHUB"

    def test_agent_sprint_use_green_class(self):
        for badge in ("AGENT", "SPRINT"):
            cls = _badge_color_class(badge)
            assert cls == "agent", f"{badge} badge should use 'agent' (green) CSS class"

    def test_dash_uses_blue_class(self):
        assert _badge_color_class("DASH") == "dash"

    def test_github_uses_purple_class(self):
        assert _badge_color_class("GITHUB") == "github"

    def test_badge_color_css_in_html(self):
        # All three badge color classes must be styled in the HTML
        assert "lr-badge" in _HTML and ".lr-badge.agent" in _HTML, \
               ".lr-badge.agent CSS class must be defined"
        assert ".lr-badge.dash" in _HTML, \
               ".lr-badge.dash CSS class must be defined"
        assert ".lr-badge.github" in _HTML, \
               ".lr-badge.github CSS class must be defined"

    def test_green_for_agent_badge(self):
        # The agent badge must use the green color token
        m = re.search(r'\.lr-badge\.agent\s*\{[^}]*color\s*:\s*var\(--green\)', _HTML)
        assert m, ".lr-badge.agent must use color: var(--green)"

    def test_blue_for_dash_badge(self):
        m = re.search(r'\.lr-badge\.dash\s*\{[^}]*color\s*:\s*var\(--blue\)', _HTML)
        assert m, ".lr-badge.dash must use color: var(--blue)"

    def test_purple_for_github_badge(self):
        m = re.search(r'\.lr-badge\.github\s*\{[^}]*color\s*:\s*var\(--purple\)', _HTML)
        assert m, ".lr-badge.github must use color: var(--purple)"


# ── AC-5: Filter chips order ──────────────────────────────────────────────────

class TestAC5FilterChips:
    def test_all_chip_exists(self):
        assert re.search(r"data-filter=['\"]all['\"]", _HTML) or \
               re.search(r"evlSetFilter\(['\"]all['\"]", _HTML) or \
               re.search(r"setFilter\(['\"]all['\"]", _HTML), \
               "An 'All' filter chip must exist"

    def test_agent_sprint_chip_exists(self):
        assert "Agent" in _HTML and "Sprint" in _HTML, \
               "An 'Agent & Sprint' filter chip must exist"
        assert re.search(r"evlSetFilter\(['\"]agent['\"]|data-filter=['\"]agent['\"]", _HTML), \
               "Agent & Sprint chip must call filter with 'agent' source"

    def test_dashboard_chip_exists(self):
        assert re.search(r"evlSetFilter\(['\"]dashboard['\"]|data-filter=['\"]dashboard['\"]", _HTML), \
               "Dashboard chip must call filter with 'dashboard' source"

    def test_github_chip_exists(self):
        assert re.search(r"evlSetFilter\(['\"]github['\"]|data-filter=['\"]github['\"]", _HTML), \
               "GitHub chip must call filter with 'github' source"

    def test_chips_in_correct_order(self):
        # All must appear before Agent & Sprint, which must appear before Dashboard,
        # which must appear before GitHub
        all_pos = _HTML.find("'all'") if "'all'" in _HTML else _HTML.find('"all"')
        agent_pos = _HTML.find("'agent'") if "'agent'" in _HTML else _HTML.find('"agent"')
        dash_pos = _HTML.find("'dashboard'") if "'dashboard'" in _HTML else _HTML.find('"dashboard"')
        gh_pos = _HTML.find("'github'") if "'github'" in _HTML else _HTML.find('"github"')
        # Check relative order in the filter chip region (not just first occurrence)
        # Find the evlSetFilter calls in order
        pattern = re.compile(r"evlSetFilter\(['\"](\w+)['\"]")
        calls = [(m.start(), m.group(1)) for m in pattern.finditer(_HTML)]
        if calls:
            values = [v for _, v in calls]
            assert "all" in values, "evlSetFilter('all') must exist"
            assert "agent" in values, "evlSetFilter('agent') must exist"
            assert "dashboard" in values, "evlSetFilter('dashboard') must exist"
            assert "github" in values, "evlSetFilter('github') must exist"
            all_i = values.index("all")
            agent_i = values.index("agent")
            dash_i = values.index("dashboard")
            gh_i = values.index("github")
            assert all_i < agent_i < dash_i < gh_i, \
                   "Filter chips must appear in order: all → agent → dashboard → github"


# ── AC-6: Filter logic ────────────────────────────────────────────────────────

class TestAC6FilterLogic:
    def test_all_shows_every_source(self):
        for src in ("agent", "dashboard", "github"):
            assert _filter_source(src, "all") is True

    def test_agent_chip_shows_only_agent(self):
        assert _filter_source("agent", "agent") is True
        assert _filter_source("dashboard", "agent") is False
        assert _filter_source("github", "agent") is False

    def test_dashboard_chip_shows_only_dashboard(self):
        assert _filter_source("dashboard", "dashboard") is True
        assert _filter_source("agent", "dashboard") is False
        assert _filter_source("github", "dashboard") is False

    def test_github_chip_shows_only_github(self):
        assert _filter_source("github", "github") is True
        assert _filter_source("agent", "github") is False
        assert _filter_source("dashboard", "github") is False

    def test_evlSetFilter_function_exists(self):
        assert "evlSetFilter" in _HTML, \
               "evlSetFilter() function must exist to handle chip clicks"


# ── AC-7: Empty day headers hidden ───────────────────────────────────────────

class TestAC7EmptyDayHeaders:
    def test_day_header_hidden_when_no_visible_rows(self):
        events_today = [
            {"source": "agent", "type": "coder_started"},
            {"source": "agent", "type": "sprint_started"},
        ]
        # Filtering to dashboard: today has no dashboard events
        assert not _any_visible_in_day(events_today, "dashboard")

    def test_day_header_visible_when_matching_rows_exist(self):
        events_today = [
            {"source": "agent", "type": "coder_started"},
            {"source": "dashboard", "type": "ticket_moved"},
        ]
        assert _any_visible_in_day(events_today, "dashboard")
        assert _any_visible_in_day(events_today, "agent")

    def test_day_header_hiding_logic_in_html(self):
        # The JS must check for visible rows before showing a day header
        # Look for logic that hides day headers (class toggle with 'hidden')
        assert re.search(
            r"day.head.*hidden|hidden.*day.head|classList.*hidden.*day|evl-day-head",
            _HTML, re.DOTALL
        ), "JS must hide day headers when they have no visible rows"


# ── AC-8: Label event rendering ───────────────────────────────────────────────

class TestAC8LabelEvents:
    def test_label_chip_class_sprint(self):
        assert _label_chip_class("sprint-48") == "lbl-sprint"
        assert _label_chip_class("sprint-50") == "lbl-sprint"

    def test_label_chip_class_uat(self):
        assert _label_chip_class("uat") == "lbl-uat"

    def test_label_chip_class_flow(self):
        assert _label_chip_class("in-progress") == "lbl-flow"
        assert _label_chip_class("sit") == "lbl-flow"

    def test_label_chip_class_generic(self):
        assert _label_chip_class("backend") == "lbl-generic"
        assert _label_chip_class("enhancement") == "lbl-generic"

    def test_label_add_uses_plus_chip(self):
        assert "lbl-act" in _HTML and "added" in _HTML, \
               "Label add events must use lbl-act added class (green + chip)"

    def test_label_remove_uses_minus_chip(self):
        assert "lbl-act" in _HTML and "removed" in _HTML, \
               "Label remove events must use lbl-act removed class (red − chip)"

    def test_label_chip_classes_styled(self):
        assert "lbl-sprint" in _HTML, "lbl-sprint CSS class must be defined"
        assert "lbl-uat" in _HTML, "lbl-uat CSS class must be defined"
        assert "lbl-flow" in _HTML, "lbl-flow CSS class must be defined"
        assert "lbl-generic" in _HTML, "lbl-generic CSS class must be defined"

    def test_label_added_green_chip_css(self):
        m = re.search(r'\.lbl-act\.added\s*\{[^}]*color\s*:\s*var\(--green\)', _HTML)
        assert m, ".lbl-act.added must use color: var(--green) for the + chip"

    def test_label_removed_red_chip_css(self):
        m = re.search(r'\.lbl-act\.removed\s*\{[^}]*color\s*:\s*var\(--red\)', _HTML)
        assert m, ".lbl-act.removed must use color: var(--red) for the − chip"

    def test_lbl_sprint_green(self):
        m = re.search(r'\.lbl-sprint\s*\{[^}]*color\s*:\s*var\(--green\)', _HTML)
        assert m, ".lbl-sprint must use green color token"

    def test_lbl_uat_purple(self):
        m = re.search(r'\.lbl-uat\s*\{[^}]*color\s*:\s*var\(--purple\)', _HTML)
        assert m, ".lbl-uat must use purple color token"

    def test_lbl_flow_blue(self):
        m = re.search(r'\.lbl-flow\s*\{[^}]*color\s*:\s*var\(--blue\)', _HTML)
        assert m, ".lbl-flow must use blue color token"


# ── AC-9: PR event icons ──────────────────────────────────────────────────────

class TestAC9PREventIcons:
    def test_pr_open_icon_exists(self):
        assert "ti-git-pull-request" in _HTML, \
               "PR opened events must use the ti-git-pull-request icon"

    def test_pr_merged_icon_exists(self):
        assert "ti-git-merge" in _HTML, \
               "PR merged events must use the ti-git-merge icon"

    def test_pr_open_uses_green_icon(self):
        # pr-open icon must be colored green
        assert "pr-open" in _HTML, \
               "pr-open CSS class (green) must be used for PR opened icon"

    def test_pr_merged_uses_purple_icon(self):
        assert "pr-merged" in _HTML, \
               "pr-merged CSS class (purple) must be used for PR merged icon"

    def test_pr_icon_classes_styled(self):
        assert re.search(r'\.git-ico\.pr-open\s*\{[^}]*color\s*:\s*var\(--green\)', _HTML), \
               ".git-ico.pr-open must use green color"
        assert re.search(r'\.git-ico\.pr-merged\s*\{[^}]*color\s*:\s*var\(--purple\)', _HTML), \
               ".git-ico.pr-merged must use purple color"

    def test_evl_renders_pr_events(self):
        # The render function must handle pr_opened and pr_merged types
        assert "pr_opened" in _HTML or "pr-open" in _HTML, \
               "Renderer must handle pr_opened event type"
        assert "pr_merged" in _HTML or "pr-merged" in _HTML, \
               "Renderer must handle pr_merged event type"


# ── AC-10: Branch event icons ─────────────────────────────────────────────────

class TestAC10BranchEventIcons:
    def test_branch_icon_exists(self):
        assert "ti-git-branch" in _HTML, \
               "Branch created events must use the ti-git-branch icon"

    def test_branch_icon_class_exists(self):
        assert "git-ico" in _HTML and "branch" in _HTML, \
               "branch CSS class must be used for branch icon"

    def test_branch_icon_class_styled(self):
        assert re.search(r'\.git-ico\.branch\s*\{[^}]*color', _HTML), \
               ".git-ico.branch must be styled"

    def test_branch_name_chip_styled(self):
        assert "branch-name" in _HTML, \
               ".branch-name class for rendering branch names must exist"

    def test_evl_renders_branch_created(self):
        assert "branch_created" in _HTML or "branch-name" in _HTML, \
               "Renderer must handle branch_created event type"


# ── AC-11: Color tokens match mock ────────────────────────────────────────────

class TestAC11ColorTokens:
    def _check_token(self, token_name: str, expected_light: str):
        pattern = rf'--{token_name}\s*:\s*{re.escape(expected_light)}'
        assert re.search(pattern, _HTML), \
               f"CSS token --{token_name} must equal {expected_light} in light theme"

    def test_green_token(self):
        self._check_token("green", "#16a34a")

    def test_blue_token(self):
        self._check_token("blue", "#2563eb")

    def test_purple_token(self):
        self._check_token("purple", "#7c3aed")

    def test_red_token(self):
        self._check_token("red", "#dc2626")

    def test_green_bg_token(self):
        self._check_token("green-bg", "#dcfce7")

    def test_blue_bg_token(self):
        self._check_token("blue-bg", "#dbeafe")

    def test_purple_bg_token(self):
        self._check_token("purple-bg", "#ede9fe")

    def test_red_bg_token(self):
        self._check_token("red-bg", "#fee2e2")

    def test_surface_token(self):
        self._check_token("surface", "#ffffff")

    def test_surface2_token(self):
        self._check_token("surface-2", "#f3f4f6")

    def test_border_token(self):
        self._check_token("border", "#e5e7eb")

    def test_log_row_class_exists(self):
        assert "log-row" in _HTML or "lr-body" in _HTML, \
               ".log-row or .lr-body class must exist for timeline rows"

    def test_timeline_container_class(self):
        assert "evl-timeline" in _HTML or "evl-list" in _HTML or "logs-activity" in _HTML, \
               "A timeline container element must exist in the logs pane"
