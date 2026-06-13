"""Frontend wiring tests for issue #861 — Plan next sprint button + card state.

Anchored to AC1 (the button is present on the board toolbar and wired to the
handler) and AC9 (the pending-sign-off card is visually distinct). These assert
the shipped markup/bundle so the button is not a dead control.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PROJECT_HTML = ROOT / "apps" / "dashboard" / "static" / "project.html"
PLAN_NEXT_JS = ROOT / "apps" / "dashboard" / "static" / "src" / "sprint-board" / "plan-next.js"
BUNDLE = ROOT / "apps" / "dashboard" / "static" / "dist" / "bundle.js"


@pytest.fixture(scope="module")
def html():
    return PROJECT_HTML.read_text(encoding="utf-8")


# ── AC1: the button exists on the board toolbar and is wired to the handler ───

def test_plan_next_button_present_and_wired(html):
    assert 'id="smgmt-plan-next-btn"' in html
    assert 'onclick="smgmtPlanNextSprint()"' in html
    assert "Plan next sprint" in html


def test_plan_next_handler_module_exists():
    src = PLAN_NEXT_JS.read_text(encoding="utf-8")
    assert "export async function smgmtPlanNextSprint" in src
    assert "/api/sprints/plan-next" in src
    # AC11: a conflict prompts to replace rather than silently duplicating.
    assert "window.confirm" in src
    assert "replace" in src


def test_plan_next_exposed_in_bundle():
    """The handler is bundled and exposed as a global so onclick resolves."""
    bundle = BUNDLE.read_text(encoding="utf-8")
    assert "smgmtPlanNextSprint" in bundle
    assert "_smgmtLoadPendingSignoff" in bundle


# ── AC9: pending-sign-off card is visually distinct ───────────────────────────

def test_pending_signoff_card_styling_present(html):
    assert ".smgmt-sprint-card.smgmt-pending-signoff" in html
    assert ".smgmt-pending-signoff-badge" in html


def test_pending_signoff_decorator_marks_cards():
    src = PLAN_NEXT_JS.read_text(encoding="utf-8")
    assert "export async function _smgmtLoadPendingSignoff" in src
    assert "smgmt-pending-signoff" in src
    assert "/api/sprints/pending-signoff" in src
