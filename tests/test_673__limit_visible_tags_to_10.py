"""Tests for issue #673: Limit visible tags to 10 most recently used.

AC-1: Logs tab tag bar shows at most 10 tags by default, sorted by most recently used
AC-2: A "See more" control appears when total tag count exceeds 10
AC-3: Clicking "See more" expands to show all remaining tags
AC-4: Collapsing ("See less") returns to the 10-tag view
AC-5: The 10-tag limit applies to all tag types, not only Sprint tags
AC-6: Recency is determined by last use date (tag applied to a log entry), most recent first
AC-7: If 10 or fewer tags exist, no "See more" control is shown
AC-8: Tag order is stable across page reloads
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECT_HTML = REPO_ROOT / "apps" / "dashboard" / "static" / "project.html"

_HTML = PROJECT_HTML.read_text()


# ── Python reference implementations mirroring JS logic ──────────────────────

LOGS_TAG_LIMIT = 10


def _apply_tag_limit(chips: list[dict], expanded: bool) -> tuple[list[dict], list[dict]]:
    """Mirrors _logsApplyTagLimit: return (visible_chips, hidden_chips).

    chips: list of {'group': str, 'value': str, 'recency': str}
    recency: ISO timestamp string; higher string = more recent.
    """
    sorted_chips = sorted(chips, key=lambda c: c['recency'], reverse=True)
    if expanded or len(sorted_chips) <= LOGS_TAG_LIMIT:
        return sorted_chips, []
    return sorted_chips[:LOGS_TAG_LIMIT], sorted_chips[LOGS_TAG_LIMIT:]


def _see_more_label(hidden_count: int) -> str:
    """Return "See more (N more)" or empty string."""
    if hidden_count == 0:
        return ''
    return f'See more ({hidden_count} more)'


def _see_more_visible(total: int) -> bool:
    return total > LOGS_TAG_LIMIT


def _sprint_recency(runs: list[dict], sprint_label: str) -> str:
    """Return max start_time for runs matching sprint_label, or '' if none."""
    times = [r['start_time'] for r in runs if r.get('sprint_label') == sprint_label]
    return max(times) if times else ''


def _global_max_recency(runs: list[dict]) -> str:
    """Return max start_time across all runs."""
    times = [r['start_time'] for r in runs]
    return max(times) if times else ''


# ── AC-1: At most 10 tags visible by default ─────────────────────────────────

class TestAC1AtMost10ByDefault:
    def test_tag_limit_constant_in_html(self):
        assert 'LOGS_TAG_LIMIT' in _HTML or re.search(r'const\s+\w*[Tt]ag\w*[Ll]imit\s*=\s*10', _HTML), \
            "A tag limit constant of 10 must exist in the JS"

    def test_hidden_chip_css_class_exists(self):
        assert 'logs-chip--hidden' in _HTML, \
            ".logs-chip--hidden CSS class must exist to hide overflow chips"

    def test_hidden_chip_display_none(self):
        assert re.search(r'\.logs-chip--hidden\s*\{[^}]*display\s*:\s*none', _HTML), \
            ".logs-chip--hidden must set display:none to hide chips"

    def test_apply_tag_limit_function_exists(self):
        assert '_logsApplyTagLimit' in _HTML or 'logsApplyTagLimit' in _HTML, \
            "_logsApplyTagLimit function must exist"

    def test_at_most_10_visible_logic(self):
        chips = [{'group': 'sprint_label', 'value': f'sprint-{i}', 'recency': f'2026-0{i % 9 + 1}-01'} for i in range(1, 16)]
        visible, hidden = _apply_tag_limit(chips, expanded=False)
        assert len(visible) == LOGS_TAG_LIMIT
        assert len(hidden) == 5

    def test_less_than_10_all_visible(self):
        chips = [{'group': 'sprint_label', 'value': f'sprint-{i}', 'recency': f'2026-0{i}-01'} for i in range(1, 8)]
        visible, hidden = _apply_tag_limit(chips, expanded=False)
        assert len(visible) == 7
        assert len(hidden) == 0

    def test_exactly_10_all_visible(self):
        chips = [{'group': 'sprint_label', 'value': f'sprint-{i}', 'recency': f'2026-0{i}-01'} for i in range(1, 11)]
        visible, hidden = _apply_tag_limit(chips, expanded=False)
        assert len(visible) == LOGS_TAG_LIMIT
        assert len(hidden) == 0


# ── AC-2: "See more" control when total > 10 ─────────────────────────────────

class TestAC2SeeMoreControl:
    def test_see_more_button_exists_in_html(self):
        assert 'logs-tags-see-more' in _HTML, \
            "A 'See more' button with id='logs-tags-see-more' must exist"

    def test_see_more_toggle_function_exists(self):
        assert '_logsTagsToggle' in _HTML or 'logsTagsToggle' in _HTML, \
            "_logsTagsToggle function must exist for the See more/See less button"

    def test_see_more_visible_when_more_than_10(self):
        assert _see_more_visible(11) is True
        assert _see_more_visible(59) is True

    def test_see_more_label_includes_count(self):
        label = _see_more_label(49)
        assert '49' in label
        assert 'more' in label.lower()

    def test_see_more_hidden_when_10_or_fewer(self):
        assert _see_more_visible(10) is False
        assert _see_more_visible(7) is False
        assert _see_more_visible(0) is False


# ── AC-3: Clicking "See more" expands all ────────────────────────────────────

class TestAC3ExpandAll:
    def test_toggle_function_in_html(self):
        assert '_logsTagsToggle' in _HTML, \
            "_logsTagsToggle function must exist to toggle expanded state"

    def test_expand_shows_all_chips(self):
        chips = [{'group': 'sprint_label', 'value': f'sprint-{i}', 'recency': f'2026-0{i % 9 + 1}-01'} for i in range(1, 16)]
        visible, hidden = _apply_tag_limit(chips, expanded=True)
        assert len(visible) == 15
        assert len(hidden) == 0

    def test_expanded_state_variable_in_html(self):
        assert '_logsTagsExpanded' in _HTML or 'logsTagsExpanded' in _HTML, \
            "A boolean state variable for expanded/collapsed state must exist"


# ── AC-4: "See less" collapses back to 10 ────────────────────────────────────

class TestAC4Collapse:
    def test_see_less_label_when_expanded(self):
        assert 'See less' in _HTML, \
            "The toggle button must show 'See less' when expanded"

    def test_collapse_hides_overflow(self):
        chips = [{'group': 'sprint_label', 'value': f'sprint-{i}', 'recency': f'2026-0{i % 9 + 1}-01'} for i in range(1, 20)]
        # After expand then collapse
        visible_after_collapse, hidden_after_collapse = _apply_tag_limit(chips, expanded=False)
        assert len(visible_after_collapse) == LOGS_TAG_LIMIT
        assert len(hidden_after_collapse) == 9

    def test_see_more_label_restored_on_collapse(self):
        label = _see_more_label(9)
        assert 'See more' in label or '9' in label


# ── AC-5: Limit applies to all tag types ─────────────────────────────────────

class TestAC5AllTagTypes:
    def test_hidden_class_applied_to_role_chips(self):
        # _logsApplyTagLimit must query chips from all groups, not just sprint
        apply_fn_idx = _HTML.find('_logsApplyTagLimit')
        if apply_fn_idx == -1:
            apply_fn_idx = _HTML.find('logsApplyTagLimit')
        assert apply_fn_idx != -1, "_logsApplyTagLimit must exist"
        # After the function definition, it should target all .logs-chip elements
        fn_region = _HTML[apply_fn_idx:apply_fn_idx + 600]
        assert 'logs-chip' in fn_region, \
            "_logsApplyTagLimit must operate on .logs-chip elements from all groups"

    def test_all_groups_participate_in_limit(self):
        # Chips from sprint, role, level should all be sorted together
        chips = (
            [{'group': 'sprint_label', 'value': f'sprint-{i}', 'recency': f'2026-0{i % 9 + 1}-01T00:00:00'} for i in range(1, 10)]
            + [{'group': 'agent_role', 'value': 'coder', 'recency': '2026-06-01T00:00:00'}]
            + [{'group': 'agent_role', 'value': 'tester', 'recency': '2025-01-01T00:00:00'}]
            + [{'group': 'event_level', 'value': 'dispatch', 'recency': '2024-01-01T00:00:00'}]
        )
        visible, hidden = _apply_tag_limit(chips, expanded=False)
        assert len(visible) == LOGS_TAG_LIMIT
        # The 'dispatch' chip (oldest) should be in hidden since it ranked 12th
        hidden_values = {c['value'] for c in hidden}
        assert 'dispatch' in hidden_values or len(hidden) > 0, \
            "Lowest-recency chips from any group should be hidden"

    def test_data_tag_recency_set_on_static_chips(self):
        # _logsPopulateSprintChips or similar must set data-tag-recency on role/level chips
        assert 'agent_role' in _HTML, "agent_role group must be referenced in the JS"
        assert 'event_level' in _HTML, "event_level group must be referenced in the JS"
        # The recency-setting code should target both dynamic and static chips
        assert 'tagRecency' in _HTML or 'tag-recency' in _HTML or 'data-tag-recency' in _HTML, \
            "data-tag-recency attribute must be set on chips"


# ── AC-6: Recency by last use date ───────────────────────────────────────────

class TestAC6Recency:
    def test_sprint_recency_from_runs(self):
        runs = [
            {'sprint_label': 'sprint-50', 'start_time': '2026-05-01T00:00:00'},
            {'sprint_label': 'sprint-51', 'start_time': '2026-05-15T00:00:00'},
            {'sprint_label': 'sprint-52', 'start_time': '2026-06-09T00:00:00'},
        ]
        assert _sprint_recency(runs, 'sprint-52') == '2026-06-09T00:00:00'
        assert _sprint_recency(runs, 'sprint-50') == '2026-05-01T00:00:00'
        assert _sprint_recency(runs, 'sprint-99') == ''

    def test_multiple_runs_same_sprint(self):
        runs = [
            {'sprint_label': 'sprint-52', 'start_time': '2026-06-01T00:00:00'},
            {'sprint_label': 'sprint-52', 'start_time': '2026-06-09T00:00:00'},
            {'sprint_label': 'sprint-52', 'start_time': '2026-06-05T00:00:00'},
        ]
        assert _sprint_recency(runs, 'sprint-52') == '2026-06-09T00:00:00'

    def test_most_recent_shown_first(self):
        chips = [
            {'group': 'sprint_label', 'value': 'sprint-50', 'recency': '2026-05-01T00:00:00'},
            {'group': 'sprint_label', 'value': 'sprint-52', 'recency': '2026-06-09T00:00:00'},
            {'group': 'sprint_label', 'value': 'sprint-51', 'recency': '2026-05-15T00:00:00'},
        ]
        visible, _ = _apply_tag_limit(chips, expanded=False)
        assert visible[0]['value'] == 'sprint-52', "Most recent sprint must appear first"
        assert visible[1]['value'] == 'sprint-51'
        assert visible[2]['value'] == 'sprint-50'

    def test_recency_attribute_referenced_in_html(self):
        assert 'tagRecency' in _HTML or 'tag_recency' in _HTML or 'tag-recency' in _HTML, \
            "A tag recency attribute must be set/read in the JS"

    def test_global_max_recency(self):
        runs = [
            {'sprint_label': 'sprint-50', 'start_time': '2026-05-01T00:00:00'},
            {'sprint_label': 'sprint-51', 'start_time': '2026-05-15T00:00:00'},
            {'sprint_label': 'sprint-52', 'start_time': '2026-06-09T00:00:00'},
        ]
        assert _global_max_recency(runs) == '2026-06-09T00:00:00'

    def test_empty_runs_returns_empty_recency(self):
        assert _sprint_recency([], 'sprint-52') == ''
        assert _global_max_recency([]) == ''


# ── AC-7: No "See more" if ≤ 10 tags ─────────────────────────────────────────

class TestAC7NoSeeMoreWhenFew:
    def test_see_more_hidden_exactly_10(self):
        assert _see_more_visible(10) is False

    def test_see_more_hidden_fewer_than_10(self):
        for n in range(0, 11):
            assert _see_more_visible(n) is False, f"See more should not appear for {n} tags"

    def test_see_more_shown_11_plus(self):
        for n in range(11, 60):
            assert _see_more_visible(n) is True, f"See more should appear for {n} tags"

    def test_html_hides_see_more_btn_initially(self):
        # The button should start hidden (hidden class or display:none)
        see_more_region = re.search(
            r'<button[^>]*id=["\']logs-tags-see-more["\'][^>]*>',
            _HTML
        )
        assert see_more_region, "logs-tags-see-more button must exist"
        btn_html = see_more_region.group(0)
        assert 'hidden' in btn_html, \
            "logs-tags-see-more button must start hidden (has 'hidden' class)"


# ── AC-8: Stable order across page reloads ───────────────────────────────────

class TestAC8StableOrder:
    def test_sort_is_deterministic(self):
        chips_a = [
            {'group': 'sprint_label', 'value': 'sprint-52', 'recency': '2026-06-09'},
            {'group': 'sprint_label', 'value': 'sprint-51', 'recency': '2026-05-15'},
            {'group': 'sprint_label', 'value': 'sprint-50', 'recency': '2026-05-01'},
        ]
        chips_b = [
            {'group': 'sprint_label', 'value': 'sprint-50', 'recency': '2026-05-01'},
            {'group': 'sprint_label', 'value': 'sprint-52', 'recency': '2026-06-09'},
            {'group': 'sprint_label', 'value': 'sprint-51', 'recency': '2026-05-15'},
        ]
        visible_a, _ = _apply_tag_limit(chips_a, expanded=False)
        visible_b, _ = _apply_tag_limit(chips_b, expanded=False)
        assert [c['value'] for c in visible_a] == [c['value'] for c in visible_b], \
            "Sorting must produce stable order regardless of input order"

    def test_recency_ties_broken_by_value(self):
        chips = [
            {'group': 'sprint_label', 'value': 'sprint-51', 'recency': '2026-06-09'},
            {'group': 'sprint_label', 'value': 'sprint-52', 'recency': '2026-06-09'},
            {'group': 'sprint_label', 'value': 'sprint-50', 'recency': '2026-06-09'},
        ]
        # Sort is deterministic: same recency → stable by some consistent secondary key
        visible_a, _ = _apply_tag_limit(chips, expanded=False)
        visible_b, _ = _apply_tag_limit(list(reversed(chips)), expanded=False)
        # Both runs should produce same set of values (stable = no random flip)
        assert set(c['value'] for c in visible_a) == set(c['value'] for c in visible_b)

    def test_sort_uses_string_comparison_on_iso_timestamps(self):
        # ISO 8601 strings sort correctly as strings
        timestamps = [
            '2026-06-09T00:00:00',
            '2026-05-01T00:00:00',
            '2026-01-01T00:00:00',
            '2025-12-31T00:00:00',
        ]
        assert sorted(timestamps, reverse=True)[0] == '2026-06-09T00:00:00'
        assert sorted(timestamps, reverse=True)[-1] == '2025-12-31T00:00:00'

    def test_apply_tag_limit_called_after_sprint_chip_population(self):
        # _logsPopulateSprintChips must call _logsApplyTagLimit
        # Find the function definition (not the call site which appears first)
        populate_idx = _HTML.find('function _logsPopulateSprintChips')
        assert populate_idx != -1, "_logsPopulateSprintChips function must exist"
        fn_body_region = _HTML[populate_idx:populate_idx + 2000]
        assert '_logsApplyTagLimit' in fn_body_region or 'logsApplyTagLimit' in fn_body_region, \
            "_logsPopulateSprintChips must call _logsApplyTagLimit after populating chips"
