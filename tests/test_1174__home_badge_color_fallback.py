"""Tests for issue #1174 — Home project badge: fall back to gray for non-palette color values.

AC coverage:
  AC1 — _projectBadgeHtml validates sanitized color against the known .pbic--* palette set
  AC2 — Non-palette values are coerced to 'gray' before composing the class name
  AC3 — Hex color (e.g. #abc123 → 'abc') falls back to pbic--gray, not pbic--abc
  AC4 — Valid palette values (e.g. 'blue', 'red') produce the correct pbic--<color> class
  AC5 — No color/icon_color set still falls back to pbic--gray
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = REPO_ROOT / "apps" / "dashboard" / "static"
HOME_HTML = STATIC_DIR / "home.html"

# Known palette suffixes extracted from .pbic--* CSS classes in home.html
EXPECTED_PALETTE = {"gray", "blue", "green", "purple", "red", "orange", "amber", "yellow", "pink", "cyan", "teal", "indigo"}


@pytest.fixture(scope="module")
def html() -> str:
    assert HOME_HTML.exists(), f"home.html not found at {HOME_HTML}"
    return HOME_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def inline_script(html) -> str:
    parts = re.findall(r"<script(?![^>]*\bsrc\b)[^>]*>(.*?)</script>", html, re.DOTALL)
    return "\n".join(parts)


@pytest.fixture(scope="module")
def badge_fn_src(inline_script) -> str:
    """Extract the _projectBadgeHtml function body."""
    m = re.search(r"function _projectBadgeHtml\s*\([^)]*\)\s*\{(.*?)\n\s*\}", inline_script, re.DOTALL)
    assert m, "_projectBadgeHtml function not found in home.html inline script"
    return m.group(1)


# ── AC1: palette validation is present ────────────────────────────────────────

def test_ac1_palette_set_defined_in_script(inline_script):
    """JS must define a set/array of known pbic palette color names (AC1)."""
    # Accept a Set literal, an Array, or a variable holding palette colors.
    # Pattern: a collection containing at least 'gray', 'blue', and 'green'.
    has_palette = (
        re.search(r"(?:new Set|const|let|var)\s*\w*\s*[=\(]\s*[\[{].*?'gray'.*?'blue'", inline_script, re.DOTALL)
        or re.search(r"(?:new Set|const|let|var)\s*\w*\s*[=\(]\s*[\[{].*?'blue'.*?'gray'", inline_script, re.DOTALL)
    )
    assert has_palette, (
        "home.html inline JS must define a palette Set or Array containing at least "
        "'gray' and 'blue' so _projectBadgeHtml can validate color names (AC1)"
    )


def test_ac1_palette_contains_all_css_colors(inline_script):
    """The JS palette collection must contain all colors defined in .pbic--* CSS classes (AC1)."""
    for color in EXPECTED_PALETTE:
        assert f"'{color}'" in inline_script, (
            f"JS palette collection is missing '{color}' — every color defined as a "
            f".pbic--{color} CSS class must appear in the palette validator (AC1)"
        )


# ── AC2: fallback to 'gray' for unknown values ───────────────────────────────

def test_ac2_fallback_to_gray_when_not_in_palette(badge_fn_src):
    """_projectBadgeHtml must coerce unknown colors to 'gray' (AC2)."""
    # Must have a conditional that falls back to 'gray' for non-palette values.
    # Accept: ternary, if-else, or .has() / .includes() guard with 'gray' default.
    has_fallback = (
        re.search(r"['\"]gray['\"]", badge_fn_src)
        and (
            re.search(r"\.has\(|\.includes\(", badge_fn_src)
            or re.search(r"\bif\b", badge_fn_src)
        )
    )
    assert has_fallback, (
        "_projectBadgeHtml must contain a palette membership check (.has()/.includes() "
        "or if-branch) and a 'gray' fallback for values not in the palette (AC2)"
    )


# ── AC3: hex color → pbic--gray, not pbic--abc ───────────────────────────────

def test_ac3_hex_color_sanitizes_to_alpha_only(inline_script):
    """After replace(/[^a-z]/g,''), hex '#abc123' becomes 'abc' — must then fall back to gray (AC3)."""
    # Confirm the sanitize regex is still in place (strips non-alpha characters).
    assert re.search(r"replace\s*\(\s*/\[.*?a-z.*?\]/g\s*,\s*['\"]['\"]", inline_script), (
        "home.html must still strip non-alpha chars (replace(/[^a-z]/g,'')) so "
        "hex values like '#abc123' are reduced to 'abc' before palette lookup (AC3)"
    )


def test_ac3_sanitized_hex_not_used_as_class(badge_fn_src):
    """The badge function must not use the raw sanitized string directly as the CSS class suffix (AC3)."""
    # If the code used the sanitized value directly (no palette check), a hex '#abc123'
    # would produce class 'pbic--abc'. We verify the palette lookup sits between sanitize
    # and class composition: the final class must come from a variable that went through
    # the palette guard, not from the raw sanitized value.
    #
    # Heuristic: the template literal `pbic--${…}` must reference a variable that is
    # *assigned* from a conditional or .has()/.includes() expression, not just the direct
    # output of .replace().
    class_compose = re.search(r"pbic--\$\{[^}]+\}", badge_fn_src)
    assert class_compose, "pbic--${...} template literal not found in _projectBadgeHtml"

    raw_direct = re.search(
        r"pbic--\$\{esc\(\s*\(\s*proj\.color\s*\|\|", badge_fn_src
    )
    assert not raw_direct, (
        "pbic-- class is still composed directly from the sanitized color without "
        "a palette guard — hex values like '#abc123' will produce 'pbic--abc' (AC3)"
    )


# ── AC4: valid palette values pass through unchanged ─────────────────────────

def test_ac4_known_palette_colors_produce_correct_class(inline_script):
    """For known colors ('blue', 'red'), _projectBadgeHtml must produce pbic--<color> (AC4).

    Verified structurally: the palette set must include 'blue' and 'red',
    so .has('blue') returns true and the color passes through unchanged.
    """
    for color in ("blue", "red", "green"):
        assert f"'{color}'" in inline_script, (
            f"Palette set must include '{color}' so known colors produce the "
            f"correct pbic--{color} class (AC4)"
        )


# ── AC5: no-color project still gets pbic--gray ──────────────────────────────

def test_ac5_no_color_defaults_to_gray(badge_fn_src):
    """When color and icon_color are absent, _projectBadgeHtml must fall back to pbic--gray (AC5)."""
    # The existing `|| 'gray'` default satisfies the fallback; confirm it's still there.
    assert re.search(r"\|\|\s*['\"]gray['\"]", badge_fn_src), (
        "_projectBadgeHtml must retain the `|| 'gray'` default so projects with "
        "no color or icon_color still render pbic--gray (AC5)"
    )
