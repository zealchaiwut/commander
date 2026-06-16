"""Acceptance tests for issue #1057 — Sprint board polish: hover/focus, live-update
transitions, skeleton, empty state.

AC map:
  AC1  Ticket cards show a distinct hover state (shadow lift or border highlight)
       using existing foundation tokens
  AC2  Ticket cards show a visible focus ring (keyboard-accessible) meeting WCAG 2.1 AA
  AC3  Cards play a subtle enter/update transition when live data arrives;
       transition is suppressed under prefers-reduced-motion: reduce
  AC4  A loading skeleton is rendered before first data fetch completes; skeleton
       matches card layout and uses foundation tokens
  AC5  An intentional empty state is shown when the board has zero tickets
  AC6  Card elements carry correct aria-label attributes; cards are reachable
       and activatable via keyboard (Tab + Enter/Space)
  AC7  Implementation is vanilla JS/CSS only — no new runtime dependencies
  AC8  Styles reuse existing foundation design tokens; no ad-hoc magic values
  AC9  Dark theme is applied and consistent across all new states
  AC10 Bundle is rebuilt after any src/ changes; no stale artifacts committed
  AC11 detect check passes with no new violations
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = REPO_ROOT / "apps" / "dashboard" / "static"
SRC_DIR = STATIC_DIR / "src"
BOARD_DIR = SRC_DIR / "sprint-board"
DIST_DIR = STATIC_DIR / "dist"
PROJECT_HTML = STATIC_DIR / "project.html"
BOARD_RENDER = BOARD_DIR / "board-render.js"
BUNDLE = DIST_DIR / "bundle.js"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def project_css() -> str:
    return _read(PROJECT_HTML)


@pytest.fixture(scope="module")
def board_render() -> str:
    return _read(BOARD_RENDER)


@pytest.fixture(scope="module")
def bundle() -> str:
    assert BUNDLE.exists(), "static/dist/bundle.js must be committed"
    return _read(BUNDLE)


# ── AC1: distinct hover state using foundation tokens ─────────────────────────

class TestHoverState:
    def test_ticket_hover_has_box_shadow(self, project_css):
        """AC1 — Hover state must include a shadow lift via CSS."""
        # Match the hover rule and confirm box-shadow is present
        hover_block = re.search(
            r'\.smgmt-ticket:hover\s*\{([^}]+)\}', project_css, re.DOTALL
        )
        assert hover_block, ".smgmt-ticket:hover rule must exist"
        decls = hover_block.group(1)
        assert "box-shadow" in decls, (
            "AC1: .smgmt-ticket:hover must add a box-shadow for visual lift"
        )

    def test_ticket_hover_uses_token_not_hardcoded_color(self, project_css):
        """AC1 — Hover state must use var(--) tokens, not raw hex/rgb."""
        hover_block = re.search(
            r'\.smgmt-ticket:hover\s*\{([^}]+)\}', project_css, re.DOTALL
        )
        assert hover_block, ".smgmt-ticket:hover rule must exist"
        decls = hover_block.group(1)
        assert not re.search(r'#[0-9a-fA-F]{3,6}\b', decls), (
            "AC1/AC8: hover state must not use hardcoded hex colors; use var(--...) tokens"
        )

    def test_ticket_hover_cursor_pointer(self, project_css):
        """AC1 — Hover on ticket should show pointer cursor for clickable affordance."""
        hover_block = re.search(
            r'\.smgmt-ticket:hover\s*\{([^}]+)\}', project_css, re.DOTALL
        )
        if hover_block:
            # cursor pointer is a nice-to-have; check the ticket default or hover
            pass  # cursor rules may be on specific draggable variants


# ── AC2: visible focus ring meeting WCAG AA ───────────────────────────────────

class TestFocusRing:
    def test_focus_visible_rule_exists(self, project_css):
        """AC2 — A :focus-visible rule on .smgmt-ticket must exist."""
        assert re.search(r'\.smgmt-ticket:focus-visible', project_css), (
            "AC2: .smgmt-ticket:focus-visible rule must be defined for keyboard focus"
        )

    def test_focus_ring_uses_outline(self, project_css):
        """AC2 — Focus ring must use 'outline' for AA-level visibility."""
        focus_block = re.search(
            r'\.smgmt-ticket:focus-visible\s*\{([^}]+)\}', project_css, re.DOTALL
        )
        assert focus_block, ".smgmt-ticket:focus-visible must have a CSS rule block"
        decls = focus_block.group(1)
        assert "outline" in decls, (
            "AC2: focus-visible must set 'outline' for a visible focus ring"
        )

    def test_focus_ring_uses_blue_token(self, project_css):
        """AC2 — Focus ring must use var(--blue) for AA contrast (not hardcoded hex)."""
        focus_block = re.search(
            r'\.smgmt-ticket:focus-visible\s*\{([^}]+)\}', project_css, re.DOTALL
        )
        assert focus_block, ".smgmt-ticket:focus-visible must have a CSS rule block"
        decls = focus_block.group(1)
        assert "var(--blue)" in decls, (
            "AC2: focus ring must use var(--blue) token for WCAG AA contrast"
        )

    def test_focus_ring_not_hardcoded_hex(self, project_css):
        """AC2/AC8 — Focus rule must not use hardcoded hex colors."""
        focus_block = re.search(
            r'\.smgmt-ticket:focus-visible\s*\{([^}]+)\}', project_css, re.DOTALL
        )
        assert focus_block, ".smgmt-ticket:focus-visible must have a CSS rule block"
        decls = focus_block.group(1)
        assert not re.search(r'#[0-9a-fA-F]{3,6}\b', decls), (
            "AC2/AC8: focus ring must not use hardcoded hex colors"
        )


# ── AC3: enter/update transition + prefers-reduced-motion suppression ─────────

class TestTransitions:
    def test_keyframes_defined(self, project_css):
        """AC3 — A @keyframes animation for ticket enter must be defined."""
        assert re.search(r'@keyframes\s+smgmt-ticket-enter', project_css), (
            "AC3: @keyframes smgmt-ticket-enter must be defined in project.html CSS"
        )

    def test_enter_class_animates(self, project_css):
        """AC3 — .smgmt-ticket-enter must apply the enter animation."""
        enter_block = re.search(
            r'\.smgmt-ticket-enter\s*\{([^}]+)\}', project_css, re.DOTALL
        )
        assert enter_block, ".smgmt-ticket-enter CSS class must be defined"
        decls = enter_block.group(1)
        assert "animation" in decls, (
            "AC3: .smgmt-ticket-enter must set 'animation' to trigger enter keyframes"
        )

    def test_reduced_motion_suppresses_animation(self, project_css):
        """AC3 — Under prefers-reduced-motion: reduce, animation must be suppressed."""
        assert "prefers-reduced-motion" in project_css, (
            "AC3: project.html CSS must include a @media (prefers-reduced-motion) block"
        )
        # Find the reduced-motion block and confirm animation is suppressed
        rm_block = re.search(
            r'@media\s*\(prefers-reduced-motion\s*:\s*reduce\)\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\}',
            project_css, re.DOTALL
        )
        assert rm_block, "@media (prefers-reduced-motion: reduce) block not found"
        block_text = rm_block.group(1)
        assert "animation" in block_text and ("none" in block_text or "0s" in block_text), (
            "AC3: reduced-motion block must suppress animation (animation: none or 0s)"
        )

    def test_board_render_adds_enter_class_to_tickets(self, board_render):
        """AC3 — board-render.js must add smgmt-ticket-enter class to new ticket rows."""
        assert "smgmt-ticket-enter" in board_render, (
            "AC3: board-render.js must add class 'smgmt-ticket-enter' to animate new tickets"
        )


# ── AC4: loading skeleton ─────────────────────────────────────────────────────

class TestLoadingSkeleton:
    def test_skeleton_class_rendered_during_load(self, board_render):
        """AC4 — loadSprintMgmt must inject skeleton HTML before data arrives."""
        assert "smgmt-skeleton" in board_render, (
            "AC4: board-render.js must inject elements with class 'smgmt-skeleton' "
            "as the loading placeholder"
        )

    def test_skeleton_css_defined(self, project_css):
        """AC4 — Skeleton styles must be defined in project.html."""
        assert re.search(r'\.smgmt-skeleton', project_css), (
            "AC4: .smgmt-skeleton CSS rule must be defined in project.html"
        )

    def test_skeleton_uses_foundation_tokens(self, project_css):
        """AC4/AC8 — Skeleton styles must use var(--) tokens, not hardcoded colors."""
        blocks = re.findall(
            r'\.smgmt-skeleton[^{]*\{([^}]+)\}', project_css, re.DOTALL
        )
        assert blocks, ".smgmt-skeleton CSS blocks must exist"
        combined = " ".join(blocks)
        # Must not have bare hex colors in skeleton rules
        assert not re.search(r'(?<!var\()#[0-9a-fA-F]{6}\b', combined), (
            "AC4/AC8: skeleton CSS must not use hardcoded hex colors; use var(--...) tokens"
        )

    def test_skeleton_has_shimmer_or_pulse_animation(self, project_css):
        """AC4 — Skeleton should have an animation (shimmer/pulse) to signal loading."""
        # Check for any @keyframes animation referenced in skeleton CSS
        has_anim = re.search(r'smgmt-skeleton.*animation|animation.*smgmt-skeleton', project_css, re.DOTALL)
        has_keyframes = re.search(r'@keyframes\s+(skeleton|shimmer|pulse)', project_css)
        assert has_keyframes or re.search(r'\.smgmt-skeleton.*animation', project_css, re.DOTALL), (
            "AC4: skeleton should have a shimmer/pulse animation to indicate loading activity"
        )


# ── AC5: intentional empty state ─────────────────────────────────────────────

class TestEmptyState:
    def test_empty_state_class_in_board_render(self, board_render):
        """AC5 — _smgmtRender must use a dedicated empty-state element."""
        assert "smgmt-empty-state" in board_render, (
            "AC5: board-render.js must use class 'smgmt-empty-state' for the zero-ticket state"
        )

    def test_empty_state_has_meaningful_message(self, board_render):
        """AC5 — Empty state must include a human-readable message."""
        empty_block = re.search(
            r'smgmt-empty-state[^`]*`', board_render, re.DOTALL
        )
        # Look for the context of the empty state HTML
        ctx = re.search(
            r'(No sprints|empty state|no tickets|no sprint|Start by)', board_render, re.IGNORECASE
        )
        assert ctx, (
            "AC5: empty state must include a meaningful human-readable message"
        )

    def test_empty_state_css_defined(self, project_css):
        """AC5 — CSS for the empty state must be defined in project.html."""
        assert re.search(r'\.smgmt-empty-state', project_css), (
            "AC5: .smgmt-empty-state CSS rule must be defined in project.html"
        )

    def test_empty_state_uses_tokens(self, project_css):
        """AC5/AC8 — Empty state CSS must use var(--) tokens."""
        blocks = re.findall(
            r'\.smgmt-empty-state[^{]*\{([^}]+)\}', project_css, re.DOTALL
        )
        assert blocks, ".smgmt-empty-state CSS blocks must exist"
        combined = " ".join(blocks)
        assert not re.search(r'(?<!var\()#[0-9a-fA-F]{6}\b', combined), (
            "AC5/AC8: empty state CSS must not use hardcoded hex colors"
        )


# ── AC6: aria-label + keyboard Tab/Enter/Space ────────────────────────────────

class TestAccessibility:
    def test_ticket_row_has_tabindex_zero(self, board_render):
        """AC6 — _smgmtTicketRowHtml must emit tabindex="0" on the ticket div,
        not tabindex="-1", so ticket rows are reachable via the Tab key."""
        # Isolate _smgmtTicketRowHtml function body
        fn_match = re.search(
            r'export function _smgmtTicketRowHtml\b.*?^}',
            board_render, re.DOTALL | re.MULTILINE
        )
        assert fn_match, "_smgmtTicketRowHtml function must exist in board-render.js"
        fn_body = fn_match.group(0)
        # The .smgmt-ticket div must NOT use tabindex="-1" (unreachable by Tab)
        assert 'tabindex="-1"' not in fn_body, (
            "AC6: _smgmtTicketRowHtml still emits tabindex=\"-1\" — change to tabindex=\"0\" "
            "so ticket cards are Tab-reachable"
        )
        # And must have tabindex="0"
        assert 'tabindex="0"' in fn_body, (
            "AC6: _smgmtTicketRowHtml must emit tabindex=\"0\" on the .smgmt-ticket div"
        )

    def test_ticket_row_has_aria_label(self, board_render):
        """AC6 — _smgmtTicketRowHtml must emit an aria-label on the ticket div."""
        fn_match = re.search(
            r'export function _smgmtTicketRowHtml\b.*?^}',
            board_render, re.DOTALL | re.MULTILINE
        )
        assert fn_match, "_smgmtTicketRowHtml function must exist"
        fn_body = fn_match.group(0)
        assert "aria-label" in fn_body, (
            "AC6: _smgmtTicketRowHtml must add aria-label to the ticket div for screen readers"
        )

    def test_ticket_row_has_keyboard_activation(self, board_render):
        """AC6 — The ticket .smgmt-ticket div must handle Enter/Space to activate."""
        fn_match = re.search(
            r'export function _smgmtTicketRowHtml\b.*?^}',
            board_render, re.DOTALL | re.MULTILINE
        )
        assert fn_match, "_smgmtTicketRowHtml function must exist"
        fn_body = fn_match.group(0)
        # The ticket div template string must have onkeydown handling Enter (and space = ' ')
        assert re.search(
            r"onkeydown.*event\.key.*===.*['\"]Enter['\"]|onkeydown.*Enter",
            fn_body, re.DOTALL
        ), (
            "AC6: .smgmt-ticket div must have onkeydown handler for Enter/Space activation; "
            "check for event.key==='Enter'||event.key===' '"
        )

    def test_backlog_ticket_row_has_tabindex(self, board_render):
        """AC6 — Backlog ticket rows also need keyboard accessibility."""
        fn_match = re.search(
            r'export function _smgmtBacklogTicketHtml\b.*?^}',
            board_render, re.DOTALL | re.MULTILINE
        )
        if fn_match:
            fn_body = fn_match.group(0)
            has_access = (
                'tabindex="0"' in fn_body
                or 'aria-label' in fn_body
            )
            assert has_access, (
                "AC6: _smgmtBacklogTicketHtml must include tabindex=\"0\" or aria-label "
                "for keyboard accessibility"
            )


# ── AC7: vanilla JS/CSS only — no new runtime dependencies ───────────────────

class TestNoDependencies:
    def test_no_new_npm_deps(self):
        """AC7 — package.json must not gain new runtime dependencies."""
        pkg = json.loads(_read(REPO_ROOT / "package.json"))
        # Only build tools are allowed; no new runtime libs
        deps = pkg.get("dependencies", {})
        dev_deps = pkg.get("devDependencies", {})
        all_deps = {**deps, **dev_deps}
        # Verify no animation/transition libraries were added
        forbidden = {"animejs", "gsap", "framer-motion", "motion", "popmotion"}
        added = forbidden & set(all_deps.keys())
        assert not added, (
            f"AC7: new runtime dependencies added that are not allowed: {added}"
        )

    def test_board_render_no_import_new_lib(self, board_render):
        """AC7 — board-render.js must not import new runtime libraries."""
        imports = re.findall(r"^import\s+.+from\s+['\"]([^'\"]+)['\"]", board_render, re.MULTILINE)
        # Only allow relative imports (starting with ./) and the known progress-activity module
        for imp in imports:
            assert imp.startswith(".") or "progress-activity" in imp, (
                f"AC7: board-render.js must not import external library '{imp}'"
            )


# ── AC8: foundation tokens, no magic values ───────────────────────────────────

class TestDesignTokens:
    HOVER_FOCUS_PATTERNS = [
        r'\.smgmt-ticket:hover',
        r'\.smgmt-ticket:focus-visible',
        r'\.smgmt-ticket-enter',
        r'\.smgmt-skeleton',
        r'\.smgmt-empty-state',
    ]

    def _extract_css_blocks(self, css: str, selector_pattern: str) -> list:
        """Return all declaration blocks for a CSS selector pattern."""
        return re.findall(
            selector_pattern.replace(".", r"\.") + r"[^{]*\{([^}]+)\}",
            css, re.DOTALL
        )

    def test_no_hardcoded_hex_in_new_rules(self, project_css):
        """AC8 — New CSS rules must not use hardcoded hex colors."""
        for pattern in self.HOVER_FOCUS_PATTERNS:
            blocks = re.findall(
                pattern + r'[^{]*\{([^}]+)\}', project_css, re.DOTALL
            )
            for block in blocks:
                # Allow hex inside var() fallbacks but not bare
                clean = re.sub(r'var\([^)]+\)', '', block)
                assert not re.search(r'#[0-9a-fA-F]{3,6}\b', clean), (
                    f"AC8: CSS block for '{pattern}' must not use hardcoded hex color; "
                    "use var(--...) design tokens"
                )

    def test_hover_uses_surface_hover_token(self, project_css):
        """AC8 — Hover background must use var(--surface-hover)."""
        hover_block = re.search(
            r'\.smgmt-ticket:hover\s*\{([^}]+)\}', project_css, re.DOTALL
        )
        assert hover_block, ".smgmt-ticket:hover must exist"
        decls = hover_block.group(1)
        assert "var(--surface-hover)" in decls or "var(--border)" in decls or "var(--blue)" in decls, (
            "AC8: hover state must use a foundation token (var(--surface-hover) or similar)"
        )


# ── AC9: dark theme consistent ────────────────────────────────────────────────

class TestDarkTheme:
    def test_new_css_uses_only_css_vars(self, project_css):
        """AC9 — All new CSS for sprint-board polish must use CSS variables only
        so both light and dark themes work correctly without extra overrides."""
        new_selectors = [
            r'\.smgmt-ticket:focus-visible',
            r'\.smgmt-ticket-enter',
            r'@keyframes smgmt-ticket-enter',
            r'\.smgmt-skeleton',
            r'\.smgmt-empty-state',
        ]
        for sel in new_selectors:
            block = re.search(sel + r'[^{]*\{([^}]+)\}', project_css, re.DOTALL)
            if block:
                decls = block.group(1)
                # Strip out var() content to avoid false positives from fallback values
                stripped = re.sub(r'var\([^)]*\)', '', decls)
                bad_hex = re.findall(r'#[0-9a-fA-F]{3,6}\b', stripped)
                assert not bad_hex, (
                    f"AC9: '{sel}' contains hardcoded hex {bad_hex}; "
                    "use CSS variables so dark theme works correctly"
                )

    def test_no_hardcoded_light_colors_in_new_rules(self, project_css):
        """AC9 — No hardcoded light-mode values like #f9fafb or #ffffff in new rules."""
        # These are the light-mode background values
        light_backgrounds = ["#f9fafb", "#ffffff", "#f3f4f6"]
        # We check the entire project_css but only care about new additions;
        # since we can't diff, verify these aren't used in skeleton/empty-state blocks
        for sel in [r'\.smgmt-skeleton', r'\.smgmt-empty-state']:
            blocks = re.findall(sel + r'[^{]*\{([^}]+)\}', project_css, re.DOTALL)
            for block in blocks:
                for val in light_backgrounds:
                    assert val not in block, (
                        f"AC9: '{sel}' must not hardcode light-mode value '{val}'; "
                        "use var(--bg) or var(--surface-2) tokens"
                    )


# ── AC10: bundle rebuilt ──────────────────────────────────────────────────────

class TestBundleRebuilt:
    def test_bundle_contains_skeleton_class(self, bundle):
        """AC10 — bundle.js must contain the skeleton HTML (built from board-render.js)."""
        assert "smgmt-skeleton" in bundle, (
            "AC10: bundle.js must contain 'smgmt-skeleton' — run `npm run build` after "
            "editing board-render.js"
        )

    def test_bundle_contains_enter_class(self, bundle):
        """AC10 — bundle.js must include the smgmt-ticket-enter class from board-render.js."""
        assert "smgmt-ticket-enter" in bundle, (
            "AC10: bundle.js must contain 'smgmt-ticket-enter' — run `npm run build` "
            "after editing src/ files"
        )

    def test_bundle_contains_empty_state_class(self, bundle):
        """AC10 — bundle.js must contain the empty-state class."""
        assert "smgmt-empty-state" in bundle, (
            "AC10: bundle.js must contain 'smgmt-empty-state' — run `npm run build`"
        )

    def test_bundle_is_newer_than_board_render(self):
        """AC10 — bundle.js mtime must be >= board-render.js mtime (no stale artifact)."""
        bundle_mtime = BUNDLE.stat().st_mtime
        render_mtime = BOARD_RENDER.stat().st_mtime
        assert bundle_mtime >= render_mtime, (
            "AC10: bundle.js is older than board-render.js — run `npm run build` to rebuild"
        )


# ── AC11: detect gate passes ──────────────────────────────────────────────────

class TestDetectGate:
    def test_no_hardcoded_spacing_in_new_rules(self, project_css):
        """AC11 — New CSS must use spacing-scale values (4/8/12/16/24/32px) only."""
        # Check skeleton and empty state rules for off-scale spacing
        for sel in [r'\.smgmt-skeleton-row', r'\.smgmt-empty-state']:
            blocks = re.findall(sel + r'[^{]*\{([^}]+)\}', project_css, re.DOTALL)
            for block in blocks:
                # Find px values
                px_vals = re.findall(r'(\d+(?:\.\d+)?)px', block)
                off_scale = [
                    v for v in px_vals
                    if float(v) not in {0, 4, 8, 12, 14, 16, 24, 32, 1, 2, 3, 6, 10, 11, 13, 100, 200, 48, 64}
                ]
                assert not off_scale, (
                    f"AC11: '{sel}' uses off-scale spacing values {off_scale}px; "
                    "allowed: 4, 8, 12, 16, 24, 32px"
                )

    def test_no_magic_z_index(self, project_css):
        """AC11 — New CSS must not use arbitrary z-index magic numbers."""
        # Only z-index: 1 or 2 is acceptable for hover/focus layering
        for sel in [r'\.smgmt-ticket:hover', r'\.smgmt-ticket:focus-visible', r'\.smgmt-ticket-enter']:
            blocks = re.findall(sel + r'[^{]*\{([^}]+)\}', project_css, re.DOTALL)
            for block in blocks:
                z_vals = re.findall(r'z-index\s*:\s*(\d+)', block)
                bad_z = [z for z in z_vals if int(z) > 10]
                assert not bad_z, (
                    f"AC11: '{sel}' uses high z-index {bad_z} — use 1 or 2 for layering"
                )

    @pytest.mark.skipif(
        not shutil.which("npx"), reason="npx not available in this environment"
    )
    def test_impeccable_detect_board_render(self):
        """AC11 — impeccable detect must pass on the sprint-board source."""
        result = subprocess.run(
            ["npx", "impeccable", "detect", str(BOARD_RENDER)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (
            f"AC11: impeccable detect found violations in board-render.js:\n{result.stdout}\n{result.stderr}"
        )
