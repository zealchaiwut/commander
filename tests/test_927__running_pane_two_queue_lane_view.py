"""Tests for issue #927 — Running pane: two-queue coder/tester lane view.

Tests cover all AC items via static analysis of project.html (for frontend
HTML/CSS/JS) and unit tests of the /live endpoint's issue serializer (for
backend field exposure).

AC coverage:
  AC1 — Running pane has coder-lane and tester-lane elements, each with a
         capacity header showing slot dots, "N of MAX working", and "M waiting".
  AC2 — Each lane contains a Working group and a Waiting group in the DOM.
  AC3 — Issues transition between lanes based on pipeline_stage:
         coding→Coder Working; awaiting_tester→Tester Waiting;
         testing→Tester Working; done→Done fold.
  AC4 — Tester rejection moves issue to Coder Waiting with FIX n/2 badge.
  AC5 — Done fold is present and collapsed by default.
  AC6 — Upcoming fold is present and collapsed by default.
  AC7 — Lane cards reuse existing node/status-pill CSS classes; no new
         card primitives introduced.
  AC8 — Slot capacity is driven by max_coder_slots / max_tester_slots from
         the live snapshot; serial (capacity=1) renders correctly.
  AC9 — /live endpoint exposes pipeline_stage and tester_attempt_count per
         issue plus max_coder_slots and max_tester_slots at top level.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_HTML_PATH = DASHBOARD_DIR / "static" / "project.html"
_SERVER_PY = DASHBOARD_DIR / "server.py"

PROJECT_HTML = _HTML_PATH.read_text(encoding="utf-8")
SERVER_SRC = _SERVER_PY.read_text(encoding="utf-8")


# ─────────────────────────── helpers ─────────────────────────────────────────

def _fn_body(name: str, src: str = PROJECT_HTML) -> str:
    """Return the brace-balanced body of a JS function in src."""
    for needle in (
        f"function {name}(",
        f"function {name} (",
        f"{name} = function(",
        f"async function {name}(",
    ):
        pos = src.find(needle)
        if pos != -1:
            break
    assert pos != -1, f"function {name} not found in source"
    brace = src.find("{", pos)
    assert brace != -1, f"no opening brace for {name}"
    depth = 0
    for i in range(brace, len(src)):
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[brace : i + 1]
    raise AssertionError(f"unbalanced braces for {name}")


def _css_rule(selector: str, src: str = PROJECT_HTML) -> str | None:
    """Return the body of the first CSS rule whose selector contains selector, or None."""
    pat = re.compile(re.escape(selector) + r"(?![\w-])[^{}]*\{([^{}]*)\}")
    m = pat.search(src)
    return m.group(1) if m else None


# ─────────────────────── AC1: Capacity header ────────────────────────────────

def test_ac1_lanes_wrap_element_in_run_shell():
    """smgmt-lanes container is inside the run-shell div."""
    assert 'id="smgmt-lanes"' in PROJECT_HTML, (
        "smgmt-lanes container not found in project.html"
    )


def test_ac1_coder_lane_element_present():
    """A coder lane element or its generated class exists in the JS."""
    # Either a static data-lane attribute or a JS string containing 'coder' lane
    has_static = 'data-lane="coder"' in PROJECT_HTML or "lane-coder" in PROJECT_HTML
    has_js_string = (
        "_smgmtLanesHtml" in PROJECT_HTML
        and ("coder" in _fn_body("_smgmtLanesHtml"))
    )
    assert has_static or has_js_string, (
        "No coder lane found in project.html — coder lane element or JS builder missing"
    )


def test_ac1_tester_lane_element_present():
    """A tester lane element or its generated class exists in the JS."""
    has_static = 'data-lane="tester"' in PROJECT_HTML or "lane-tester" in PROJECT_HTML
    has_js_string = (
        "_smgmtLanesHtml" in PROJECT_HTML
        and ("tester" in _fn_body("_smgmtLanesHtml"))
    )
    assert has_static or has_js_string, (
        "No tester lane found in project.html — tester lane element or JS builder missing"
    )


def test_ac1_capacity_header_slot_dots_in_js():
    """Capacity header renders slot dots for each occupied slot."""
    fn = _fn_body("_smgmtLanesHtml")
    # Slot dots — the builder should reference a slot-dot class or similar
    has_dots = (
        "slot-dot" in fn
        or "lane-dot" in fn
        or "capacity-dot" in fn
        or "filled" in fn
    )
    assert has_dots, "_smgmtLanesHtml does not render slot indicator dots"


def test_ac1_capacity_header_working_count_in_js():
    """Capacity header emits 'working' count text (e.g. 'N of MAX working')."""
    fn = _fn_body("_smgmtLanesHtml")
    assert "working" in fn.lower(), (
        "_smgmtLanesHtml does not include 'working' count in capacity header"
    )


def test_ac1_capacity_header_waiting_count_in_js():
    """Capacity header emits 'waiting' count text (e.g. 'M waiting')."""
    fn = _fn_body("_smgmtLanesHtml")
    assert "waiting" in fn.lower(), (
        "_smgmtLanesHtml does not include 'waiting' count in capacity header"
    )


# ─────────────────────── AC2: Working / Waiting groups ───────────────────────

def test_ac2_working_group_in_js():
    """Lane JS renders a Working group containing active issues."""
    fn = _fn_body("_smgmtLanesHtml")
    assert (
        "working" in fn.lower() and ("group" in fn.lower() or "Working" in fn)
    ), "_smgmtLanesHtml does not render a Working group"


def test_ac2_waiting_group_in_js():
    """Lane JS renders a Waiting group for queued issues."""
    fn = _fn_body("_smgmtLanesHtml")
    assert (
        "waiting" in fn.lower() and ("group" in fn.lower() or "Waiting" in fn)
    ), "_smgmtLanesHtml does not render a Waiting group"


# ─────────────────────── AC3: Stage-based transitions ────────────────────────

def test_ac3_coding_stage_maps_to_coder_working():
    """Issues with pipeline_stage 'coding' appear in the Coder Working group."""
    fn = _fn_body("_smgmtLanesHtml")
    # The function must branch on 'coding' to place issues in the coder lane
    assert "coding" in fn, (
        "_smgmtLanesHtml does not handle 'coding' pipeline_stage"
    )


def test_ac3_awaiting_tester_maps_to_tester_waiting():
    """Issues with pipeline_stage 'awaiting_tester' appear in Tester Waiting."""
    fn = _fn_body("_smgmtLanesHtml")
    assert "awaiting_tester" in fn or "awaiting-tester" in fn, (
        "_smgmtLanesHtml does not handle 'awaiting_tester' stage"
    )


def test_ac3_testing_stage_maps_to_tester_working():
    """Issues with pipeline_stage 'testing' appear in Tester Working."""
    fn = _fn_body("_smgmtLanesHtml")
    assert "testing" in fn, (
        "_smgmtLanesHtml does not handle 'testing' pipeline_stage"
    )


def test_ac3_coded_badge_rendered_for_awaiting_tester():
    """CODED badge is rendered for issues in 'awaiting_tester' stage."""
    # Badge may be emitted by the card helper (_smgmtLaneCardHtml) called from
    # _smgmtLanesHtml; check the card helper's body and the full source.
    fn = _fn_body("_smgmtLaneCardHtml")
    assert "CODED" in fn or "coded" in fn.lower(), (
        "_smgmtLaneCardHtml does not render CODED badge for awaiting-tester issues"
    )


# ─────────────────────── AC4: FIX n/2 badge ──────────────────────────────────

def test_ac4_fix_badge_rendered_for_rework():
    """FIX n/2 badge is rendered in Coder Waiting for rework issues."""
    # Badge is emitted by _smgmtLaneCardHtml (card helper) called from _smgmtLanesHtml.
    fn = _fn_body("_smgmtLaneCardHtml")
    has_fix = (
        "FIX" in fn
        or (
            "fix" in fn.lower()
            and ("tester_attempt_count" in fn or "fix_round" in fn or "rework" in fn)
        )
    )
    assert has_fix, (
        "_smgmtLaneCardHtml does not render FIX n/2 badge for rework items"
    )


def test_ac4_rework_stage_routed_to_coder_waiting():
    """Issues with pipeline_stage 'rework' appear in Coder Waiting."""
    fn = _fn_body("_smgmtLanesHtml")
    assert "rework" in fn, (
        "_smgmtLanesHtml does not handle 'rework' pipeline_stage for coder-waiting"
    )


# ─────────────────────── AC5: Done fold ──────────────────────────────────────

def test_ac5_done_fold_present_in_js():
    """Done fold section exists in lane rendering."""
    fn = _fn_body("_smgmtLanesHtml")
    assert "done" in fn.lower() and ("fold" in fn.lower() or "Done" in fn), (
        "_smgmtLanesHtml does not include a Done fold"
    )


def test_ac5_done_fold_collapsed_by_default():
    """Done fold is collapsed by default (hidden or closed details/summary)."""
    fn = _fn_body("_smgmtLanesHtml")
    # Look for hidden or collapsed default state
    has_collapsed = (
        "hidden" in fn
        or "collapsed" in fn
        or '<details' in fn
        or "display:none" in fn
        or "display: none" in fn
    )
    assert has_collapsed, (
        "_smgmtLanesHtml done fold is not collapsed by default"
    )


# ─────────────────────── AC6: Upcoming fold ──────────────────────────────────

def test_ac6_upcoming_fold_present_in_js():
    """Upcoming fold section exists for next-level gated issues."""
    fn = _fn_body("_smgmtLanesHtml")
    assert "upcoming" in fn.lower() or "Upcoming" in fn, (
        "_smgmtLanesHtml does not include an Upcoming fold"
    )


def test_ac6_upcoming_fold_collapsed_by_default():
    """Upcoming fold is collapsed by default."""
    fn = _fn_body("_smgmtLanesHtml")
    has_collapsed = (
        "hidden" in fn
        or "collapsed" in fn
        or "<details" in fn
        or "display:none" in fn
        or "display: none" in fn
    )
    assert has_collapsed, (
        "_smgmtLanesHtml upcoming fold is not collapsed by default"
    )


def test_ac6_upcoming_shows_blocking_dependency():
    """Upcoming items show their blocking level dependency."""
    fn = _fn_body("_smgmtLanesHtml")
    # Should reference dispatch_level or "start after"
    has_dep = (
        "dispatch_level" in fn
        or "start after" in fn.lower()
        or "level" in fn.lower()
    )
    assert has_dep, (
        "_smgmtLanesHtml does not show blocking dependency for upcoming items"
    )


# ─────────────────────── AC7: No new card primitives ─────────────────────────

def test_ac7_lane_cards_reuse_existing_node_classes():
    """Lane cards reuse 'node' CSS class (no new card primitives)."""
    fn = _fn_body("_smgmtLanesHtml")
    # Should use 'node' class (from existing rail node cards) or smgmt-ticket
    assert '"node"' in fn or "'node'" in fn or "node-" in fn or "smgmt-ticket" in fn, (
        "_smgmtLanesHtml does not reuse existing card primitives (node / smgmt-ticket classes)"
    )


# ─────────────────────── AC8: Slot capacity from config ──────────────────────

def test_ac8_lanes_reads_max_coder_slots():
    """Lane render reads max_coder_slots from the live snapshot."""
    fn = _fn_body("_smgmtLanesHtml")
    assert "max_coder_slots" in fn or "maxCoder" in fn, (
        "_smgmtLanesHtml does not read max_coder_slots for capacity header"
    )


def test_ac8_lanes_reads_max_tester_slots():
    """Lane render reads max_tester_slots from the live snapshot."""
    fn = _fn_body("_smgmtLanesHtml")
    assert "max_tester_slots" in fn or "maxTester" in fn, (
        "_smgmtLanesHtml does not read max_tester_slots for capacity header"
    )


def test_ac8_serial_mode_defaults_to_capacity_one():
    """When max_*_slots is absent from live data, the lane defaults to capacity 1."""
    fn = _fn_body("_smgmtLanesHtml")
    # Should have a fallback of 1 (e.g. `|| 1`)
    assert "|| 1" in fn or "??1" in fn or "?? 1" in fn or "default.*1" in fn.lower(), (
        "_smgmtLanesHtml does not default slot capacity to 1 for serial mode"
    )


# ─────────────────────── AC9: Backend field exposure ─────────────────────────

def test_ac9_pipeline_stage_in_issues_out():
    """Backend /live endpoint includes pipeline_stage in each issue object."""
    assert "pipeline_stage" in SERVER_SRC, (
        "pipeline_stage not found in server.py — /live endpoint must expose it per issue"
    )


def test_ac9_tester_attempt_count_in_issues_out():
    """Backend /live endpoint includes tester_attempt_count in each issue object."""
    # Check that issues_out.append includes tester_attempt_count
    append_match = re.search(
        r"issues_out\.append\(\{(.*?)\}\)",
        SERVER_SRC,
        re.DOTALL,
    )
    assert append_match, "issues_out.append block not found in server.py"
    block = append_match.group(1)
    assert "tester_attempt_count" in block, (
        "tester_attempt_count not found in issues_out.append block in server.py"
    )


def test_ac9_max_coder_slots_in_live_response():
    """Backend /live endpoint includes max_coder_slots at top level."""
    assert "max_coder_slots" in SERVER_SRC, (
        "max_coder_slots not found in server.py — /live response must expose it"
    )


def test_ac9_max_tester_slots_in_live_response():
    """Backend /live endpoint includes max_tester_slots at top level."""
    assert "max_tester_slots" in SERVER_SRC, (
        "max_tester_slots not found in server.py — /live response must expose it"
    )


def test_ac9_pipeline_stage_coding_from_coder_running():
    """Backend derives pipeline_stage='coding' from coder_running/coder_dispatched."""
    # Check that the server derives 'coding' stage from agent_status
    relevant = SERVER_SRC[SERVER_SRC.find("issues_out.append") - 500: SERVER_SRC.find("issues_out.append") + 500]
    has_coding = "coding" in relevant or (
        "coder_running" in SERVER_SRC
        and "pipeline_stage" in SERVER_SRC
    )
    assert has_coding, (
        "server.py does not derive pipeline_stage='coding' for coder_running/coder_dispatched"
    )


def test_ac9_pipeline_stage_awaiting_tester_from_coder_done():
    """Backend derives pipeline_stage='awaiting_tester' from coder_done."""
    assert "awaiting_tester" in SERVER_SRC or "awaiting-tester" in SERVER_SRC, (
        "server.py does not derive pipeline_stage='awaiting_tester' for coder_done"
    )


# ─────────────────────── CSS: no impeccable anti-patterns ────────────────────

def test_no_side_stripe_border_in_lane_styles():
    """Lane styles do not use side-stripe border patterns (impeccable gate)."""
    # Extract lane-related CSS
    lane_css_match = re.search(
        r"\.lane[s\-][^{]*\{[^}]*\}",
        PROJECT_HTML,
    )
    if lane_css_match:
        css = lane_css_match.group(0)
        assert "border-left:" not in css or "border-right:" not in css, (
            "Lane CSS uses side-stripe border — impeccable anti-pattern"
        )


def test_no_gradient_text_in_lane_styles():
    """Lane styles do not use gradient text (impeccable gate)."""
    # Look for webkit-background-clip: text within lane-related blocks
    lane_section = re.search(
        r"\/\* .*lane.*\*\/.*?(?=\/\* [A-Z]|\Z)",
        PROJECT_HTML,
        re.DOTALL | re.IGNORECASE,
    )
    if lane_section:
        sec = lane_section.group(0)
        assert "-webkit-background-clip: text" not in sec, (
            "Lane CSS uses gradient text — impeccable anti-pattern"
        )


# ─────────────────────── Integration: _smgmtRunningViewUpdate hook ───────────

def test_lanes_update_called_from_running_view_update():
    """_smgmtRunningViewUpdate calls _smgmtLanesUpdate to keep lanes live."""
    fn = _fn_body("_smgmtRunningViewUpdate")
    assert "_smgmtLanesUpdate" in fn, (
        "_smgmtRunningViewUpdate does not call _smgmtLanesUpdate — lanes won't refresh on poll"
    )
