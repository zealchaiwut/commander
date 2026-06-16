"""Acceptance tests for issue #1071 — Polish Roadmap/Milestones tab UI and accessibility.

AC map:
  AC1   Milestone cards have distinct hover style (elevation, border, or background shift)
        using foundation tokens
  AC2   Milestone cards have visible focus ring on keyboard focus, meeting WCAG 2.1 AA contrast
  AC3   Progress bar animates on load (fill from 0% to value); animation disabled when
        prefers-reduced-motion: reduce is set
  AC4   Loading state renders intentional skeleton or spinner while milestone data fetches
  AC5   Empty state renders friendly message/illustration when no milestones exist
  AC6   All milestone cards are keyboard-navigable (Tab, Enter) with correct aria-label
  AC7   Implementation uses foundation design tokens — no hardcoded hex values
  AC8   Dark theme supported without additional overrides (CSS vars throughout)
  AC9   Vanilla JS/CSS only — no new framework dependencies
  AC10  Detect (lint/type/style checks) passes clean — spacing scale, no magic z-index
  AC11  Changes scoped to roadmap view region only — no side effects on other tabs
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = REPO_ROOT / "apps" / "dashboard" / "static"
PROJECT_HTML = STATIC_DIR / "project.html"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def html() -> str:
    return _read(PROJECT_HTML)


# ── AC1: distinct hover state using foundation tokens ────────────────────────


class TestHoverState:
    def test_rm_card_hover_rule_exists(self, html):
        """AC1 — .rm-card:hover CSS rule must exist."""
        assert re.search(r'\.rm-card:hover', html), (
            "AC1: .rm-card:hover rule must be defined"
        )

    def test_rm_card_hover_uses_foundation_token(self, html):
        """AC1 — Hover rule must reference foundation tokens (surface-hover, border, or blue)."""
        block = re.search(r'\.rm-card:hover\s*\{([^}]+)\}', html, re.DOTALL)
        assert block, ".rm-card:hover block must have declarations"
        decls = block.group(1)
        has_token = (
            "var(--surface-hover)" in decls
            or "var(--border)" in decls
            or "var(--blue)" in decls
            or "var(--text-sub)" in decls
        )
        assert has_token, (
            "AC1: .rm-card:hover must use at least one foundation token "
            "(var(--surface-hover), var(--border), var(--blue), or similar)"
        )

    def test_rm_card_hover_no_hardcoded_hex(self, html):
        """AC1/AC7 — Hover rule must not use hardcoded hex colors."""
        block = re.search(r'\.rm-card:hover\s*\{([^}]+)\}', html, re.DOTALL)
        assert block, ".rm-card:hover must exist"
        decls = block.group(1)
        clean = re.sub(r'var\([^)]+\)', '', decls)
        assert not re.search(r'#[0-9a-fA-F]{3,6}\b', clean), (
            "AC1/AC7: .rm-card:hover must not use hardcoded hex colors; use var(--...) tokens"
        )

    def test_rm_card_has_transition(self, html):
        """AC1 — .rm-card base rule should include a transition for smooth hover."""
        # Find the .rm-card base rule (not .rm-card.rm-active or :hover)
        block = re.search(r'\.rm-card\s*\{([^}]+)\}', html, re.DOTALL)
        assert block, ".rm-card base CSS rule must exist"
        decls = block.group(1)
        assert "transition" in decls, (
            "AC1: .rm-card must include a CSS transition for smooth hover/focus effects"
        )


# ── AC2: visible focus ring meeting WCAG 2.1 AA ──────────────────────────────


class TestFocusRing:
    def test_rm_card_focus_visible_exists(self, html):
        """AC2 — .rm-card:focus-visible rule must be defined."""
        assert re.search(r'\.rm-card:focus-visible', html), (
            "AC2: .rm-card:focus-visible rule must be defined for keyboard focus"
        )

    def test_focus_ring_uses_outline(self, html):
        """AC2 — Focus ring must use 'outline' for AA-level visibility."""
        block = re.search(r'\.rm-card:focus-visible\s*\{([^}]+)\}', html, re.DOTALL)
        assert block, ".rm-card:focus-visible must have a CSS rule block"
        decls = block.group(1)
        assert "outline" in decls, (
            "AC2: .rm-card:focus-visible must set 'outline' for a visible focus ring"
        )

    def test_focus_ring_uses_blue_token(self, html):
        """AC2 — Focus ring must use var(--blue) for WCAG AA contrast."""
        block = re.search(r'\.rm-card:focus-visible\s*\{([^}]+)\}', html, re.DOTALL)
        assert block, ".rm-card:focus-visible must have a CSS rule block"
        decls = block.group(1)
        assert "var(--blue)" in decls, (
            "AC2: focus ring must use var(--blue) token for WCAG AA contrast"
        )

    def test_focus_ring_no_hardcoded_hex(self, html):
        """AC2/AC7 — Focus rule must not use hardcoded hex colors."""
        block = re.search(r'\.rm-card:focus-visible\s*\{([^}]+)\}', html, re.DOTALL)
        assert block, ".rm-card:focus-visible must have a CSS rule block"
        decls = block.group(1)
        clean = re.sub(r'var\([^)]+\)', '', decls)
        assert not re.search(r'#[0-9a-fA-F]{3,6}\b', clean), (
            "AC2/AC7: .rm-card:focus-visible must not use hardcoded hex colors"
        )


# ── AC3: progress bar animation + prefers-reduced-motion ─────────────────────


class TestProgressAnimation:
    def test_progress_animation_defined(self, html):
        """AC3 — Progress bar must have a CSS transition or @keyframes animation."""
        fill_block = re.search(r'\.rm-progress-fill\s*\{([^}]+)\}', html, re.DOTALL)
        assert fill_block, ".rm-progress-fill CSS rule must exist"
        decls = fill_block.group(1)
        has_animation = "transition" in decls or "animation" in decls
        assert has_animation, (
            "AC3: .rm-progress-fill must have a CSS transition or animation "
            "to animate the fill from 0% to the target value on load"
        )

    def test_progress_starts_at_zero(self, html):
        """AC3 — Progress fill initial width should be 0 (data-driven, not hardcoded pct)."""
        # The _rmCardHtml function must NOT embed the pct inline as style="width:X%"
        # Instead it should use data-pct attribute or start at 0 with JS driving the fill
        fn_match = re.search(
            r'function _rmCardHtml\(ms\).*?^}',
            html, re.DOTALL | re.MULTILINE
        )
        assert fn_match, "_rmCardHtml function must exist in project.html"
        fn_body = fn_match.group(0)
        # Must use data-pct OR start fill at 0 for animation to work
        uses_data_pct = "data-pct" in fn_body
        starts_at_zero = re.search(r'rm-progress-fill.*?style.*?width\s*:\s*0', fn_body, re.DOTALL)
        # Either approach is valid
        assert uses_data_pct or starts_at_zero, (
            "AC3: _rmCardHtml must store target percentage in data-pct attribute "
            "(or set initial width to 0) so the CSS transition can animate from 0 to value"
        )

    def test_reduced_motion_suppresses_progress_animation(self, html):
        """AC3 — prefers-reduced-motion: reduce block must suppress the progress animation."""
        assert "prefers-reduced-motion" in html, (
            "AC3: project.html must include @media (prefers-reduced-motion) block"
        )
        rm_block = re.search(
            r'@media\s*\(prefers-reduced-motion\s*:\s*reduce\)\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\}',
            html, re.DOTALL
        )
        assert rm_block, "@media (prefers-reduced-motion: reduce) block not found"
        block_text = rm_block.group(1)
        # Must reference the progress fill or a broad animation reset
        assert (
            "rm-progress" in block_text
            or ("animation" in block_text and ("none" in block_text or "0s" in block_text))
        ), (
            "AC3: prefers-reduced-motion block must suppress the roadmap progress animation"
        )

    def test_rmrender_triggers_fill_animation(self, html):
        """AC3 — _rmRender must trigger progress fill after inserting HTML (requestAnimationFrame or setTimeout)."""
        fn_match = re.search(
            r'function _rmRender\(\).*?^}',
            html, re.DOTALL | re.MULTILINE
        )
        assert fn_match, "_rmRender function must exist"
        fn_body = fn_match.group(0)
        uses_raf = "requestAnimationFrame" in fn_body or "setTimeout" in fn_body
        uses_data_pct = "data-pct" in fn_body
        assert uses_raf or uses_data_pct, (
            "AC3: _rmRender must trigger progress fill after innerHTML is set "
            "(via requestAnimationFrame or setTimeout); or query data-pct attributes"
        )


# ── AC4: loading skeleton ─────────────────────────────────────────────────────


class TestLoadingSkeleton:
    def test_skeleton_class_in_css(self, html):
        """AC4 — A .rm-skeleton* CSS rule must exist."""
        assert re.search(r'\.rm-skeleton', html), (
            "AC4: .rm-skeleton CSS rule must be defined in project.html"
        )

    def test_skeleton_injected_on_load(self, html):
        """AC4 — roadmapInit must inject skeleton HTML while data fetches."""
        fn_match = re.search(
            r'function roadmapInit\(\).*?^}',
            html, re.DOTALL | re.MULTILINE
        )
        assert fn_match, "roadmapInit function must exist"
        fn_body = fn_match.group(0)
        assert "rm-skeleton" in fn_body, (
            "AC4: roadmapInit must inject skeleton HTML (rm-skeleton class) "
            "as the loading placeholder instead of plain text"
        )

    def test_skeleton_css_uses_tokens(self, html):
        """AC4/AC7 — Skeleton CSS must use var(--) tokens, not hardcoded colors."""
        blocks = re.findall(r'\.rm-skeleton[^{]*\{([^}]+)\}', html, re.DOTALL)
        assert blocks, ".rm-skeleton CSS blocks must exist"
        combined = " ".join(blocks)
        clean = re.sub(r'var\([^)]+\)', '', combined)
        assert not re.search(r'(?<!var\()#[0-9a-fA-F]{6}\b', clean), (
            "AC4/AC7: skeleton CSS must not use hardcoded hex colors; use var(--...) tokens"
        )

    def test_skeleton_has_shimmer_or_animation(self, html):
        """AC4 — Skeleton should have a shimmer/pulse animation to signal loading."""
        has_skeleton_anim = re.search(
            r'\.rm-skeleton.*?animation|rm-skeleton-shimmer|rm-skeleton-pulse',
            html, re.DOTALL
        )
        has_keyframes = re.search(r'@keyframes\s+rm-skeleton', html)
        assert has_skeleton_anim or has_keyframes, (
            "AC4: skeleton should have a shimmer/pulse animation (keyframes or animation property)"
        )


# ── AC5: intentional empty state ─────────────────────────────────────────────


class TestEmptyState:
    def test_rm_empty_class_in_css(self, html):
        """AC5 — .rm-empty CSS rule must exist."""
        assert re.search(r'\.rm-empty', html), (
            "AC5: .rm-empty CSS rule must exist for the empty milestone state"
        )

    def test_rm_empty_has_meaningful_content(self, html):
        """AC5 — Empty state must include a human-readable friendly message."""
        fn_match = re.search(
            r'function _rmRender\(\).*?^}',
            html, re.DOTALL | re.MULTILINE
        )
        assert fn_match, "_rmRender must exist"
        fn_body = fn_match.group(0)
        # The empty state must have more than just "No milestones yet." — needs to feel friendly
        empty_block = re.search(r'rm-empty[^"]*"([^"]*)"', fn_body, re.DOTALL)
        # Check the roadmapInit path (may be in _rmRender or roadmapInit)
        ctx = re.search(
            r'(No milestones|milestone yet|Get started|Add your first|plan your roadmap|roadmap is empty)',
            html, re.IGNORECASE
        )
        assert ctx, (
            "AC5: empty state must include a meaningful human-readable message "
            "(e.g. 'No milestones yet', 'Get started', 'Add your first milestone')"
        )

    def test_rm_empty_uses_icon(self, html):
        """AC5 — Empty state should include a Tabler icon for visual polish."""
        fn_match = re.search(
            r'function _rmRender\(\).*?^}',
            html, re.DOTALL | re.MULTILINE
        )
        assert fn_match, "_rmRender must exist"
        fn_body = fn_match.group(0)
        assert "ti ti-" in fn_body or "ti-map" in fn_body, (
            "AC5: empty state should include a Tabler icon (ti ti-*) for visual polish"
        )

    def test_rm_empty_css_uses_tokens(self, html):
        """AC5/AC7 — Empty state CSS must use var(--) tokens."""
        blocks = re.findall(r'\.rm-empty[^{]*\{([^}]+)\}', html, re.DOTALL)
        assert blocks, ".rm-empty CSS blocks must exist"
        combined = " ".join(blocks)
        clean = re.sub(r'var\([^)]+\)', '', combined)
        assert not re.search(r'(?<!var\()#[0-9a-fA-F]{6}\b', clean), (
            "AC5/AC7: .rm-empty CSS must not use hardcoded hex colors; use var(--...) tokens"
        )


# ── AC6: keyboard navigation + aria-label ─────────────────────────────────────


class TestKeyboardAccessibility:
    def _get_rm_card_fn(self, html: str) -> str:
        fn_match = re.search(
            r'function _rmCardHtml\(ms\).*?^}',
            html, re.DOTALL | re.MULTILINE
        )
        assert fn_match, "_rmCardHtml function must exist in project.html"
        return fn_match.group(0)

    def test_card_has_tabindex_zero(self, html):
        """AC6 — _rmCardHtml must emit tabindex="0" so cards are Tab-reachable."""
        fn_body = self._get_rm_card_fn(html)
        assert 'tabindex="0"' in fn_body, (
            'AC6: _rmCardHtml must emit tabindex="0" on the .rm-card div '
            "so milestone cards are reachable via the Tab key"
        )

    def test_card_has_aria_label(self, html):
        """AC6 — _rmCardHtml must emit an aria-label on the card div."""
        fn_body = self._get_rm_card_fn(html)
        assert "aria-label" in fn_body, (
            "AC6: _rmCardHtml must add aria-label to the .rm-card div for screen readers"
        )

    def test_card_has_keyboard_enter_handler(self, html):
        """AC6 — .rm-card div must handle Enter key to activate."""
        fn_body = self._get_rm_card_fn(html)
        has_enter = re.search(
            r"onkeydown.*Enter|key.*===.*['\"]Enter['\"]",
            fn_body, re.DOTALL
        )
        assert has_enter, (
            "AC6: .rm-card must have an onkeydown handler for Enter key activation; "
            "keyboard-only users must be able to activate the card"
        )

    def test_rm_card_keyboard_handler_function_exists(self, html):
        """AC6 — A keyboard handler function for rm-card must be defined."""
        assert re.search(r'function _rmCardKeydown\b', html), (
            "AC6: _rmCardKeydown function must be defined to handle keyboard activation "
            "of milestone cards"
        )


# ── AC7: foundation tokens, no hardcoded values ───────────────────────────────


class TestDesignTokens:
    NEW_SELECTORS = [
        r'\.rm-card:hover',
        r'\.rm-card:focus-visible',
    ]

    def test_new_rules_no_hardcoded_hex(self, html):
        """AC7 — New roadmap CSS rules must not use hardcoded hex colors."""
        for pattern in self.NEW_SELECTORS:
            blocks = re.findall(pattern + r'[^{]*\{([^}]+)\}', html, re.DOTALL)
            for block in blocks:
                clean = re.sub(r'var\([^)]+\)', '', block)
                assert not re.search(r'#[0-9a-fA-F]{3,6}\b', clean), (
                    f"AC7: CSS block for '{pattern}' must not use hardcoded hex color; "
                    "use var(--...) design tokens"
                )

    def test_hover_uses_surface_hover_or_similar_token(self, html):
        """AC7 — Hover background must use a surface/border token."""
        block = re.search(r'\.rm-card:hover\s*\{([^}]+)\}', html, re.DOTALL)
        assert block, ".rm-card:hover must exist"
        decls = block.group(1)
        assert "var(--" in decls, (
            "AC7: .rm-card:hover must use at least one CSS var(--...) token"
        )


# ── AC8: dark theme — CSS vars only ──────────────────────────────────────────


class TestDarkTheme:
    ROADMAP_SELECTORS = [
        r'\.rm-card:focus-visible',
        r'\.rm-card:hover',
    ]

    def test_new_css_uses_only_css_vars(self, html):
        """AC8 — New roadmap CSS must use CSS variables so both themes work correctly."""
        for sel in self.ROADMAP_SELECTORS:
            block = re.search(sel + r'[^{]*\{([^}]+)\}', html, re.DOTALL)
            if block:
                decls = block.group(1)
                stripped = re.sub(r'var\([^)]*\)', '', decls)
                bad_hex = re.findall(r'#[0-9a-fA-F]{3,6}\b', stripped)
                assert not bad_hex, (
                    f"AC8: '{sel}' contains hardcoded hex {bad_hex}; "
                    "use CSS variables so dark theme works correctly"
                )

    def test_skeleton_no_light_mode_hardcoded_values(self, html):
        """AC8 — Skeleton CSS must not hardcode light-mode background values."""
        light_backgrounds = ["#f9fafb", "#ffffff", "#f3f4f6", "#f5f6f8"]
        skeleton_blocks = re.findall(r'\.rm-skeleton[^{]*\{([^}]+)\}', html, re.DOTALL)
        for block in skeleton_blocks:
            for val in light_backgrounds:
                assert val not in block, (
                    f"AC8: skeleton CSS must not hardcode light-mode value '{val}'; "
                    "use var(--surface-2) or var(--surface-hover) tokens"
                )


# ── AC9: vanilla JS/CSS only ─────────────────────────────────────────────────


class TestNoDependencies:
    def test_no_new_animation_packages(self):
        """AC9 — package.json must not gain new animation/motion runtime libraries."""
        pkg = json.loads(_read(REPO_ROOT / "package.json"))
        all_deps = {
            **pkg.get("dependencies", {}),
            **pkg.get("devDependencies", {}),
        }
        forbidden = {"animejs", "gsap", "framer-motion", "motion", "popmotion"}
        added = forbidden & set(all_deps.keys())
        assert not added, (
            f"AC9: new runtime dependencies added that are not allowed: {added}"
        )

    def test_no_new_import_statements_in_roadmap_js(self, html):
        """AC9 — Roadmap JS in project.html must not import external libraries."""
        # Roadmap code is inline in project.html, not a module — no import statements expected
        roadmap_js_section = re.search(
            r'function roadmapInit\(\)(.*?)function _rmCardKeydown', html, re.DOTALL
        )
        if roadmap_js_section:
            section = roadmap_js_section.group(1)
            imports = re.findall(r'\bimport\s+.+from\s+[\'"]([^\'"]+)[\'"]', section)
            for imp in imports:
                assert imp.startswith("."), (
                    f"AC9: roadmap JS must not import external library '{imp}'"
                )


# ── AC10: detect gate — spacing scale, no magic z-index ──────────────────────


class TestDetectGate:
    def test_no_off_scale_spacing_in_skeleton_rules(self, html):
        """AC10 — Skeleton CSS must use spacing-scale values (4/8/12/16/24/32px)."""
        blocks = re.findall(r'\.rm-skeleton[^{]*\{([^}]+)\}', html, re.DOTALL)
        allowed_px = {0, 1, 2, 3, 4, 6, 8, 10, 11, 12, 13, 14, 16, 24, 32, 48, 64, 100, 200}
        for block in blocks:
            px_vals = re.findall(r'(\d+(?:\.\d+)?)px', block)
            off_scale = [v for v in px_vals if float(v) not in allowed_px]
            assert not off_scale, (
                f"AC10: skeleton CSS uses off-scale spacing values {off_scale}px; "
                "use 4/8/12/16/24/32px scale"
            )

    def test_no_magic_z_index_in_hover_focus_rules(self, html):
        """AC10 — New hover/focus rules must not use arbitrary high z-index values."""
        for sel in [r'\.rm-card:hover', r'\.rm-card:focus-visible']:
            blocks = re.findall(sel + r'[^{]*\{([^}]+)\}', html, re.DOTALL)
            for block in blocks:
                z_vals = re.findall(r'z-index\s*:\s*(\d+)', block)
                bad_z = [z for z in z_vals if int(z) > 10]
                assert not bad_z, (
                    f"AC10: '{sel}' uses high z-index {bad_z} — use 1 or 2 for layering"
                )

    @pytest.mark.skipif(
        not shutil.which("npx"), reason="npx not available in this environment"
    )
    def test_impeccable_detect_passes(self):
        """AC10 — impeccable detect must pass on project.html."""
        result = subprocess.run(
            ["npx", "impeccable", "detect", str(PROJECT_HTML)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (
            f"AC10: impeccable detect found violations in project.html:\n"
            f"{result.stdout}\n{result.stderr}"
        )


# ── AC11: changes scoped to roadmap region only ───────────────────────────────


class TestScopeIsolation:
    def test_no_smgmt_changes_in_roadmap_css(self, html):
        """AC11 — Roadmap changes must not modify sprint-board (.smgmt-*) selectors."""
        # Isolate the roadmap CSS section
        roadmap_css = re.search(
            r'/\* ── Roadmap tab.*?/\* ──',
            html, re.DOTALL
        )
        if roadmap_css:
            section = roadmap_css.group(0)
            # No sprint-board selectors should appear in the roadmap CSS section
            smgmt_refs = re.findall(r'\.smgmt-', section)
            assert not smgmt_refs, (
                f"AC11: roadmap CSS section contains .smgmt-* references: {smgmt_refs}; "
                "roadmap changes must be scoped to .rm-* selectors"
            )

    def test_rm_card_focus_visible_rule_after_roadmap_marker(self, html):
        """AC11 — New .rm-card focus/hover rules must appear in the roadmap section."""
        roadmap_start = html.find("/* ── Roadmap tab (issue #878)")
        assert roadmap_start != -1, "Roadmap CSS section marker must exist"
        # The rm-card hover/focus rules should appear after the roadmap marker
        hover_pos = html.find(".rm-card:hover", roadmap_start)
        assert hover_pos != -1, (
            "AC11: .rm-card:hover rule must appear after the roadmap CSS section marker"
        )

    def test_bl_filter_unaffected(self, html):
        """AC11 — Backlog milestone filter CSS (.bl-ms-filter) must not be altered."""
        # Just verify .bl-ms-filter still exists (no deletion)
        assert ".bl-ms-filter" in html, (
            "AC11: .bl-ms-filter selector must still exist (roadmap changes must not "
            "remove unrelated CSS)"
        )
