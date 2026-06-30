"""Tests for issue #867: Icon/color pickers - add arrow-key keyboard navigation (runs against UAT)"""
import os
import pytest


# Resolved from UAT .env at runtime; see tester skill Step 0.
# Default kept only as a last-resort fallback if BASE_URL not exported.
BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "8001")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )


def test_icon_picker_onkeydown_handler_registered():
    """AC: Icon picker has onkeydown handler registered."""
    pytest.skip("manual — keyboard navigation and focus tested via browser UAT steps, not HTTP")


def test_color_picker_onkeydown_handler_registered():
    """AC: Color picker has onkeydown handler registered."""
    pytest.skip("manual — keyboard navigation and focus tested via browser UAT steps, not HTTP")


def test_icon_picker_arrow_right_advances_selection():
    """AC: Icon picker handles ArrowRight and ArrowDown to move focus/selection to next icon."""
    pytest.skip("manual — tested in UAT step 1 (ArrowRight) and step 3 (ArrowDown)")


def test_icon_picker_arrow_left_retreats_selection():
    """AC: Icon picker handles ArrowLeft and ArrowUp to move focus/selection to previous icon."""
    pytest.skip("manual — tested in UAT step 2 (ArrowLeft) and step 3 (ArrowUp)")


def test_color_picker_arrow_right_advances_selection():
    """AC: Color picker handles ArrowRight and ArrowDown to move focus/selection to next color."""
    pytest.skip("manual — tested in UAT step 4 (ArrowRight) and step 4 (ArrowDown)")


def test_color_picker_arrow_left_retreats_selection():
    """AC: Color picker handles ArrowLeft and ArrowUp to move focus/selection to previous color."""
    pytest.skip("manual — tested in UAT step 4 (ArrowLeft) and step 4 (ArrowUp)")


def test_roving_tabindex_maintained_after_navigation():
    """AC: After Arrow-key navigation, newly focused option has tabindex=0 and others have tabindex=-1."""
    pytest.skip("manual — DOM tabindex pattern verified in UAT step 6")
