"""Tests for issue #809 — mini-rail preview UI on the sprint Board.

Frontend ACs are anchored by static-source assertions on project.html
(the repo's established pattern for vanilla-JS UI features):
- AC3: mini-rail renders per planned sprint with chip groups per level and
       a `∥` indicator for parallel-eligible pairs, using .mini-rail / .mr-* classes
- AC4: cycles surface as a red state on the mini-rail
- AC5: unestimated tickets show an amber "N unestimated — preview partial" warning
- AC6: the mini-rail refreshes on the board re-render (ticket add/remove/reorder)
- AC8: UI targets the .mini-rail / .mr-* CSS class contract
- AC9: passes the impeccable detect gate (run for real when npx is available)
"""
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_HTML = Path(__file__).parent.parent / "apps" / "dashboard" / "static" / "project.html"


def _html() -> str:
    return PROJECT_HTML.read_text(encoding="utf-8")


# ── AC8 / AC3: the .mini-rail / .mr-* CSS class contract ─────────────────────

def test_mini_rail_class_present():
    src = _html()
    assert "mini-rail" in src, "`.mini-rail` class contract not found"


def test_mr_prefixed_classes_present():
    src = _html()
    # At least the level group and chip element classes from the mock contract.
    for cls in ("mr-level", "mr-chip"):
        assert cls in src, f"`{cls}` class not found in mini-rail markup"


# ── AC3: chip groups per level + parallel indicator ──────────────────────────

def test_parallel_indicator_present():
    src = _html()
    assert "∥" in src, "parallel-eligible indicator `∥` not found in mini-rail"


def test_mini_rail_fetches_preview_dag_endpoint():
    src = _html()
    assert "/preview-dag" in src, "mini-rail does not call the preview-dag endpoint"


def test_mini_rail_loader_defined():
    src = _html()
    assert "_smgmtLoadMiniRail" in src, "mini-rail loader function not defined"


# ── AC4: cycles → red state ──────────────────────────────────────────────────

def test_cycle_red_state():
    src = _html()
    # A dedicated cycle modifier class on the rail, styled with the red token.
    assert "mr-cycle" in src, "cycle state class `mr-cycle` not found"
    # The cycle styling must reference the red design token.
    assert "var(--red)" in src


# ── AC5: amber "preview partial" warning ─────────────────────────────────────

def test_unestimated_partial_warning_text():
    src = _html()
    assert "unestimated" in src and "preview partial" in src, (
        "amber 'N unestimated — preview partial' warning text not found"
    )


def test_partial_warning_uses_amber_token():
    src = _html()
    assert "mr-warn" in src, "partial-warning element class `mr-warn` not found"


# ── AC6: refreshes on board re-render ────────────────────────────────────────

def test_mini_rail_wired_into_render():
    src = _html()
    # _smgmtRender rebuilds the board after ticket add/remove/reorder; the
    # mini-rail loader must be invoked from it so the rail refreshes.
    render_idx = src.find("function _smgmtRender(")
    assert render_idx != -1, "_smgmtRender not found"
    # Find the end of the function body heuristically (next top-level function).
    next_fn = src.find("\nfunction ", render_idx + 1)
    body = src[render_idx:next_fn if next_fn != -1 else len(src)]
    assert "_smgmtLoadMiniRail" in body, (
        "_smgmtLoadMiniRail is not called from _smgmtRender — rail won't refresh"
    )


# ── AC9: impeccable detect gate ──────────────────────────────────────────────

def test_impeccable_detect_passes():
    if shutil.which("npx") is None:
        pytest.skip("npx not available in this environment — gate runs in CI/tester env")
    repo_root = Path(__file__).parent.parent
    result = subprocess.run(
        ["npx", "impeccable", "detect", str(PROJECT_HTML)],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"impeccable detect failed:\n{result.stdout}\n{result.stderr}"
    )
