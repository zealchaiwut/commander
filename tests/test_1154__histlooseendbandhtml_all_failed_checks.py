"""Tests for issue #1154 — _histLooseEndBandHtml surfaces all failed reconciliation checks.

AC coverage:
  AC1 — _histLooseEndBandHtml iterates over all r.checks where c.ok === false, not only known names
  AC2 — each failing check type (including unknown future types) produces a visible amber band entry
  AC3 — Details block (_histReconPassedHtml) surfaces all failed checks alongside passed ones
  AC4 — existing stale_labels failures render identically (no regression: CTA and detail preserved)
  AC5 — zero failed checks: no amber band; details shows only passing checks (no regression)
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
HISTORY_JS = (REPO_ROOT / "apps" / "dashboard" / "static" / "src" / "sprint-board" / "history.js").read_text(encoding="utf-8")
PROJECT_HTML = (REPO_ROOT / "apps" / "dashboard" / "static" / "project.html").read_text(encoding="utf-8")


def _fn_body(name: str, src: str = HISTORY_JS) -> str:
    """Return the brace-balanced body of a named JS function."""
    for needle in (f"function {name}(", f"{name} = function", f"async function {name}("):
        pos = src.find(needle)
        if pos != -1:
            break
    else:
        raise AssertionError(f"function {name} not found in source")
    brace = src.find("{", pos)
    assert brace != -1
    depth = 0
    for i in range(brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[brace : i + 1]
    raise AssertionError(f"unbalanced braces for {name}")


def _css_exists(selector: str, src: str = PROJECT_HTML) -> bool:
    pat = re.compile(re.escape(selector) + r"(?![\w-])[^{}]*\{")
    return bool(pat.search(src))


# ═══════════════════════════════════════════════════════════════════════════════
# AC1 — Iterates over ALL !c.ok checks, not only hard-coded names
# ═══════════════════════════════════════════════════════════════════════════════

def test_ac1_loose_end_band_accumulates_multiple_bands():
    """_histLooseEndBandHtml must collect all bands instead of returning after the first match."""
    body = _fn_body("_histLooseEndBandHtml")
    # An accumulator pattern (array push + join) is required to emit more than one band.
    assert ".push(" in body, (
        "_histLooseEndBandHtml must accumulate band entries via .push() — "
        "returning after the first match drops non-stale_labels failures (issue #1154 AC1)"
    )
    assert ".join(" in body, (
        "_histLooseEndBandHtml must join accumulated bands before returning"
    )


def test_ac1_loose_end_band_iterates_over_checks_for_generic_path():
    """_histLooseEndBandHtml must have a loop/filter over all !c.ok checks for unknown types."""
    body = _fn_body("_histLooseEndBandHtml")
    # The generic fallback must iterate (filter + forEach, or for...of) rather than only find().
    has_generic_iteration = ".forEach(" in body or (
        body.count(".filter(") >= 2  # one for looseN count, one for generic iteration
    )
    assert has_generic_iteration, (
        "_histLooseEndBandHtml must iterate over remaining !c.ok checks for unknown types — "
        "a single .find() per name silently drops future check types (issue #1154 AC1)"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC2 — Each failing check type produces a visible amber band entry
# ═══════════════════════════════════════════════════════════════════════════════

def test_ac2_generic_band_uses_reconcile_label_for_display():
    """Unknown check types must use _histReconcileLabel to produce a human-readable name."""
    body = _fn_body("_histLooseEndBandHtml")
    assert "_histReconcileLabel" in body, (
        "_histLooseEndBandHtml must call _histReconcileLabel to label unknown check types "
        "so future checks get a readable name in the amber band (issue #1154 AC2)"
    )


def test_ac2_known_checks_excluded_from_generic_path():
    """sprint_pr and stale_labels must be excluded from the generic iteration to avoid double-rendering."""
    body = _fn_body("_histLooseEndBandHtml")
    # Must have an exclusion for the already-handled check names
    has_exclusion = (
        "knownChecks" in body
        or ("sprint_pr" in body and "stale_labels" in body and ".has(" in body)
        or ("sprint_pr" in body and "stale_labels" in body and "!== " in body)
    )
    assert has_exclusion, (
        "_histLooseEndBandHtml must exclude 'sprint_pr' and 'stale_labels' from the generic "
        "iteration path to prevent duplicate amber bands (issue #1154 AC2)"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC3 — Details block surfaces failed checks alongside passed ones
# ═══════════════════════════════════════════════════════════════════════════════

def test_ac3_recon_passed_fn_also_renders_failed_checks():
    """_histReconPassedHtml must filter for !c.ok checks and render them in the Details block."""
    body = _fn_body("_histReconPassedHtml")
    assert "!c.ok" in body or "c.ok === false" in body, (
        "_histReconPassedHtml must also render failed (!c.ok) checks in the Details section — "
        "currently only passed checks are shown, silently hiding failures (issue #1154 AC3)"
    )


def test_ac3_recon_failed_item_css_class_in_details_fn():
    """_histReconPassedHtml must apply recon-failed-item class to distinguish failed checks."""
    body = _fn_body("_histReconPassedHtml")
    assert "recon-failed-item" in body, (
        "_histReconPassedHtml must use the 'recon-failed-item' CSS class on failed check items "
        "so they are visually distinct from passed ones in the Details block (issue #1154 AC3)"
    )


def test_ac3_recon_failed_item_css_exists_in_project_html():
    """CSS for .recon-failed-item must exist to style failed checks in the Details block."""
    assert _css_exists(".recon-failed-item"), (
        "CSS rule for .recon-failed-item must exist in project.html — "
        "failed checks in the Details block need distinct styling (issue #1154 AC3)"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC4 — stale_labels and sprint_pr failures still render identically (no regression)
# ═══════════════════════════════════════════════════════════════════════════════

def test_ac4_stale_labels_explicit_handler_preserved():
    """stale_labels must still be explicitly handled with its CTA in _histLooseEndBandHtml."""
    body = _fn_body("_histLooseEndBandHtml")
    assert "stale_labels" in body, (
        "_histLooseEndBandHtml must still explicitly check for 'stale_labels' (issue #1154 AC4)"
    )
    assert "_histClearStaleLabels" in body, (
        "stale_labels band must still include the 'Clear labels' CTA button (issue #1154 AC4)"
    )


def test_ac4_sprint_pr_explicit_handler_preserved():
    """sprint_pr must still be explicitly handled with the 'Merge PR' CTA."""
    body = _fn_body("_histLooseEndBandHtml")
    assert "sprint_pr" in body, (
        "_histLooseEndBandHtml must still explicitly check for 'sprint_pr' (issue #1154 AC4)"
    )
    assert "Merge PR" in body, (
        "sprint_pr band must still render the 'Merge PR' CTA (issue #1154 AC4)"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC5 — Zero failed checks: no amber band, details shows passing checks only
# ═══════════════════════════════════════════════════════════════════════════════

def test_ac5_empty_result_when_no_failures():
    """_histLooseEndBandHtml must return '' when all checks pass and there are no stale branches."""
    body = _fn_body("_histLooseEndBandHtml")
    # Either an explicit return '' or join on an empty array (which produces '')
    has_empty_path = (
        "return ''" in body
        or 'return ""' in body
        or ".join(" in body  # bands.join('') on empty array === ''
    )
    assert has_empty_path, (
        "_histLooseEndBandHtml must return '' when there are no loose ends — "
        "happy-path cards must not show any amber band (issue #1154 AC5)"
    )


def test_ac5_recon_passed_fn_still_renders_passed_checks():
    """_histReconPassedHtml must still render passed (c.ok === true) checks for the all-pass case."""
    body = _fn_body("_histReconPassedHtml")
    assert "c.ok" in body or "!!c.ok" in body, (
        "_histReconPassedHtml must still filter for passed checks — "
        "all-pass cards should show green checkmarks in the Details block (issue #1154 AC5)"
    )
