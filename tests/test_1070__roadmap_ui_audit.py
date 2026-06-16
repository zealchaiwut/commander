"""Acceptance tests for issue #1070 — Audit and fix Roadmap tab UI against impeccable rules.

AC map:
  AC1   impeccable detect reports zero findings on the roadmap view region of project.html
  AC2   Progress bars meet contrast requirements using foundation tokens
  AC3   Card spacing is consistent and token-based throughout the milestones list
  AC4   Date and count values are correctly aligned per design system rules
  AC5   Typography (size, weight, line-height) follows foundation token scale
  AC6   No inline styles or non-token values introduced in the roadmap region JS
  AC7   All milestone card action buttons remain functional after fixes
  AC8   Dark theme renders correctly — look-ahead accent uses a defined foundation token
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = REPO_ROOT / "apps" / "dashboard" / "static"
PROJECT_HTML = STATIC_DIR / "project.html"

SPACING_SCALE = {0, 1, 2, 3, 4, 6, 8, 10, 11, 12, 13, 14, 16, 24, 32, 48, 64}
FONT_SIZE_SCALE = {10, 11, 12, 13, 14, 15, 16, 18, 20, 24, 28, 32}


def _read() -> str:
    return PROJECT_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def html() -> str:
    return _read()


def _roadmap_css(html: str) -> str:
    """Extract the CSS block that starts at the roadmap section marker."""
    start = html.find("/* ── Roadmap tab (issue #878)")
    assert start != -1, "Roadmap CSS section marker not found"
    end = html.find("</style>", start)
    return html[start:end]


def _rm_card_html_fn(html: str) -> str:
    m = re.search(r"function _rmCardHtml\(ms\).*?^}", html, re.DOTALL | re.MULTILINE)
    assert m, "_rmCardHtml function not found"
    return m.group(0)


def _roadmap_init_fn(html: str) -> str:
    m = re.search(r"function roadmapInit\(\).*?^}", html, re.DOTALL | re.MULTILINE)
    assert m, "roadmapInit function not found"
    return m.group(0)


def _rm_render_fn(html: str) -> str:
    m = re.search(r"function _rmRender\(\).*?^}", html, re.DOTALL | re.MULTILINE)
    assert m, "_rmRender function not found"
    return m.group(0)


# ── AC1: impeccable detect zero findings ─────────────────────────────────────


@pytest.mark.skipif(not shutil.which("npx"), reason="npx not available")
def test_impeccable_detect_passes(html):
    """AC1 — impeccable detect must exit 0 on project.html."""
    result = subprocess.run(
        ["npx", "impeccable", "detect", str(PROJECT_HTML)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"AC1: impeccable detect found violations:\n{result.stdout}\n{result.stderr}"
    )


# ── AC2: progress bars use foundation tokens ──────────────────────────────────


class TestProgressBarsTokens:
    def test_progress_fill_uses_token_for_low(self, html):
        """AC2 — .rm-prog-low must use var(--red) not hardcoded color."""
        m = re.search(r"\.rm-prog-low\s*\{([^}]+)\}", html)
        assert m, ".rm-prog-low CSS rule must exist"
        assert "var(--red)" in m.group(1), (
            "AC2: .rm-prog-low must use var(--red) for contrast with dark backgrounds"
        )

    def test_progress_fill_uses_token_for_mid(self, html):
        """AC2 — .rm-prog-mid must use var(--amber) not hardcoded color."""
        m = re.search(r"\.rm-prog-mid\s*\{([^}]+)\}", html)
        assert m, ".rm-prog-mid CSS rule must exist"
        assert "var(--amber)" in m.group(1), (
            "AC2: .rm-prog-mid must use var(--amber) for correct dark/light theme contrast"
        )

    def test_progress_fill_uses_token_for_high(self, html):
        """AC2 — .rm-prog-high must use var(--green) not hardcoded color."""
        m = re.search(r"\.rm-prog-high\s*\{([^}]+)\}", html)
        assert m, ".rm-prog-high CSS rule must exist"
        assert "var(--green)" in m.group(1), (
            "AC2: .rm-prog-high must use var(--green) for correct dark/light theme contrast"
        )

    def test_progress_track_uses_surface_token(self, html):
        """AC2 — .rm-progress-wrap track must use var(--surface-2) not hardcoded gray."""
        m = re.search(r"\.rm-progress-wrap\s*\{([^}]+)\}", html)
        assert m, ".rm-progress-wrap CSS rule must exist"
        assert "var(--surface-2)" in m.group(1), (
            "AC2: .rm-progress-wrap must use var(--surface-2) as the track background "
            "so contrast is correct in both light and dark themes"
        )

    def test_progress_css_no_hardcoded_hex(self, html):
        """AC2 — Progress bar CSS must not use hardcoded hex colors."""
        for sel in [r"\.rm-prog-low", r"\.rm-prog-mid", r"\.rm-prog-high", r"\.rm-progress-wrap"]:
            m = re.search(sel + r"\s*\{([^}]+)\}", html)
            if m:
                clean = re.sub(r"var\([^)]+\)", "", m.group(1))
                assert not re.search(r"#[0-9a-fA-F]{3,6}\b", clean), (
                    f"AC2: {sel} must not use hardcoded hex; use var(--...) tokens"
                )


# ── AC3: card spacing on the token scale ─────────────────────────────────────


class TestCardSpacing:
    def test_rm_list_gap_on_scale(self, html):
        """AC3 — .rm-list gap must be a value from the spacing token scale (4/8/12/16/24/32)."""
        m = re.search(r"\.rm-list\s*\{([^}]+)\}", html, re.DOTALL)
        assert m, ".rm-list CSS rule must exist"
        gap_m = re.search(r"gap\s*:\s*(\d+)px", m.group(1))
        assert gap_m, ".rm-list must have a gap property"
        gap_val = int(gap_m.group(1))
        allowed = {4, 8, 12, 16, 24, 32}
        assert gap_val in allowed, (
            f"AC3: .rm-list gap is {gap_val}px which is off the spacing token scale "
            f"({sorted(allowed)}px); use 16px between milestone cards"
        )

    def test_rm_card_padding_on_scale(self, html):
        """AC3 — .rm-card padding must be a value from the spacing token scale."""
        m = re.search(r"\.rm-card\s*\{([^}]+)\}", html, re.DOTALL)
        assert m, ".rm-card CSS rule must exist"
        pad_m = re.search(r"padding\s*:\s*(\d+)px", m.group(1))
        assert pad_m, ".rm-card must have a padding property"
        pad_val = int(pad_m.group(1))
        allowed = {4, 8, 12, 16, 24, 32}
        assert pad_val in allowed, (
            f"AC3: .rm-card padding is {pad_val}px which is off the spacing token scale"
        )

    def test_rm_card_head_margin_on_scale(self, html):
        """AC3 — .rm-card-head margin-bottom must be on the spacing scale."""
        m = re.search(r"\.rm-card-head\s*\{([^}]+)\}", html)
        assert m, ".rm-card-head CSS rule must exist"
        mb_m = re.search(r"margin-bottom\s*:\s*(\d+)px", m.group(1))
        assert mb_m, ".rm-card-head must have a margin-bottom property"
        mb_val = int(mb_m.group(1))
        allowed = {4, 8, 12, 16, 24, 32}
        assert mb_val in allowed, (
            f"AC3: .rm-card-head margin-bottom is {mb_val}px which is off the spacing scale"
        )


# ── AC4: date and count alignment ─────────────────────────────────────────────


class TestAlignment:
    def test_rm_card_meta_is_flex_with_alignment(self, html):
        """AC4 — .rm-card-meta must be display:flex with align-items:center."""
        m = re.search(r"\.rm-card-meta\s*\{([^}]+)\}", html)
        assert m, ".rm-card-meta CSS rule must exist"
        decls = m.group(1)
        assert "display" in decls and "flex" in decls, (
            "AC4: .rm-card-meta must use display:flex to align date and count"
        )
        assert "align-items" in decls and "center" in decls, (
            "AC4: .rm-card-meta must use align-items:center for vertical alignment"
        )

    def test_rm_due_has_font_size_on_scale(self, html):
        """AC4 — .rm-due must have a font-size on the token scale."""
        m = re.search(r"\.rm-due\s*\{([^}]+)\}", html)
        assert m, ".rm-due CSS rule must exist"
        fs_m = re.search(r"font-size\s*:\s*(\d+)px", m.group(1))
        assert fs_m, ".rm-due must have an explicit font-size"
        fs_val = int(fs_m.group(1))
        assert fs_val in FONT_SIZE_SCALE, (
            f"AC4: .rm-due font-size {fs_val}px is not on the token scale {sorted(FONT_SIZE_SCALE)}"
        )

    def test_rm_progress_text_uses_mono_font(self, html):
        """AC4 — .rm-progress-text must use monospace font for count values."""
        m = re.search(r"\.rm-progress-text\s*\{([^}]+)\}", html)
        assert m, ".rm-progress-text CSS rule must exist"
        decls = m.group(1)
        assert "var(--mono)" in decls, (
            "AC4: .rm-progress-text must use var(--mono) font-family for count values "
            "(data/metrics must use monospace per DESIGN.md)"
        )


# ── AC5: typography on token scale ────────────────────────────────────────────


class TestTypography:
    def test_rm_card_title_font_size_on_scale(self, html):
        """AC5 — .rm-card-title font-size must be on the token scale."""
        m = re.search(r"\.rm-card-title\s*\{([^}]+)\}", html)
        assert m, ".rm-card-title CSS rule must exist"
        fs_m = re.search(r"font-size\s*:\s*(\d+)px", m.group(1))
        assert fs_m, ".rm-card-title must have an explicit font-size"
        assert int(fs_m.group(1)) in FONT_SIZE_SCALE, (
            f"AC5: .rm-card-title font-size {fs_m.group(1)}px is not on the token scale"
        )

    def test_rm_card_title_font_weight_correct(self, html):
        """AC5 — .rm-card-title must have font-weight 600 or 700 (heading weight)."""
        m = re.search(r"\.rm-card-title\s*\{([^}]+)\}", html)
        assert m, ".rm-card-title must exist"
        fw_m = re.search(r"font-weight\s*:\s*(\d+)", m.group(1))
        assert fw_m, ".rm-card-title must have font-weight"
        assert int(fw_m.group(1)) in {600, 700}, (
            "AC5: .rm-card-title font-weight must be 600 or 700 (heading weight per DESIGN.md)"
        )

    def test_rm_card_desc_line_height(self, html):
        """AC5 — .rm-card-desc must have line-height >= 1.4 for readability."""
        m = re.search(r"\.rm-card-desc\s*\{([^}]+)\}", html)
        assert m, ".rm-card-desc CSS rule must exist"
        lh_m = re.search(r"line-height\s*:\s*([\d.]+)", m.group(1))
        assert lh_m, ".rm-card-desc must have an explicit line-height"
        assert float(lh_m.group(1)) >= 1.4, (
            f"AC5: .rm-card-desc line-height {lh_m.group(1)} is too tight; "
            "use ≥ 1.4 per DESIGN.md typography rules"
        )

    def test_toolbar_title_no_inline_font_size(self, html):
        """AC5 — Roadmap toolbar title must use a CSS class, not an inline font-size style."""
        render_fn = _rm_render_fn(html)
        assert 'style="font-size:14px' not in render_fn and "style='font-size:14px" not in render_fn, (
            "AC5: toolbar title must not use an inline style='font-size:14px;...'; "
            "use .rm-toolbar-title CSS class instead"
        )


# ── AC6: no inline styles with hardcoded values in roadmap JS ────────────────


class TestNoInlineStyles:
    def test_loading_state_uses_css_class(self, html):
        """AC6 — roadmapInit loading placeholder must use a CSS class, not inline padding/color."""
        init_fn = _roadmap_init_fn(html)
        has_inline_padding = 'style="padding:32px;text-align:center' in init_fn
        assert not has_inline_padding, (
            "AC6: roadmapInit loading state must use a CSS class (e.g. .rm-loading or "
            ".rm-skeleton-card) instead of inline style='padding:32px;text-align:center...'"
        )

    def test_look_ahead_icon_no_inline_margin(self, html):
        """AC6 — Look-ahead icon must not use inline margin-right style."""
        render_fn = _rm_render_fn(html)
        assert 'style="margin-right:5px"' not in render_fn, (
            "AC6: look-ahead label icon must not use inline style='margin-right:5px'; "
            "use a CSS class or remove the inline style"
        )

    def test_stale_retry_btn_no_inline_margin(self, html):
        """AC6 — Stale notice retry button must not use inline style='margin-left:auto'."""
        render_fn = _rm_render_fn(html)
        assert 'style="margin-left:auto"' not in render_fn, (
            "AC6: stale notice retry button must not use inline style='margin-left:auto'; "
            "use a CSS class (.rm-stale-retry) or add margin-left:auto to a rule"
        )

    def test_history_icon_no_inline_color(self, html):
        """AC6 — History row icon must not use inline style for color."""
        fn_m = re.search(r"function _rmHistoryHtml\(ms\).*?^}", html, re.DOTALL | re.MULTILINE)
        assert fn_m, "_rmHistoryHtml function must exist"
        fn_body = fn_m.group(0)
        assert 'style="color:var(--text-muted)"' not in fn_body, (
            "AC6: history row icon must not use inline style='color:var(--text-muted)'; "
            "use a CSS class (e.g. .rm-history-icon) instead"
        )

    def test_rm_toolbar_title_class_in_css(self, html):
        """AC6 — A .rm-toolbar-title CSS class must be defined for the toolbar heading."""
        assert re.search(r"\.rm-toolbar-title", html), (
            "AC6: .rm-toolbar-title CSS class must be defined to replace the inline "
            "style='font-size:14px;font-weight:700' on the Milestones toolbar heading"
        )


# ── AC7: card action buttons functional ──────────────────────────────────────


class TestCardActions:
    def test_rm_card_html_has_mark_active_btn(self, html):
        """AC7 — _rmCardHtml must emit a 'Mark active' button with onclick."""
        fn_body = _rm_card_html_fn(html)
        assert "_rmMarkActive(" in fn_body, (
            "AC7: _rmCardHtml must include 'Mark active' button calling _rmMarkActive()"
        )

    def test_rm_card_html_has_edit_btn(self, html):
        """AC7 — _rmCardHtml must emit an 'Edit' button with onclick."""
        fn_body = _rm_card_html_fn(html)
        assert "_rmEditCard(" in fn_body, (
            "AC7: _rmCardHtml must include 'Edit' button calling _rmEditCard()"
        )

    def test_rm_card_html_has_close_btn(self, html):
        """AC7 — _rmCardHtml must emit a 'Close' button with onclick."""
        fn_body = _rm_card_html_fn(html)
        assert "_rmClose(" in fn_body, (
            "AC7: _rmCardHtml must include 'Close' button calling _rmClose()"
        )

    def test_mark_active_fn_exists(self, html):
        """AC7 — _rmMarkActive function must be defined."""
        assert "function _rmMarkActive(" in html, (
            "AC7: _rmMarkActive function must be defined for milestone card actions"
        )

    def test_edit_fn_exists(self, html):
        """AC7 — _rmEditCard function must be defined."""
        assert "function _rmEditCard(" in html, (
            "AC7: _rmEditCard function must be defined for milestone card actions"
        )

    def test_close_fn_exists(self, html):
        """AC7 — _rmClose function must be defined."""
        assert "function _rmClose(" in html, (
            "AC7: _rmClose function must be defined for milestone card actions"
        )


# ── AC8: dark theme — look-ahead num uses defined foundation token ────────────


class TestDarkThemeTokens:
    def test_rm_look_ahead_num_uses_defined_token(self, html):
        """AC8 — .rm-look-ahead-num background must use a defined foundation token, not var(--accent)."""
        m = re.search(r"\.rm-look-ahead-num\s*\{([^}]+)\}", html, re.DOTALL)
        assert m, ".rm-look-ahead-num CSS rule must exist"
        decls = m.group(1)
        assert "var(--accent)" not in decls, (
            "AC8: .rm-look-ahead-num must not use var(--accent) which is undefined in the "
            "foundation token set; use var(--blue) for the accent color"
        )
        assert "var(--blue)" in decls or "var(--text)" in decls, (
            "AC8: .rm-look-ahead-num must use a defined foundation token such as "
            "var(--blue) for its background color"
        )

    def test_rm_card_css_no_hardcoded_hex(self, html):
        """AC8 — .rm-card CSS must not use hardcoded hex colors."""
        blocks = re.findall(r"\.rm-card\b[^{]*\{([^}]+)\}", html, re.DOTALL)
        for block in blocks:
            clean = re.sub(r"var\([^)]+\)", "", block)
            bad = re.findall(r"#[0-9a-fA-F]{6}\b", clean)
            assert not bad, (
                f"AC8: .rm-card CSS contains hardcoded hex {bad}; "
                "use var(--...) tokens so dark theme renders correctly"
            )

    def test_rm_progress_css_no_hardcoded_hex(self, html):
        """AC8 — Progress bar CSS must not use hardcoded hex colors."""
        css_section = _roadmap_css(html)
        progress_blocks = re.findall(
            r"\.rm-prog(?:ress|[-_]\w+)\b[^{]*\{([^}]+)\}", css_section, re.DOTALL
        )
        for block in progress_blocks:
            clean = re.sub(r"var\([^)]+\)", "", block)
            bad = re.findall(r"#[0-9a-fA-F]{6}\b", clean)
            assert not bad, (
                f"AC8: progress bar CSS contains hardcoded hex {bad}; "
                "use var(--...) tokens for correct dark theme rendering"
            )
