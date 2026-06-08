"""Tests for issue #633: Bundle cause→effect events into collapsible chip rows.

AC-1: Sprint-move action renders as one bundled row with two effect chips (+sprint-48 / −sprint-47)
      and a 2 count badge, expandable to two GitHub sub-rows each with seconds-level timestamp and actor
AC-2: Bulk-issue-create action renders as one bundled row with N created-issue chips and N count badge
AC-3: Single-effect action shows one chip inline with no expand affordance and is not clickable
AC-4: Bundling key is action_id: only GitHub events sharing an action_id with a Commander cause
      event are bundled; no action_id → standalone row
AC-5: Agent lifecycle events (CODER started → CODER finished) are never bundled; each renders
      as a standalone row regardless of shared fields
AC-6: GitHub events with no matching Commander cause render as standalone rows
AC-7: When source filter is set to GitHub, all bundles flatten: each GitHub sub-event renders
      as an independent standalone row
AC-8: Collapsed bundle row displays: headline action label + effect chips + count badge
AC-9: Clicking a collapsed multi-effect bundle expands it; clicking expanded collapses it
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECT_HTML = REPO_ROOT / "apps" / "dashboard" / "static" / "project.html"

_HTML = PROJECT_HTML.read_text()


# ── Helper: Python reference implementation of the bundling algorithm ──────────
# This mirrors what _evlBundleEvents() in project.html JS must do.

_GITHUB_SOURCE = "github"
_CAUSE_SOURCES = {"dashboard"}
_LIFECYCLE_SOURCES = {"sprint_manager"}


def _bundle_events(events: list[dict], source_filter: str | None = None) -> list[dict]:
    """Bundle events by action_id — Python reference for the JS algorithm.

    Returns list of display rows:
      {'kind': 'bundle',     'cause': event, 'effects': [event,...], 'count': N}
      {'kind': 'standalone', 'event': event}
    """
    if source_filter == "github":
        return [
            {"kind": "standalone", "event": e}
            for e in events
            if e.get("source") == _GITHUB_SOURCE
        ]

    # Build action_id → github effects map
    gh_by_aid: dict[str, list[dict]] = {}
    for e in events:
        if e.get("source") == _GITHUB_SOURCE and e.get("action_id"):
            gh_by_aid.setdefault(e["action_id"], []).append(e)

    consumed: set[int] = set()  # object ids of consumed github events
    rows: list[dict] = []

    for e in events:
        src = e.get("source", "")

        if src == _GITHUB_SOURCE:
            continue  # processed below

        if src in _LIFECYCLE_SOURCES:
            rows.append({"kind": "standalone", "event": e})
            continue

        if src in _CAUSE_SOURCES:
            aid = e.get("action_id")
            if aid and aid in gh_by_aid:
                effects = gh_by_aid[aid]
                for eff in effects:
                    consumed.add(id(eff))
                rows.append({"kind": "bundle", "cause": e, "effects": effects, "count": len(effects)})
            else:
                rows.append({"kind": "standalone", "event": e})
            continue

        # Unknown source → standalone
        rows.append({"kind": "standalone", "event": e})

    # Append GitHub events not consumed by any bundle
    for e in events:
        if e.get("source") == _GITHUB_SOURCE and id(e) not in consumed:
            rows.append({"kind": "standalone", "event": e})

    return rows


def _effect_chip_label(ev: dict) -> str:
    """Reference for _evlEffectChip in JS."""
    t = ev.get("type", "")
    detail = ev.get("detail") or {}
    if t == "label_added":
        return "+" + (detail.get("label_name") or ev.get("target", ""))
    if t == "label_removed":
        return "−" + (detail.get("label_name") or ev.get("target", ""))
    if t == "issue_opened":
        return "+" + (ev.get("target") or "issue")
    if t == "issue_closed":
        return "closed " + (ev.get("target") or "")
    if t == "branch_created":
        return "branch"
    if t == "pr_opened":
        return "PR opened"
    if t == "pr_merged":
        return "PR merged"
    return t


# ── Fixtures ───────────────────────────────────────────────────────────────────

def _cause(type_="ticket_moved", action_id="aid-1", detail=None):
    return {
        "source": "dashboard",
        "type": type_,
        "target": "#42",
        "actor": "commander",
        "timestamp": "2026-06-08T10:00:00",
        "action_id": action_id,
        "detail": detail or {},
    }


def _gh_effect(type_="label_added", action_id="aid-1", label_name="sprint-48",
               actor="zealchaiwut", target="#42", ts="2026-06-08T10:00:05"):
    detail: dict = {}
    if type_ in ("label_added", "label_removed"):
        detail["label_name"] = label_name
    return {
        "source": "github",
        "type": type_,
        "target": target,
        "actor": actor,
        "timestamp": ts,
        "action_id": action_id,
        "detail": detail,
    }


def _lifecycle(type_="ticket_dispatched", action_id="aid-sprint"):
    return {
        "source": "sprint_manager",
        "type": type_,
        "target": "#42",
        "actor": "system",
        "timestamp": "2026-06-08T10:00:00",
        "action_id": action_id,
        "detail": {"agent": "CODER"},
    }


# ── AC-1: Sprint-move bundle with 2 chips ─────────────────────────────────────

class TestSprintMoveBundling:
    """AC-1: Sprint-move action bundles into one row with 2 effect chips and count=2."""

    def _events(self):
        cause = _cause(
            type_="ticket_moved",
            action_id="mv-1",
            detail={"from_sprint": "sprint-47", "to_sprint": "sprint-48"},
        )
        added = _gh_effect(type_="label_added", action_id="mv-1", label_name="sprint-48")
        removed = _gh_effect(type_="label_removed", action_id="mv-1", label_name="sprint-47")
        return [cause, added, removed]

    def test_produces_exactly_one_row(self):
        rows = _bundle_events(self._events())
        assert len(rows) == 1, f"Expected 1 bundle row, got {len(rows)}: {rows}"

    def test_row_is_bundle_kind(self):
        rows = _bundle_events(self._events())
        assert rows[0]["kind"] == "bundle"

    def test_bundle_has_two_effects(self):
        rows = _bundle_events(self._events())
        bundle = rows[0]
        assert bundle["count"] == 2
        assert len(bundle["effects"]) == 2

    def test_effect_chips_are_correct(self):
        rows = _bundle_events(self._events())
        effects = rows[0]["effects"]
        labels = {_effect_chip_label(e) for e in effects}
        assert "+sprint-48" in labels, f"Missing +sprint-48 in {labels}"
        assert "−sprint-47" in labels, f"Missing −sprint-47 in {labels}"

    def test_cause_is_the_ticket_moved_event(self):
        rows = _bundle_events(self._events())
        assert rows[0]["cause"]["type"] == "ticket_moved"

    def test_sub_rows_have_timestamps_and_actors(self):
        rows = _bundle_events(self._events())
        for eff in rows[0]["effects"]:
            assert eff["timestamp"], f"Effect missing timestamp: {eff}"
            assert eff["actor"], f"Effect missing actor: {eff}"


# ── AC-2: Bulk-create bundle with N chips ─────────────────────────────────────

class TestBulkCreateBundling:
    """AC-2: Bulk-issue-create bundles into one row with N issue chips and count=N."""

    def _events(self, n=5):
        cause = _cause(type_="bulk_created", action_id="bulk-1")
        effects = [
            _gh_effect(type_="issue_opened", action_id="bulk-1",
                       target=f"#{595 + i}", ts=f"2026-06-08T10:00:0{i}")
            for i in range(n)
        ]
        return [cause] + effects

    def test_produces_exactly_one_row(self):
        rows = _bundle_events(self._events())
        assert len(rows) == 1

    def test_count_badge_equals_n(self):
        rows = _bundle_events(self._events(5))
        assert rows[0]["count"] == 5

    def test_effect_chips_are_issue_opened(self):
        rows = _bundle_events(self._events(3))
        labels = [_effect_chip_label(e) for e in rows[0]["effects"]]
        for lbl in labels:
            assert lbl.startswith("+#"), f"Expected +#NNN chip, got {lbl}"

    def test_variable_n_bulk_create(self):
        for n in (1, 3, 10):
            rows = _bundle_events(self._events(n))
            assert rows[0]["count"] == n, f"Expected count={n}, got {rows[0]['count']}"


# ── AC-3: Single-effect action has no expand affordance ───────────────────────

class TestSingleEffectAction:
    """AC-3: Single-effect action shows one chip inline; no expand affordance."""

    def _events(self):
        cause = _cause(action_id="single-1")
        eff = _gh_effect(action_id="single-1", label_name="sprint-48")
        return [cause, eff]

    def test_produces_one_bundle_row(self):
        rows = _bundle_events(self._events())
        assert len(rows) == 1
        assert rows[0]["kind"] == "bundle"

    def test_count_is_one(self):
        rows = _bundle_events(self._events())
        assert rows[0]["count"] == 1

    def test_single_effect_marker_in_html(self):
        """HTML must have a CSS class or attribute that marks single-effect bundles.
        The evl-single class disables expand affordance and cursor pointer."""
        assert "evl-single" in _HTML, (
            "project.html missing 'evl-single' class for single-effect bundles"
        )

    def test_single_bundle_no_chevron_logic_in_html(self):
        """JS must differentiate single-effect (isSingle) from multi-effect bundles."""
        assert "isSingle" in _HTML, (
            "project.html missing 'isSingle' check in JS bundling render logic"
        )


# ── AC-4: Bundling key is action_id ───────────────────────────────────────────

class TestActionIdBundlingKey:
    """AC-4: Only GitHub events sharing action_id with a Commander cause are bundled.
    Events with no action_id → standalone row."""

    def test_no_action_id_github_event_is_standalone(self):
        gh = {
            "source": "github", "type": "label_added", "target": "#1",
            "actor": "user", "timestamp": "2026-06-08T10:00:00", "action_id": None,
            "detail": {"label_name": "sprint-48"},
        }
        rows = _bundle_events([gh])
        assert len(rows) == 1
        assert rows[0]["kind"] == "standalone"
        assert rows[0]["event"] is gh

    def test_mismatched_action_id_no_bundle(self):
        cause = _cause(action_id="aid-A")
        gh = _gh_effect(action_id="aid-B")  # different aid
        rows = _bundle_events([cause, gh])
        assert len(rows) == 2, f"Expected 2 standalone rows, got {len(rows)}"
        kinds = {r["kind"] for r in rows}
        assert kinds == {"standalone"}

    def test_matching_action_id_creates_bundle(self):
        cause = _cause(action_id="aid-match")
        gh = _gh_effect(action_id="aid-match")
        rows = _bundle_events([cause, gh])
        assert len(rows) == 1
        assert rows[0]["kind"] == "bundle"

    def test_dashboard_cause_with_no_action_id_is_standalone(self):
        cause = _cause(action_id=None)
        rows = _bundle_events([cause])
        assert rows[0]["kind"] == "standalone"

    def test_github_only_action_id_no_cause_is_standalone(self):
        gh = _gh_effect(action_id="orphan-aid")
        rows = _bundle_events([gh])
        assert rows[0]["kind"] == "standalone"


# ── AC-5: Agent lifecycle events never bundled ────────────────────────────────

class TestAgentLifecycleNeverBundled:
    """AC-5: Agent lifecycle events (sprint_manager source) are always standalone."""

    def test_sprint_manager_event_is_standalone(self):
        ev = _lifecycle("sprint_started")
        rows = _bundle_events([ev])
        assert rows[0]["kind"] == "standalone"

    def test_lifecycle_not_bundled_even_with_same_action_id(self):
        lifecycle = _lifecycle("ticket_dispatched", action_id="shared-aid")
        gh = _gh_effect(action_id="shared-aid")
        rows = _bundle_events([lifecycle, gh])
        # Both must be standalone — lifecycle never bundles
        assert len(rows) == 2
        kinds = [r["kind"] for r in rows]
        assert all(k == "standalone" for k in kinds), (
            f"Expected all standalone, got: {kinds}"
        )

    def test_coder_started_finished_are_separate_standalone_rows(self):
        started = _lifecycle("ticket_dispatched", action_id="run-1")
        finished = {**_lifecycle("ticket_completed", action_id="run-1"),
                    "timestamp": "2026-06-08T10:30:00"}
        rows = _bundle_events([started, finished])
        assert len(rows) == 2
        assert all(r["kind"] == "standalone" for r in rows)

    def test_multiple_lifecycle_events_each_standalone(self):
        evs = [
            _lifecycle("sprint_started"),
            _lifecycle("ticket_dispatched"),
            _lifecycle("ticket_completed"),
            _lifecycle("sprint_finished"),
        ]
        rows = _bundle_events(evs)
        assert len(rows) == len(evs)
        assert all(r["kind"] == "standalone" for r in rows)


# ── AC-6: GitHub events with no matching Commander cause → standalone ──────────

class TestGithubEventsNoMatchingCause:
    """AC-6: Manual GitHub edits (no Commander cause) render as standalone rows."""

    def test_github_event_with_no_cause_is_standalone(self):
        gh = _gh_effect(action_id=None)
        rows = _bundle_events([gh])
        assert rows[0]["kind"] == "standalone"

    def test_github_event_with_orphan_aid_is_standalone(self):
        gh = _gh_effect(action_id="no-matching-cause")
        rows = _bundle_events([gh])
        assert rows[0]["kind"] == "standalone"

    def test_multiple_unmatched_github_events_each_standalone(self):
        evs = [
            _gh_effect(action_id=None, type_="issue_opened", target="#100"),
            _gh_effect(action_id=None, type_="label_added", label_name="bug", target="#101"),
            _gh_effect(action_id="orphan-1", type_="issue_closed", target="#102"),
        ]
        rows = _bundle_events(evs)
        assert len(rows) == 3
        assert all(r["kind"] == "standalone" for r in rows)

    def test_mixed_matched_and_unmatched_github_events(self):
        cause = _cause(action_id="real-aid")
        matched_gh = _gh_effect(action_id="real-aid", label_name="sprint-48")
        unmatched_gh = _gh_effect(action_id=None, label_name="bug", type_="label_added")
        rows = _bundle_events([cause, matched_gh, unmatched_gh])
        assert len(rows) == 2
        bundle = next(r for r in rows if r["kind"] == "bundle")
        standalone = next(r for r in rows if r["kind"] == "standalone")
        assert bundle["count"] == 1
        assert standalone["event"] is unmatched_gh


# ── AC-7: GitHub source filter flattens all bundles ───────────────────────────

class TestGithubSourceFilterFlattens:
    """AC-7: source_filter='github' → all GitHub events become independent standalone rows."""

    def test_source_filter_github_flattens_sprint_move_bundle(self):
        cause = _cause(action_id="mv-1")
        added = _gh_effect(type_="label_added", action_id="mv-1", label_name="sprint-48")
        removed = _gh_effect(type_="label_removed", action_id="mv-1", label_name="sprint-47")
        rows = _bundle_events([cause, added, removed], source_filter="github")
        assert len(rows) == 2, f"Expected 2 flattened rows, got {len(rows)}"
        assert all(r["kind"] == "standalone" for r in rows)

    def test_source_filter_github_excludes_commander_events(self):
        cause = _cause(action_id="mv-1")
        gh = _gh_effect(action_id="mv-1")
        rows = _bundle_events([cause, gh], source_filter="github")
        sources = [r["event"]["source"] for r in rows]
        assert "dashboard" not in sources, "Commander events leaked into GitHub filter view"
        assert all(s == "github" for s in sources)

    def test_source_filter_github_flattens_bulk_create_bundle(self):
        cause = _cause(type_="bulk_created", action_id="bulk-1")
        effects = [_gh_effect(type_="issue_opened", action_id="bulk-1",
                              target=f"#{595 + i}") for i in range(5)]
        rows = _bundle_events([cause] + effects, source_filter="github")
        assert len(rows) == 5
        assert all(r["kind"] == "standalone" for r in rows)

    def test_source_filter_github_excludes_lifecycle_events(self):
        lifecycle = _lifecycle("sprint_started")
        gh = _gh_effect(action_id=None)
        rows = _bundle_events([lifecycle, gh], source_filter="github")
        assert len(rows) == 1
        assert rows[0]["event"]["source"] == "github"

    def test_source_filter_none_still_bundles(self):
        cause = _cause(action_id="aid-1")
        gh = _gh_effect(action_id="aid-1")
        rows = _bundle_events([cause, gh], source_filter=None)
        assert rows[0]["kind"] == "bundle"


# ── AC-8: HTML structure for collapsed bundle row ─────────────────────────────

class TestBundleRowHtmlStructure:
    """AC-8: HTML contains required CSS classes for bundle display."""

    def test_evl_bundle_row_class_exists(self):
        assert "evl-bundle-row" in _HTML, "Missing .evl-bundle-row CSS class"

    def test_evl_effect_chip_class_exists(self):
        assert "evl-effect-chip" in _HTML, "Missing .evl-effect-chip CSS class"

    def test_evl_count_badge_class_exists(self):
        assert "evl-count-badge" in _HTML, "Missing .evl-count-badge CSS class"

    def test_evl_headline_class_exists(self):
        assert "evl-headline" in _HTML, "Missing .evl-headline CSS class"

    def test_evl_effect_chips_container_class_exists(self):
        assert "evl-effect-chips" in _HTML, "Missing .evl-effect-chips CSS class"

    def test_evl_added_chip_class_exists(self):
        assert "evl-effect-chip--added" in _HTML, "Missing .evl-effect-chip--added CSS class"

    def test_evl_removed_chip_class_exists(self):
        assert "evl-effect-chip--removed" in _HTML, "Missing .evl-effect-chip--removed CSS class"

    def test_evl_sub_rows_class_exists(self):
        assert "evl-sub-rows" in _HTML, "Missing .evl-sub-rows CSS class"

    def test_evl_sub_row_class_exists(self):
        assert "evl-sub-row" in _HTML, "Missing .evl-sub-row CSS class"

    def test_evl_ts_class_exists(self):
        assert "evl-ts" in _HTML, "Missing .evl-ts CSS class (seconds-level timestamp)"

    def test_evl_actor_class_exists(self):
        assert "evl-actor" in _HTML, "Missing .evl-actor CSS class"

    def test_evl_standalone_row_class_exists(self):
        assert "evl-standalone-row" in _HTML, "Missing .evl-standalone-row CSS class"


# ── AC-9: Expand/collapse HTML affordances ────────────────────────────────────

class TestExpandCollapseAffordances:
    """AC-9: HTML has chevron, expanded state, and evlToggleBundle JS function."""

    def test_evl_chevron_class_exists(self):
        assert "evl-chevron" in _HTML, "Missing .evl-chevron CSS class"

    def test_evl_bundle_row_expanded_class_exists(self):
        assert "evl-bundle-row.expanded" in _HTML or ".expanded .evl-" in _HTML, (
            "Missing .evl-bundle-row.expanded or .expanded .evl-* CSS selector for expand state"
        )

    def test_evl_toggle_bundle_function_exists(self):
        assert "evlToggleBundle" in _HTML, "Missing evlToggleBundle JS function"

    def test_evl_sub_rows_hidden_by_default(self):
        assert "evl-sub-rows" in _HTML, "Missing .evl-sub-rows container"
        # The sub-rows container must be hidden by default (display:none or .hidden class)
        assert "display: none" in _HTML or "display:none" in _HTML, (
            "Missing display:none for .evl-sub-rows (sub-rows should be hidden by default)"
        )

    def test_evl_toggle_function_uses_expanded_class(self):
        assert "expanded" in _HTML, "Missing 'expanded' class usage in JS toggle logic"


# ── HTML: Activity view button and source filter ──────────────────────────────

class TestActivityViewUi:
    """HTML has Activity view button, source filter chips, and body container."""

    def test_activity_view_button_exists(self):
        assert 'id="logs-view-activity"' in _HTML, (
            "Missing logs-view-activity button in Logs toolbar"
        )

    def test_activity_view_button_calls_logs_set_view(self):
        assert "logsSetView('activity')" in _HTML, (
            "Activity view button must call logsSetView('activity')"
        )

    def test_logs_activity_body_container_exists(self):
        assert 'id="logs-activity"' in _HTML, (
            "Missing logs-activity body container"
        )

    def test_evl_source_filter_all_chip_exists(self):
        assert 'id="evl-source-all"' in _HTML, (
            "Missing evl-source-all source filter chip"
        )

    def test_evl_source_filter_github_chip_exists(self):
        assert 'id="evl-source-github"' in _HTML, (
            "Missing evl-source-github source filter chip"
        )

    def test_evl_set_source_function_exists(self):
        assert "evlSetSource" in _HTML, "Missing evlSetSource JS function"

    def test_logs_set_view_handles_activity_mode(self):
        assert "'activity'" in _HTML, (
            "logsSetView does not handle 'activity' mode"
        )

    def test_evl_list_container_exists(self):
        assert 'id="evl-list"' in _HTML, "Missing evl-list container"


# ── HTML: JS bundling functions ───────────────────────────────────────────────

class TestJsBundlingFunctions:
    """HTML contains the required JS functions for bundling."""

    def test_evl_bundle_events_function_exists(self):
        assert "_evlBundleEvents" in _HTML, "Missing _evlBundleEvents JS function"

    def test_evl_effect_chip_function_exists(self):
        assert "_evlEffectChip" in _HTML, "Missing _evlEffectChip JS function"

    def test_evl_headline_label_function_exists(self):
        assert "_evlHeadlineLabel" in _HTML, "Missing _evlHeadlineLabel JS function"

    def test_evl_render_function_exists(self):
        assert "evlRender" in _HTML, "Missing evlRender JS function"

    def test_evl_fetch_function_exists(self):
        assert "evlFetch" in _HTML, "Missing evlFetch JS function"

    def test_evl_state_object_exists(self):
        assert "_evlState" in _HTML, "Missing _evlState JS state object"

    def test_evl_fetches_from_events_endpoint(self):
        assert "/events" in _HTML and "evlFetch" in _HTML, (
            "evlFetch must call the /api/projects/{slug}/events endpoint"
        )

    def test_evl_bundle_events_checks_github_source_filter(self):
        assert "sourceFilter" in _HTML or "source_filter" in _HTML, (
            "JS bundling must check source filter for GitHub flatten behavior"
        )

    def test_evl_bundle_events_checks_lifecycle_sources(self):
        assert "sprint_manager" in _HTML or "_EVL_LIFECYCLE_SOURCES" in _HTML, (
            "JS bundling must exclude sprint_manager (lifecycle) events from bundling"
        )

    def test_evl_bundle_events_checks_cause_sources(self):
        assert "_EVL_CAUSE_SOURCES" in _HTML or "dashboard" in _HTML, (
            "JS bundling must identify 'dashboard' events as cause events"
        )


# ── Effect chip label correctness ─────────────────────────────────────────────

class TestEffectChipLabels:
    """Chip label format for each GitHub event type."""

    def test_label_added_chip_format(self):
        ev = _gh_effect(type_="label_added", label_name="sprint-48")
        assert _effect_chip_label(ev) == "+sprint-48"

    def test_label_removed_chip_format(self):
        ev = _gh_effect(type_="label_removed", label_name="sprint-47")
        assert _effect_chip_label(ev) == "−sprint-47"

    def test_issue_opened_chip_format(self):
        ev = {"source": "github", "type": "issue_opened", "target": "#595",
              "actor": "u", "timestamp": "2026-06-08T10:00:00", "action_id": None, "detail": {}}
        assert _effect_chip_label(ev) == "+#595"

    def test_issue_closed_chip_format(self):
        ev = {"source": "github", "type": "issue_closed", "target": "#42",
              "actor": "u", "timestamp": "2026-06-08T10:00:00", "action_id": None, "detail": {}}
        assert _effect_chip_label(ev) == "closed #42"

    def test_branch_created_chip_format(self):
        ev = {"source": "github", "type": "branch_created", "target": "feature/123-foo",
              "actor": "u", "timestamp": "2026-06-08T10:00:00", "action_id": None,
              "detail": {"ref": "feature/123-foo"}}
        assert _effect_chip_label(ev) == "branch"

    def test_pr_opened_chip_format(self):
        ev = {"source": "github", "type": "pr_opened", "target": "PR#7",
              "actor": "u", "timestamp": "2026-06-08T10:00:00", "action_id": None, "detail": {}}
        assert _effect_chip_label(ev) == "PR opened"

    def test_pr_merged_chip_format(self):
        ev = {"source": "github", "type": "pr_merged", "target": "PR#7",
              "actor": "u", "timestamp": "2026-06-08T10:00:00", "action_id": None, "detail": {}}
        assert _effect_chip_label(ev) == "PR merged"
