"""Tests for issue #1108 — Reduce running-pane lanes to chip-only assignment map.

Replaces full lane cards (title + logs) with compact number-only chips in the
Coder/Tester Working and Waiting rows. Verified against static analysis of
project.html (HTML/CSS/JS) — no server needed.

AC coverage:
  AC1 — Each lane header shows agent icon, lane name, and capacity string
         (e.g. `1 of 1 working · 1 waiting`).
  AC2 — Under the header, exactly two rows exist: Working and Waiting.
  AC3 — Each row lists issues as number-only chips (#519) — no titles, no logs.
  AC4 — Working chips render with a blue tint and a small animated pulse dot.
  AC5 — Waiting chips render grey by default; retry chips render amber.
  AC6 — Empty Working group displays `— idle`; empty Waiting group displays `—`.
  AC7 — Serial mode: Working holds at most one chip; parallel up to capacity.
  AC8 — Clicking any chip scrolls to and highlights that issue's card in All Issues.
  AC9 — No issue title text and no log output appears inside a lane.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_HTML_PATH = DASHBOARD_DIR / "static" / "project.html"
PROJECT_HTML = _HTML_PATH.read_text(encoding="utf-8")


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


def _css_has(selector: str, prop: str, src: str = PROJECT_HTML) -> bool:
    """Return True if a CSS rule containing selector also contains prop."""
    pattern = re.compile(
        re.escape(selector) + r"(?![\w-])[^{]*\{([^{}]*)\}",
        re.DOTALL,
    )
    for m in pattern.finditer(src):
        if prop in m.group(1):
            return True
    return False


def _css_rule_exists(selector: str, src: str = PROJECT_HTML) -> bool:
    """Return True if a CSS rule with this selector exists."""
    pattern = re.compile(re.escape(selector) + r"(?![\w-])[^{]*\{")
    return bool(pattern.search(src))


# ──────────────────────────── AC1: Lane header ───────────────────────────────


def test_ac1_coder_lane_header_has_icon():
    """Coder lane header includes a Tabler icon element (ti- class)."""
    fn = _fn_body("_smgmtLanesHtml")
    # The coder lane HTML must include an icon; look for ti- class in the coder section.
    # We accept ti-code, ti-robot, or any ti-* that appears in the coder lane block.
    has_icon = "ti-" in fn and ("coder" in fn.lower())
    assert has_icon, (
        "_smgmtLanesHtml coder lane does not include an agent icon (ti-* class)"
    )


def test_ac1_tester_lane_header_has_icon():
    """Tester lane header includes a Tabler icon element (ti- class)."""
    fn = _fn_body("_smgmtLanesHtml")
    has_icon = "ti-" in fn and ("tester" in fn.lower())
    assert has_icon, (
        "_smgmtLanesHtml tester lane does not include an agent icon (ti-* class)"
    )


def test_ac1_capacity_string_working():
    """Capacity string includes 'working' text."""
    fn = _fn_body("_smgmtLanesHtml")
    assert "working" in fn.lower(), (
        "_smgmtLanesHtml does not include 'working' in the capacity string"
    )


def test_ac1_capacity_string_waiting():
    """Capacity string includes 'waiting' text."""
    fn = _fn_body("_smgmtLanesHtml")
    assert "waiting" in fn.lower(), (
        "_smgmtLanesHtml does not include 'waiting' in the capacity string"
    )


# ──────────────────────── AC2: Working and Waiting rows ──────────────────────


def test_ac2_working_row_exists():
    """Lane contains a Working row."""
    fn = _fn_body("_smgmtLanesHtml")
    assert "Working" in fn, (
        "_smgmtLanesHtml does not render a Working row"
    )


def test_ac2_waiting_row_exists():
    """Lane contains a Waiting row."""
    fn = _fn_body("_smgmtLanesHtml")
    assert "Waiting" in fn, (
        "_smgmtLanesHtml does not render a Waiting row"
    )


# ──────────────────── AC3: Number-only chips — no titles/logs ────────────────


def test_ac3_chip_function_or_inline_chip_html_exists():
    """A chip-rendering function (_smgmtLaneChipHtml) or inline chip class exists."""
    has_chip_fn = "_smgmtLaneChipHtml" in PROJECT_HTML
    has_chip_class = "lane-chip" in PROJECT_HTML
    assert has_chip_fn or has_chip_class, (
        "No chip-rendering function or lane-chip class found — chips not implemented"
    )


def test_ac3_chips_show_issue_number():
    """Chip HTML includes the issue number (#N)."""
    if "_smgmtLaneChipHtml" in PROJECT_HTML:
        fn = _fn_body("_smgmtLaneChipHtml")
        has_num = "#" in fn or "num" in fn.lower() or "number" in fn.lower()
        assert has_num, "_smgmtLaneChipHtml does not include issue number"
    else:
        fn = _fn_body("_smgmtLanesHtml")
        assert "#" in fn or "num" in fn.lower(), (
            "_smgmtLanesHtml chip rendering does not include issue number"
        )


def test_ac3_lane_body_does_not_render_titles():
    """Lane chip rendering does not include node-title or smgmt-ticket-title."""
    if "_smgmtLaneChipHtml" in PROJECT_HTML:
        fn = _fn_body("_smgmtLaneChipHtml")
    else:
        fn = _fn_body("_smgmtLanesHtml")
    assert "node-title" not in fn, (
        "Chip renderer includes node-title — titles must not appear inside a lane"
    )
    assert "smgmt-ticket-title" not in fn, (
        "Chip renderer includes smgmt-ticket-title — titles must not appear inside a lane"
    )


def test_ac3_lane_body_does_not_render_logs():
    """Lane chip rendering does not include log output or log classes."""
    if "_smgmtLaneChipHtml" in PROJECT_HTML:
        fn = _fn_body("_smgmtLaneChipHtml")
    else:
        fn = _fn_body("_smgmtLanesHtml")
    assert "log-body" not in fn and "log-line" not in fn, (
        "Chip renderer includes log output — logs must not appear inside a lane"
    )


# ─────────────────── AC4: Working chips — blue tint + pulse dot ──────────────


def test_ac4_lane_chip_working_css_exists():
    """CSS class .lane-chip--working exists with blue tint styling."""
    assert _css_rule_exists(".lane-chip--working"), (
        "CSS rule for .lane-chip--working not found — working chip styling missing"
    )


def test_ac4_lane_chip_working_has_blue_tint():
    """Working chip CSS uses blue background token."""
    rule_ok = _css_has(".lane-chip--working", "blue") or _css_has(
        ".lane-chip--working", "--blue"
    )
    # Also accept if the rule body contains blue-bg or var(--blue
    pattern = re.compile(
        r"\.lane-chip--working(?![\w-])[^{]*\{([^{}]*)\}", re.DOTALL
    )
    m = pattern.search(PROJECT_HTML)
    if m:
        body = m.group(1)
        rule_ok = "blue" in body
    assert rule_ok, (
        ".lane-chip--working does not use blue color token — working chip must have blue tint"
    )


def test_ac4_pulse_dot_animation_defined():
    """An animation for the pulse dot is defined (laneChipPulse or lane-chip-pulse)."""
    has_anim = (
        "laneChipPulse" in PROJECT_HTML
        or "lane-chip-pulse" in PROJECT_HTML
        or "lane-pulse" in PROJECT_HTML
        or "@keyframes" in PROJECT_HTML  # verify it's in context
    )
    # More specific: look for a keyframes animation near lane-chip
    idx = PROJECT_HTML.find("lane-chip")
    if idx != -1:
        surrounding = PROJECT_HTML[max(0, idx - 200) : idx + 2000]
        has_anim = "@keyframes" in surrounding or "laneChipPulse" in PROJECT_HTML
    assert has_anim, (
        "No pulse animation found for lane chip — working chip must have animated pulse dot"
    )


def test_ac4_pulse_dot_class_exists():
    """A CSS class for the pulse dot exists (.lane-chip-pulse or similar)."""
    has_pulse = (
        "lane-chip-pulse" in PROJECT_HTML
        or "lane-pulse-dot" in PROJECT_HTML
        or "chip-pulse" in PROJECT_HTML
    )
    assert has_pulse, (
        "No pulse dot CSS class found — working chip must have a visible animated pulse dot"
    )


def test_ac4_working_chip_includes_pulse_dot():
    """Working chip HTML includes the pulse dot element."""
    if "_smgmtLaneChipHtml" in PROJECT_HTML:
        fn = _fn_body("_smgmtLaneChipHtml")
    else:
        fn = _fn_body("_smgmtLanesHtml")
    has_dot = (
        "lane-chip-pulse" in fn
        or "lane-pulse-dot" in fn
        or "chip-pulse" in fn
    )
    assert has_dot, (
        "Working chip HTML does not include pulse dot — working chip must show animated pulse"
    )


# ──────────────────── AC5: Waiting chips grey; retry chips amber ──────────────


def test_ac5_lane_chip_base_css_exists():
    """CSS class .lane-chip exists as the base chip style."""
    assert _css_rule_exists(".lane-chip"), (
        "CSS rule .lane-chip not found — base chip style missing"
    )


def test_ac5_lane_chip_retry_css_exists():
    """CSS class .lane-chip--retry exists with amber styling."""
    assert _css_rule_exists(".lane-chip--retry"), (
        "CSS rule for .lane-chip--retry not found — retry chip amber styling missing"
    )


def test_ac5_lane_chip_retry_has_amber_tint():
    """.lane-chip--retry uses amber color token."""
    pattern = re.compile(
        r"\.lane-chip--retry(?![\w-])[^{]*\{([^{}]*)\}", re.DOTALL
    )
    m = pattern.search(PROJECT_HTML)
    assert m, ".lane-chip--retry rule not found"
    body = m.group(1)
    assert "amber" in body, (
        ".lane-chip--retry does not use amber color token"
    )


def test_ac5_retry_chips_rendered_for_rework_issues():
    """Chip HTML applies --retry modifier for issues with tester_attempt_count > 0 or coder retry."""
    if "_smgmtLaneChipHtml" in PROJECT_HTML:
        fn = _fn_body("_smgmtLaneChipHtml")
    else:
        fn = _fn_body("_smgmtLanesHtml")
    has_retry = (
        "retry" in fn.lower()
        or "tester_attempt_count" in fn
        or "coder_attempt" in fn
        or "isRetry" in fn
        or "is_retry" in fn
    )
    assert has_retry, (
        "Chip renderer does not apply retry/amber modifier — retry chips must render amber"
    )


# ──────────────── AC6: Empty states — idle / — ───────────────────────────────


def test_ac6_working_empty_shows_idle():
    """Empty Working group displays '— idle' placeholder."""
    fn = _fn_body("_smgmtLanesHtml")
    assert "idle" in fn.lower(), (
        "_smgmtLanesHtml does not render '— idle' for empty Working group"
    )


def test_ac6_waiting_empty_shows_dash():
    """Empty Waiting group displays '—' placeholder."""
    fn = _fn_body("_smgmtLanesHtml")
    assert "—" in fn or "\\u2014" in fn or "&mdash;" in fn, (
        "_smgmtLanesHtml does not render '—' for empty Waiting group"
    )


# ─────────────── AC7: Serial vs parallel mode capacity ───────────────────────


def test_ac7_capacity_driven_by_max_slots():
    """Working row capacity is driven by max_coder_slots / max_tester_slots from the live snapshot."""
    fn = _fn_body("_smgmtLanesHtml")
    assert "max_coder_slots" in fn or "maxCoder" in fn, (
        "_smgmtLanesHtml does not read max_coder_slots for capacity"
    )
    assert "max_tester_slots" in fn or "maxTester" in fn, (
        "_smgmtLanesHtml does not read max_tester_slots for capacity"
    )


def test_ac7_serial_mode_defaults_to_one():
    """Serial mode defaults to capacity 1 when max slots is absent."""
    fn = _fn_body("_smgmtLanesHtml")
    assert "|| 1" in fn or "?? 1" in fn or "??1" in fn, (
        "_smgmtLanesHtml does not default slot capacity to 1 for serial mode"
    )


# ────────────── AC8: Chip click scrolls to All Issues card ───────────────────


def test_ac8_chip_click_handler_exists():
    """A click handler function exists for lane chips (_smgmtLaneChipClick or inline)."""
    has_click_fn = "_smgmtLaneChipClick" in PROJECT_HTML
    has_inline = False
    if "_smgmtLaneChipHtml" in PROJECT_HTML:
        fn = _fn_body("_smgmtLaneChipHtml")
        has_inline = "onclick" in fn or "click" in fn.lower()
    else:
        fn = _fn_body("_smgmtLanesHtml")
        has_inline = "onclick" in fn or "_smgmtLaneChipClick" in fn
    assert has_click_fn or has_inline, (
        "No click handler found for lane chips — clicking a chip must scroll to the issue card"
    )


def test_ac8_click_scrolls_into_view():
    """Click handler calls scrollIntoView or a scroll-to helper."""
    has_fn = "_smgmtLaneChipClick" in PROJECT_HTML
    if has_fn:
        fn = _fn_body("_smgmtLaneChipClick")
        has_scroll = "scrollIntoView" in fn or "scroll" in fn.lower()
        assert has_scroll, (
            "_smgmtLaneChipClick does not call scrollIntoView — chip click must scroll to issue"
        )
    else:
        # Accept inline onclick that references a scroll call
        fn = _fn_body("_smgmtLanesHtml")
        has_scroll = "scrollIntoView" in fn or "scroll" in fn.lower() or "_smgmtLaneChipClick" in fn
        assert has_scroll, (
            "Chip click does not scroll to issue card in All Issues"
        )


def test_ac8_click_targets_smgmt_ticket_by_issue_number():
    """Click handler finds .smgmt-ticket[data-issue] to scroll to the correct card."""
    has_fn = "_smgmtLaneChipClick" in PROJECT_HTML
    if has_fn:
        fn = _fn_body("_smgmtLaneChipClick")
        has_target = "smgmt-ticket" in fn or "data-issue" in fn
        assert has_target, (
            "_smgmtLaneChipClick does not target smgmt-ticket[data-issue] — must scroll to correct card"
        )
    else:
        # Check the lanes html or inline usage
        fn = _fn_body("_smgmtLanesHtml")
        has_target = "smgmt-ticket" in fn or "data-issue" in fn
        # This AC is satisfied if either the dedicated function or inline references the selector
        assert "_smgmtLaneChipClick" in PROJECT_HTML or has_target, (
            "Chip click does not target smgmt-ticket[data-issue] for scroll"
        )


def test_ac8_click_highlights_card():
    """Click handler applies a highlight (is-selected or similar) to the issue card."""
    has_fn = "_smgmtLaneChipClick" in PROJECT_HTML
    if has_fn:
        fn = _fn_body("_smgmtLaneChipClick")
        has_highlight = (
            "is-selected" in fn
            or "highlight" in fn.lower()
            or "smgmt-kb-focused" in fn
            or "classList" in fn
        )
        assert has_highlight, (
            "_smgmtLaneChipClick does not highlight the target card"
        )


# ──────────────── AC9: No title or log output inside lane ────────────────────


def test_ac9_no_node_agent_badge_in_lane_chips():
    """Lane chip rendering does not include node-agent badge."""
    if "_smgmtLaneChipHtml" in PROJECT_HTML:
        fn = _fn_body("_smgmtLaneChipHtml")
    else:
        fn = _fn_body("_smgmtLanesHtml")
    assert "node-agent" not in fn, (
        "Chip renderer includes node-agent badge — no agent badge should appear in lane chips"
    )


def test_ac9_no_elapsed_time_in_lane_chips():
    """Lane chip rendering does not include elapsed time."""
    if "_smgmtLaneChipHtml" in PROJECT_HTML:
        fn = _fn_body("_smgmtLaneChipHtml")
        has_elapsed = "elapsed" in fn or "node-time" in fn
        assert not has_elapsed, (
            "_smgmtLaneChipHtml includes elapsed time — no time should appear in lane chips"
        )


def test_ac9_lane_chip_class_used_not_node_class():
    """Lane chips use .lane-chip class, not .node card class."""
    if "_smgmtLaneChipHtml" in PROJECT_HTML:
        fn = _fn_body("_smgmtLaneChipHtml")
    else:
        fn = _fn_body("_smgmtLanesHtml")
    # chip HTML must use lane-chip, not the full .node card primitive
    has_chip = "lane-chip" in fn
    assert has_chip, (
        "Lane rendering does not use .lane-chip class — chips must replace full node cards"
    )
    # No full node card should appear (node-title, node-agent, etc.)
    assert "class=\"node " not in fn and "class='node " not in fn, (
        "Lane rendering still uses .node card class — chips should replace node cards"
    )
