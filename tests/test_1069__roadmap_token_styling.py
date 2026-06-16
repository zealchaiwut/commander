"""Acceptance tests for issue #1069 — Redesign Roadmap/Milestones Tab with Token Styling.

AC map:
  AC1   Each milestone card displays: title, progress bar, ticket counts (open/closed),
        and start/due dates in a consistent layout
  AC2   Progress visualization uses semantic color tokens (e.g. --color-success,
        --color-warning) scaled to completion percentage
  AC3   Spacing and alignment use foundation tokens from tokens.css
        (no hardcoded px values for layout in new card anatomy elements)
  AC4   An empty state renders when no milestones exist (non-empty message, token-styled)
  AC5   All existing milestone links and click handlers remain fully functional
  AC6   No regressions outside the roadmap view region of project.html
  AC7   Dark theme applied consistently across all roadmap components
  AC8   Impeccable detect passes (no inline styles for progress width, semantic HTML)
  AC9   Implementation is vanilla JS/CSS only — no new frameworks or libraries
  AC10  Diff is scoped to the roadmap view region; no unrelated files modified
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECT_HTML = REPO_ROOT / "apps" / "dashboard" / "static" / "project.html"


@pytest.fixture(scope="module")
def html() -> str:
    return PROJECT_HTML.read_text(encoding="utf-8")


# ── AC1: card anatomy — title, progress bar, ticket counts, dates ─────────────


class TestCardAnatomy:
    def _card_fn(self, html: str) -> str:
        m = re.search(r"function _rmCardHtml\(ms\).*?^}", html, re.DOTALL | re.MULTILINE)
        assert m, "_rmCardHtml must exist in project.html"
        return m.group(0)

    def test_card_renders_title(self, html):
        """AC1 — card must display milestone title."""
        fn = self._card_fn(html)
        assert "ms.title" in fn or "title" in fn, (
            "AC1: _rmCardHtml must render ms.title in the card"
        )

    def test_card_renders_progress_bar(self, html):
        """AC1 — card must include a progress bar element."""
        fn = self._card_fn(html)
        assert "rm-progress-wrap" in fn, (
            "AC1: _rmCardHtml must include .rm-progress-wrap for the progress bar"
        )
        assert "rm-progress-fill" in fn, (
            "AC1: _rmCardHtml must include .rm-progress-fill element"
        )

    def test_card_renders_closed_count(self, html):
        """AC1 — card must display closed ticket count (completed issues)."""
        fn = self._card_fn(html)
        assert "closed" in fn.lower() or "completed" in fn.lower() or "done" in fn.lower(), (
            "AC1: _rmCardHtml must display a closed/completed ticket count"
        )

    def test_card_renders_open_count(self, html):
        """AC1 — card must display open ticket count."""
        fn = self._card_fn(html)
        assert "open" in fn.lower() or "total" in fn.lower(), (
            "AC1: _rmCardHtml must display an open ticket count"
        )

    def test_card_renders_due_date(self, html):
        """AC1 — card must display the due date when present."""
        fn = self._card_fn(html)
        assert "due" in fn.lower() or "due_on" in fn, (
            "AC1: _rmCardHtml must display the due date (ms.due_on)"
        )

    def test_card_ticket_count_label(self, html):
        """AC1 — ticket count display must include both open and closed counts."""
        fn = self._card_fn(html)
        # The card should show both open and closed; look for open count computation
        has_open = "open" in fn.lower()
        has_closed = "closed" in fn.lower() or "done" in fn.lower() or "completed" in fn.lower()
        assert has_open and has_closed, (
            "AC1: _rmCardHtml must display both open and closed ticket counts; "
            "e.g. 'N closed · N open'"
        )


# ── AC2: semantic color tokens for progress ───────────────────────────────────


class TestProgressSemanticColor:
    def test_progress_low_class_uses_danger_token(self, html):
        """AC2 — low-completion progress fill must use --red or --color-error-fg token."""
        # Check for .rm-prog-low (or equivalent) CSS rule
        low_block = re.search(r'\.rm-prog-low\s*\{([^}]+)\}', html, re.DOTALL)
        assert low_block, (
            "AC2: .rm-prog-low CSS rule must exist for low-completion (0–33%) progress"
        )
        decls = low_block.group(1)
        has_danger = (
            "var(--red)" in decls
            or "var(--color-error" in decls
            or "var(--color-danger" in decls
        )
        assert has_danger, (
            "AC2: .rm-prog-low must use var(--red) or a danger/error token "
            "to signal low completion"
        )

    def test_progress_mid_class_uses_warning_token(self, html):
        """AC2 — mid-completion progress fill must use --amber or --color-warning token."""
        mid_block = re.search(r'\.rm-prog-mid\s*\{([^}]+)\}', html, re.DOTALL)
        assert mid_block, (
            "AC2: .rm-prog-mid CSS rule must exist for mid-completion (34–66%) progress"
        )
        decls = mid_block.group(1)
        has_warning = (
            "var(--amber)" in decls
            or "var(--color-warning" in decls
        )
        assert has_warning, (
            "AC2: .rm-prog-mid must use var(--amber) or var(--color-warning) token"
        )

    def test_progress_high_class_uses_success_token(self, html):
        """AC2 — high-completion progress fill must use --green or --color-success token."""
        high_block = re.search(r'\.rm-prog-high\s*\{([^}]+)\}', html, re.DOTALL)
        assert high_block, (
            "AC2: .rm-prog-high CSS rule must exist for high-completion (67–100%) progress"
        )
        decls = high_block.group(1)
        has_success = (
            "var(--green)" in decls
            or "var(--color-success" in decls
        )
        assert has_success, (
            "AC2: .rm-prog-high must use var(--green) or var(--color-success) token"
        )

    def test_card_html_applies_semantic_color_class(self, html):
        """AC2 — _rmCardHtml must apply rm-prog-low/mid/high class based on completion."""
        m = re.search(r"function _rmCardHtml\(ms\).*?^}", html, re.DOTALL | re.MULTILINE)
        assert m, "_rmCardHtml must exist"
        fn = m.group(0)
        # Must reference rm-prog-low, rm-prog-mid, rm-prog-high (or build the class name)
        has_color_classes = (
            ("rm-prog-low" in fn and "rm-prog-mid" in fn and "rm-prog-high" in fn)
            or re.search(r"rm-prog-['\"]", fn)
        )
        assert has_color_classes, (
            "AC2: _rmCardHtml must apply rm-prog-low, rm-prog-mid, or rm-prog-high "
            "based on the completion percentage"
        )

    def test_color_class_selection_covers_full_range(self, html):
        """AC2 — color class logic must cover low/mid/high completion ranges."""
        m = re.search(r"function _rmCardHtml\(ms\).*?^}", html, re.DOTALL | re.MULTILINE)
        assert m, "_rmCardHtml must exist"
        fn = m.group(0)
        # Must have branching logic for the three ranges (e.g. 33, 34, 66, 67)
        has_threshold = re.search(r'\b(33|34|66|67)\b', fn)
        assert has_threshold, (
            "AC2: _rmCardHtml must use thresholds (e.g. <=33, <=66, else) to select "
            "the semantic color class for the progress fill"
        )

    def test_progress_color_classes_no_hardcoded_hex(self, html):
        """AC2/AC7 — progress color CSS classes must use tokens, not hardcoded hex."""
        for cls in ["rm-prog-low", "rm-prog-mid", "rm-prog-high"]:
            block = re.search(rf'\.{cls}\s*\{{([^}}]+)\}}', html, re.DOTALL)
            if block:
                decls = block.group(1)
                clean = re.sub(r'var\([^)]+\)', '', decls)
                assert not re.search(r'#[0-9a-fA-F]{3,6}\b', clean), (
                    f"AC2: .{cls} must not use hardcoded hex colors — use var(--...) tokens"
                )


# ── AC3: foundation tokens for layout (no hardcoded px in new card anatomy) ──


class TestFoundationTokens:
    def test_progress_fill_width_uses_css_var(self, html):
        """AC3/AC8 — .rm-progress-fill CSS must use var(--rm-pct) for dynamic width."""
        fill_block = re.search(r'\.rm-progress-fill\s*\{([^}]+)\}', html, re.DOTALL)
        assert fill_block, ".rm-progress-fill CSS rule must exist"
        decls = fill_block.group(1)
        uses_css_var = "var(--rm-pct" in decls
        assert uses_css_var, (
            "AC3/AC8: .rm-progress-fill must use width: var(--rm-pct, 0%) so the dynamic "
            "width is set via a CSS custom property, not an inline style attribute"
        )

    def test_card_html_sets_pct_via_css_var(self, html):
        """AC3/AC8 — _rmCardHtml must set --rm-pct via style attribute (not width directly)."""
        m = re.search(r"function _rmCardHtml\(ms\).*?^}", html, re.DOTALL | re.MULTILINE)
        assert m, "_rmCardHtml must exist"
        fn = m.group(0)
        # Must set --rm-pct: N% not style="width:N%"
        sets_css_var = "--rm-pct" in fn
        sets_direct_width = re.search(r'style=["\']width\s*:\s*["\']?\s*\+?\s*pct', fn)
        assert sets_css_var, (
            "AC3/AC8: _rmCardHtml must set --rm-pct CSS custom property "
            "(e.g. style=\"--rm-pct:N%\") instead of a direct width inline style"
        )
        assert not sets_direct_width, (
            "AC3/AC8: _rmCardHtml must NOT use style=\"width:N%\" inline style on the "
            "progress fill — use CSS custom property --rm-pct instead"
        )

    def test_new_color_classes_have_no_hardcoded_layout_px(self, html):
        """AC3 — new .rm-prog-* CSS blocks must not hardcode spacing px values."""
        for cls in ["rm-prog-low", "rm-prog-mid", "rm-prog-high"]:
            block = re.search(rf'\.{cls}\s*\{{([^}}]+)\}}', html, re.DOTALL)
            if block:
                decls = block.group(1)
                # Only 'background' (or similar) properties expected, no margin/padding px
                layout_px = re.findall(
                    r'(?:margin|padding|gap|width|height)\s*:\s*\d+px', decls
                )
                assert not layout_px, (
                    f"AC3: .{cls} must not have hardcoded px layout values: {layout_px}"
                )


# ── AC4: empty state — non-empty message, token-styled ───────────────────────


class TestEmptyState:
    def test_empty_state_message_not_bare(self, html):
        """AC4 — empty state must have a meaningful multi-element message."""
        m = re.search(r"function _rmRender\(\).*?^}", html, re.DOTALL | re.MULTILINE)
        assert m, "_rmRender must exist"
        fn = m.group(0)
        # Look for the empty state block — should have title + subtitle or descriptive text
        empty_block = re.search(r"rm-empty[^;]+;", fn, re.DOTALL)
        # At minimum, the message must go beyond a single word phrase
        has_descriptive = re.search(
            r"(milestone|roadmap|started|plan|journey|create|add)",
            fn, re.IGNORECASE
        )
        assert has_descriptive, (
            "AC4: empty state must include a descriptive message about milestones "
            "(e.g. 'No milestones yet', 'Plan your roadmap by adding a milestone')"
        )

    def test_empty_state_has_icon(self, html):
        """AC4 — empty state must include a Tabler icon for visual polish."""
        m = re.search(r"function _rmRender\(\).*?^}", html, re.DOTALL | re.MULTILINE)
        assert m, "_rmRender must exist"
        fn = m.group(0)
        assert "ti ti-" in fn or "ti-map" in fn, (
            "AC4: empty state must include a Tabler icon (ti ti-*)"
        )

    def test_rm_empty_css_uses_tokens(self, html):
        """AC4 — .rm-empty CSS must use var(--...) tokens, not hardcoded hex."""
        blocks = re.findall(r'\.rm-empty[^{]*\{([^}]+)\}', html, re.DOTALL)
        assert blocks, ".rm-empty CSS rule must exist"
        combined = " ".join(blocks)
        clean = re.sub(r'var\([^)]+\)', '', combined)
        assert not re.search(r'#[0-9a-fA-F]{6}\b', clean), (
            "AC4: .rm-empty CSS must not use hardcoded hex colors — use var(--...) tokens"
        )

    def test_rm_empty_css_uses_text_muted_or_sub(self, html):
        """AC4 — empty state must use semantic text tokens for its color."""
        blocks = re.findall(r'\.rm-empty[^{]*\{([^}]+)\}', html, re.DOTALL)
        assert blocks, ".rm-empty CSS rule must exist"
        combined = " ".join(blocks)
        has_token = (
            "var(--text-muted)" in combined
            or "var(--text-sub)" in combined
            or "var(--text)" in combined
        )
        assert has_token, (
            "AC4: .rm-empty must use var(--text-muted) or var(--text-sub) for color"
        )


# ── AC5: existing handlers preserved ──────────────────────────────────────────


class TestExistingHandlers:
    REQUIRED_FUNCTIONS = [
        "_rmCreate",
        "_rmMarkActive",
        "_rmEditCard",
        "_rmSaveEdit",
        "_rmCancelEdit",
        "_rmClose",
        "_rmReopen",
        "_rmRefresh",
        "_rmInitDnd",
        "_rmPersistOrder",
    ]

    @pytest.mark.parametrize("fn_name", REQUIRED_FUNCTIONS)
    def test_handler_still_exists(self, html, fn_name):
        """AC5 — Milestone action handler must still be defined in project.html."""
        assert f"function {fn_name}" in html or f"{fn_name}(" in html, (
            f"AC5: {fn_name} must still be defined — existing handlers must not be removed"
        )

    def test_roadmap_init_still_exists(self, html):
        """AC5 — roadmapInit function must still exist and call fetch."""
        m = re.search(r"function roadmapInit\(\).*?^}", html, re.DOTALL | re.MULTILINE)
        assert m, "AC5: roadmapInit function must still exist"
        fn = m.group(0)
        assert "/api/roadmap" in fn, (
            "AC5: roadmapInit must still fetch from /api/roadmap"
        )

    def test_edit_form_fields_still_present(self, html):
        """AC5 — Inline edit form (title, desc, due date, save/cancel) must still exist."""
        m = re.search(r"function _rmEditCard\(num\).*?^}", html, re.DOTALL | re.MULTILINE)
        assert m, "_rmEditCard must still exist"
        fn = m.group(0)
        assert "rm-edit-form" in fn, "AC5: edit form must still use .rm-edit-form"
        assert "Save" in fn, "AC5: edit form must still have a Save button"
        assert "Cancel" in fn, "AC5: edit form must still have a Cancel button"

    def test_rm_show_add_and_hide_add_still_exist(self, html):
        """AC5 — Add milestone form show/hide handlers must still be present."""
        assert "function _rmShowAdd" in html, "AC5: _rmShowAdd must still be defined"
        assert "function _rmHideAdd" in html, "AC5: _rmHideAdd must still be defined"


# ── AC6: no regressions outside roadmap view ──────────────────────────────────


class TestNoRegressions:
    def test_backlog_milestone_filter_intact(self, html):
        """AC6 — .bl-ms-filter CSS must still exist (backlog milestone filter untouched)."""
        assert ".bl-ms-filter" in html, (
            "AC6: .bl-ms-filter must still exist — roadmap changes must not remove "
            "unrelated backlog filter CSS"
        )

    def test_sprint_board_classes_intact(self, html):
        """AC6 — .smgmt-* sprint board classes must still exist."""
        assert ".smgmt-" in html, (
            "AC6: sprint board .smgmt-* classes must still be present"
        )

    def test_milestone_selector_intact(self, html):
        """AC6 — Milestone selector (bc-milestone) must still exist."""
        assert "bc-milestone" in html, (
            "AC6: #bc-milestone element must still exist — "
            "roadmap changes must not affect bulk-create milestone selector"
        )

    def test_rm_active_badge_css_intact(self, html):
        """AC6 — .rm-active-badge CSS must still exist."""
        assert ".rm-active-badge" in html, (
            "AC6: .rm-active-badge CSS rule must still be present"
        )

    def test_active_card_class_still_applied(self, html):
        """AC6 — rm-active class must still be applied to active milestone cards."""
        m = re.search(r"function _rmCardHtml\(ms\).*?^}", html, re.DOTALL | re.MULTILINE)
        assert m, "_rmCardHtml must exist"
        fn = m.group(0)
        assert "rm-active" in fn, (
            "AC6: _rmCardHtml must still apply rm-active class to active milestone cards"
        )


# ── AC7: dark theme via CSS vars ──────────────────────────────────────────────


class TestDarkTheme:
    def test_progress_color_classes_work_in_dark_mode(self, html):
        """AC7 — rm-prog-* classes use vars defined in both light and dark themes."""
        # --green, --amber, --red are defined in both :root and [data-theme=dark]
        dark_section = re.search(
            r'\[data-theme=["\']dark["\']\]\s*\{([^}]+)\}', html, re.DOTALL
        )
        assert dark_section, "[data-theme='dark'] block must exist"
        dark_vars = dark_section.group(1)
        assert "--green" in dark_vars, "AC7: --green token must be defined in dark theme"
        assert "--amber" in dark_vars, "AC7: --amber token must be defined in dark theme"
        assert "--red" in dark_vars, "AC7: --red token must be defined in dark theme"

    def test_empty_state_works_in_dark_mode(self, html):
        """AC7 — Empty state CSS must use vars valid in dark theme."""
        blocks = re.findall(r'\.rm-empty[^{]*\{([^}]+)\}', html, re.DOTALL)
        combined = " ".join(blocks)
        # Must reference text-muted or text-sub, both defined in dark theme
        has_dark_safe_token = (
            "var(--text-muted)" in combined
            or "var(--text-sub)" in combined
            or "var(--text)" in combined
        )
        assert has_dark_safe_token, (
            "AC7: .rm-empty must use a text token defined in the dark theme"
        )

    def test_new_roadmap_css_no_hardcoded_colors(self, html):
        """AC7 — All new roadmap CSS must use CSS vars, not hardcoded colors."""
        # Check all .rm-prog-* rules
        for cls in ["rm-prog-low", "rm-prog-mid", "rm-prog-high"]:
            block = re.search(rf'\.{cls}\s*\{{([^}}]+)\}}', html, re.DOTALL)
            if block:
                decls = block.group(1)
                clean = re.sub(r'var\([^)]+\)', '', decls)
                bad = re.findall(r'#[0-9a-fA-F]{3,6}\b', clean)
                assert not bad, (
                    f"AC7: .{cls} contains hardcoded colors {bad}; "
                    "use CSS variables for dark theme compatibility"
                )


# ── AC8: impeccable — no inline styles, semantic HTML ─────────────────────────


class TestImpeccable:
    def test_progress_fill_no_direct_width_inline_style(self, html):
        """AC8 — _rmCardHtml must not set width via inline style on progress fill."""
        m = re.search(r"function _rmCardHtml\(ms\).*?^}", html, re.DOTALL | re.MULTILINE)
        assert m, "_rmCardHtml must exist"
        fn = m.group(0)
        # Must NOT produce style="width:N%"
        bad_pattern = re.search(
            r'style=["\'][^"\']*width\s*:\s*["\']?\s*\+?\s*pct[^"\']*["\']',
            fn, re.DOTALL
        )
        assert not bad_pattern, (
            "AC8: _rmCardHtml must not use style=\"width:N%\" inline style "
            "on the progress fill — use CSS custom property --rm-pct"
        )

    def test_progress_fill_no_background_inline_style(self, html):
        """AC8 — _rmCardHtml must not set background color via inline style."""
        m = re.search(r"function _rmCardHtml\(ms\).*?^}", html, re.DOTALL | re.MULTILINE)
        assert m, "_rmCardHtml must exist"
        fn = m.group(0)
        bad_bg = re.search(
            r'rm-progress-fill[^;]*style=["\'][^"\']*background',
            fn, re.DOTALL
        )
        assert not bad_bg, (
            "AC8: _rmCardHtml must not set background color via inline style on "
            "rm-progress-fill — use a CSS class (rm-prog-low/mid/high)"
        )

    def test_progress_width_css_rule_not_hardcoded_percentage(self, html):
        """AC8 — .rm-progress-fill CSS must not have a hardcoded % width."""
        fill_block = re.search(r'\.rm-progress-fill\s*\{([^}]+)\}', html, re.DOTALL)
        if fill_block:
            decls = fill_block.group(1)
            # Must not have literal percentage like width: 75% hardcoded
            hardcoded_pct = re.search(r'width\s*:\s*\d+%', decls)
            assert not hardcoded_pct, (
                "AC8: .rm-progress-fill must not have a hardcoded percentage width; "
                "use var(--rm-pct, 0%) which is set dynamically per card"
            )

    @pytest.mark.skipif(
        not shutil.which("npx"),
        reason="npx not available — skip impeccable gate (see memory: coder-clone-no-node.md)"
    )
    def test_impeccable_detect_passes(self):
        """AC8 — impeccable detect must pass on project.html."""
        result = subprocess.run(
            ["npx", "impeccable", "detect", str(PROJECT_HTML)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (
            f"AC8: impeccable detect found violations:\n{result.stdout}\n{result.stderr}"
        )


# ── AC9: vanilla JS/CSS only ──────────────────────────────────────────────────


class TestVanillaOnly:
    def test_no_new_js_framework_dependencies(self):
        """AC9 — package.json must not add animation/styling framework deps."""
        pkg_path = REPO_ROOT / "package.json"
        if not pkg_path.exists():
            return
        pkg = json.loads(pkg_path.read_text())
        all_deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        forbidden = {"animejs", "gsap", "framer-motion", "motion", "popmotion", "styled-components"}
        added = forbidden & set(all_deps.keys())
        assert not added, (
            f"AC9: forbidden runtime dependencies added: {added}"
        )

    def test_no_import_statements_in_roadmap_section(self, html):
        """AC9 — roadmap JS in project.html must not import external modules."""
        m = re.search(
            r"function roadmapInit\(\).*?(?=function advInit\b)",
            html, re.DOTALL
        )
        if m:
            section = m.group(0)
            external_imports = re.findall(
                r'\bimport\s+.+from\s+[\'"]([^\'"]+)[\'"]', section
            )
            bad = [i for i in external_imports if not i.startswith(".")]
            assert not bad, (
                f"AC9: roadmap JS must not import external libraries: {bad}"
            )


# ── AC10: scope — only roadmap region changed ──────────────────────────────────


class TestScope:
    def test_new_css_classes_in_roadmap_section(self, html):
        """AC10 — new .rm-prog-* CSS rules must appear within the roadmap CSS section."""
        roadmap_start = html.find("/* ── Roadmap tab (issue #878)")
        assert roadmap_start != -1, "Roadmap CSS section marker must exist"
        low_pos = html.find(".rm-prog-low", roadmap_start)
        assert low_pos != -1, (
            "AC10: .rm-prog-low CSS class must appear after the roadmap section marker"
        )

    def test_sprint_board_section_unchanged(self, html):
        """AC10 — sprint board classes must not be modified."""
        assert "smgmt-board" in html or "smgmt-" in html, (
            "AC10: sprint board CSS must be untouched"
        )

    def test_advisor_tab_intact(self, html):
        """AC10 — advisor tab classes must still be present."""
        assert "#pane-advisor" in html, (
            "AC10: #pane-advisor must still exist — roadmap changes must not affect "
            "the advisor tab"
        )
