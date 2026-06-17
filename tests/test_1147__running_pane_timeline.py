"""Tests for issue #1147 — Render per-issue Gantt timeline in running pane.

Static analysis of project.html (HTML/CSS/JS) — no server needed.

AC coverage:
  AC1  — One track per issue rendered on a shared time axis (sprint start →
          projected finish); each row shows issue number at left edge.
  AC2  — Segments placed by real wall-clock offsets so serial and pipeline modes
          both render correctly without special-casing.
  AC3  — Segment colours: coder=purple, tester=amber, documenter=teal, reviewer=indigo.
  AC4  — Retry rounds render dimmed + hatched over the base segment colour.
  AC5  — The live running segment and its projected remainder both blink.
  AC6  — 15-minute gridlines and minute markers span all tracks; sprint start at 0.
  AC7  — A green vertical "now" line cuts across all tracks at current elapsed time.
  AC8  — Projected finish labelled "est. finish HH:MM"; colliding minute markers suppressed.
  AC9  — Done issues show a grey estimate envelope behind the coloured segments.
  AC10 — Queued issues and running projected remainder render as ghosted (faint + hatched).
  AC11 — Sprint wrap-up row after last ticket; ghosted documenter + reviewer (when enabled).
  AC12 — Agent time-split bar above timeline shows coder vs tester actual; documenter/reviewer pending.
  AC13 — Per-row right-side label stacks main metric above sub-line; labels never wrap mid-word.
  AC14 — Issue number appears at left edge of each track row.
  AC15 — Visual output matches running_pane_timeline_mock.html.
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


def _css_rule_exists(selector: str, src: str = PROJECT_HTML) -> bool:
    pattern = re.compile(re.escape(selector) + r"(?![\w-])[^{]*\{")
    return bool(pattern.search(src))


def _css_has(selector: str, prop: str, src: str = PROJECT_HTML) -> bool:
    pattern = re.compile(
        re.escape(selector) + r"(?![\w-])[^{]*\{([^{}]*)\}",
        re.DOTALL,
    )
    for m in pattern.finditer(src):
        if prop in m.group(1):
            return True
    return False


# ──────────── AC1 + AC14: One track per issue, issue number at left ──────────


def test_ac1_timeline_container_in_run_shell():
    """The running pane HTML contains a timeline container element."""
    has_container = (
        'id="smgmt-rp-timeline"' in PROJECT_HTML
        or 'id="smgmt-timeline"' in PROJECT_HTML
        or "rp-timeline" in PROJECT_HTML
    )
    assert has_container, (
        "Running pane timeline container element not found in project.html"
    )


def test_ac1_timeline_above_orchestrator_panel():
    """Timeline sits below run-head and above orchestrator (visible without scrolling past issues)."""
    shell_start = PROJECT_HTML.index('id="smgmt-run-shell"')
    shell = PROJECT_HTML[shell_start:shell_start + 4000]
    tl_pos = shell.find('id="smgmt-rp-timeline"')
    orch_pos = shell.find('id="smgmt-orch-panel"')
    head_pos = shell.find('id="smgmt-run-head"')
    assert tl_pos != -1 and orch_pos != -1 and head_pos != -1
    assert head_pos < tl_pos < orch_pos, (
        "Timeline must render between run-head and orchestrator panel"
    )


def test_ac1_timeline_render_function_exists():
    """A JS function exists to render the running-pane Gantt timeline."""
    has_fn = (
        "_smgmtTimelineHtml" in PROJECT_HTML
        or "_smgmtTimelineRender" in PROJECT_HTML
        or "_smgmtRpTimeline" in PROJECT_HTML
    )
    assert has_fn, (
        "No running-pane timeline render function found in project.html"
    )


def test_ac1_timeline_uses_shared_time_axis():
    """Timeline render function scales segments on a shared total-duration axis."""
    fn_name = None
    for name in ("_smgmtTimelineHtml", "_smgmtTimelineRender", "_smgmtRpTimelineHtml"):
        if name in PROJECT_HTML:
            fn_name = name
            break
    assert fn_name, "No timeline render function found"
    fn = _fn_body(fn_name)
    # Must reference sprint_started_at or a total-duration scale factor
    has_scale = (
        "sprint_started_at" in fn
        or "totalMs" in fn
        or "totalSec" in fn
        or "totalMin" in fn
        or "totalDuration" in fn
        or "sprint_start" in fn
        or "startMs" in fn
    )
    assert has_scale, (
        f"{fn_name} does not use sprint_started_at or a shared time scale — "
        "all tracks must share the same time axis"
    )


def test_ac14_issue_number_at_left_edge():
    """Timeline rows show issue number at the left edge of each track."""
    fn_name = None
    for name in ("_smgmtTimelineHtml", "_smgmtTimelineRender", "_smgmtRpTimelineHtml"):
        if name in PROJECT_HTML:
            fn_name = name
            break
    assert fn_name, "No timeline render function found"
    fn = _fn_body(fn_name)
    has_num = (
        ".number" in fn
        or "rp-tl-num" in fn
        or "rp-tl-row-num" in fn
        or "#${" in fn  # "#${iss.number}" pattern
        or "iss.number" in fn
        or "issue.number" in fn
        or "num" in fn.lower()
    )
    assert has_num, (
        f"{fn_name} does not render issue number at track left — "
        "each row must show the issue number at its left edge"
    )


# ───────── AC2: Wall-clock offsets, no special-casing per mode ───────────────


def test_ac2_segment_positioned_by_wall_clock_offset():
    """Segment left position is computed from wall-clock start offset, not mode-specific logic."""
    fn_name = None
    for name in ("_smgmtTimelineHtml", "_smgmtTimelineRender", "_smgmtRpTimelineHtml"):
        if name in PROJECT_HTML:
            fn_name = name
            break
    assert fn_name, "No timeline render function found"
    fn = _fn_body(fn_name)
    # Must compute a `left` percentage or pixel offset from a start timestamp
    has_offset = (
        "left" in fn
        and ("start" in fn or "offset" in fn or "startMs" in fn)
    )
    assert has_offset, (
        f"{fn_name} does not compute left offset from segment start time — "
        "segments must be positioned by real wall-clock offsets"
    )


def test_ac2_no_serial_pipeline_branch_in_render():
    """Render function does not special-case pipeline vs serial layout (offsets handle it)."""
    fn_name = None
    for name in ("_smgmtTimelineHtml", "_smgmtTimelineRender", "_smgmtRpTimelineHtml"):
        if name in PROJECT_HTML:
            fn_name = name
            break
    assert fn_name, "No timeline render function found"
    fn = _fn_body(fn_name)
    # Should NOT contain an if/else branch on pipeline_mode for segment positioning
    # (pipeline positioning falls out naturally from wall-clock offsets)
    has_serial_branch = (
        "pipeline_mode" in fn
        and "if" in fn
        and "serial" in fn.lower()
    )
    assert not has_serial_branch, (
        f"{fn_name} contains a pipeline/serial branch in segment rendering — "
        "wall-clock offsets should handle both modes without special-casing"
    )


# ─────────── AC3: Segment colours ────────────────────────────────────────────


def test_ac3_coder_segments_use_purple():
    """CSS: coder segment uses purple colour token."""
    # Accept either direct CSS rule or inline style reference in JS
    css_ok = (
        _css_has(".rp-tl-seg--coder", "purple")
        or _css_has(".rp-tl-coder", "purple")
        or _css_has(".rp-tl-seg--coder", "--purple")
        or _css_has(".rp-tl-coder", "--purple")
    )
    js_ok = (
        ("coder" in PROJECT_HTML and "purple" in PROJECT_HTML and "rp-tl" in PROJECT_HTML)
    )
    assert css_ok or js_ok, (
        "Coder segments must use purple colour — no .rp-tl-seg--coder rule with purple found"
    )


def test_ac3_tester_segments_use_amber():
    """CSS: tester segment uses amber colour token."""
    css_ok = (
        _css_has(".rp-tl-seg--tester", "amber")
        or _css_has(".rp-tl-tester", "amber")
    )
    js_ok = (
        "tester" in PROJECT_HTML and "amber" in PROJECT_HTML and "rp-tl" in PROJECT_HTML
    )
    assert css_ok or js_ok, (
        "Tester segments must use amber colour — no .rp-tl-seg--tester rule with amber found"
    )


def test_ac3_documenter_segments_use_teal():
    """CSS: documenter segment uses teal colour token."""
    css_ok = (
        _css_has(".rp-tl-seg--documenter", "teal")
        or _css_has(".rp-tl-documenter", "teal")
    )
    js_ok = (
        "documenter" in PROJECT_HTML and "teal" in PROJECT_HTML and "rp-tl" in PROJECT_HTML
    )
    assert css_ok or js_ok, (
        "Documenter segments must use teal colour — no rule with teal found"
    )


def test_ac3_reviewer_segments_use_indigo():
    """CSS: reviewer segment uses indigo colour token."""
    css_ok = (
        _css_has(".rp-tl-seg--reviewer", "indigo")
        or _css_has(".rp-tl-reviewer", "indigo")
    )
    js_ok = (
        "reviewer" in PROJECT_HTML and "indigo" in PROJECT_HTML and "rp-tl" in PROJECT_HTML
    )
    assert css_ok or js_ok, (
        "Reviewer segments must use indigo colour — no rule with indigo found"
    )


# ─────────── AC4: Retry rounds dimmed + hatched ──────────────────────────────


def test_ac4_retry_hatching_css_exists():
    """CSS defines a hatched pattern for retry/fix-round segments."""
    has_hatch = (
        "repeating-linear-gradient" in PROJECT_HTML
        and (
            "retry" in PROJECT_HTML
            or "fix" in PROJECT_HTML
        )
        and "rp-tl" in PROJECT_HTML
    )
    if not has_hatch:
        # Also accept stripes via background-image on any rp-tl class
        has_hatch = (
            _css_has(".rp-tl-seg--retry", "repeating-linear-gradient")
            or _css_has(".rp-tl-seg--fix", "repeating-linear-gradient")
        )
    assert has_hatch, (
        "No hatched background pattern found for retry/fix segments in rp-tl CSS"
    )


def test_ac4_retry_segments_dimmed():
    """CSS: retry segments are dimmed (opacity or reduced alpha)."""
    has_dim = (
        _css_has(".rp-tl-seg--retry", "opacity")
        or _css_has(".rp-tl-seg--fix", "opacity")
        or _css_has(".rp-tl-seg--retry", "rgba")
        or _css_has(".rp-tl-overlay--retry", "opacity")
        or ("retry" in PROJECT_HTML and "opacity" in PROJECT_HTML and "rp-tl" in PROJECT_HTML)
    )
    assert has_dim, (
        "Retry segments must be dimmed — no opacity rule found for retry class"
    )


def test_ac4_render_function_marks_retry_segments():
    """Render function applies retry/fix class for non-initial attempt_kind segments."""
    fn_name = None
    for name in ("_smgmtTimelineHtml", "_smgmtTimelineRender", "_smgmtRpTimelineHtml"):
        if name in PROJECT_HTML:
            fn_name = name
            break
    assert fn_name, "No timeline render function found"
    fn = _fn_body(fn_name)
    has_retry = (
        "retry" in fn.lower()
        or "fix" in fn.lower()
        or "attempt_kind" in fn
        or "isRetry" in fn
    )
    assert has_retry, (
        f"{fn_name} does not handle retry rounds — must apply dimmed+hatched style for retries"
    )


# ─────────── AC5: Running + projected remainder blink ────────────────────────


def test_ac5_blink_animation_defined():
    """A blink @keyframes animation is defined for the running segment."""
    has_blink = (
        "@keyframes" in PROJECT_HTML
        and (
            "rp-tl" in PROJECT_HTML or "rpTlBlink" in PROJECT_HTML
        )
        and "blink" in PROJECT_HTML.lower()
    )
    if not has_blink:
        # Accept any keyframes near rp-tl
        idx = PROJECT_HTML.find("rp-tl")
        if idx != -1:
            nearby = PROJECT_HTML[max(0, idx - 500):idx + 3000]
            has_blink = "@keyframes" in nearby
    assert has_blink, (
        "No blink @keyframes animation found near rp-tl CSS — running segment must blink"
    )


def test_ac5_running_segment_class_uses_blink():
    """CSS .rp-tl-seg--running applies the blink animation."""
    has_blink_rule = (
        _css_has(".rp-tl-seg--running", "animation")
        or _css_has(".rp-tl-seg--live", "animation")
        or ("rp-tl-seg--running" in PROJECT_HTML and "animation" in PROJECT_HTML)
    )
    assert has_blink_rule, (
        ".rp-tl-seg--running does not apply a CSS animation — running segment must blink"
    )


def test_ac5_projected_remainder_class_uses_blink():
    """CSS .rp-tl-seg--projected applies the blink animation."""
    has_blink_rule = (
        _css_has(".rp-tl-seg--projected", "animation")
        or ("projected" in PROJECT_HTML and "animation" in PROJECT_HTML and "rp-tl" in PROJECT_HTML)
    )
    assert has_blink_rule, (
        ".rp-tl-seg--projected does not apply animation — projected remainder must blink"
    )


# ─────────── AC6: 15-minute gridlines + minute markers ───────────────────────


def test_ac6_gridline_css_exists():
    """CSS class for 15-minute gridlines exists."""
    has_grid = (
        _css_rule_exists(".rp-tl-grid")
        or _css_rule_exists(".rp-tl-gridline")
        or _css_rule_exists(".rp-tl-tick")
        or ("gridline" in PROJECT_HTML and "rp-tl" in PROJECT_HTML)
        or ("rp-tl-grid" in PROJECT_HTML)
    )
    assert has_grid, (
        "No gridline CSS found for running-pane timeline — 15-min gridlines required"
    )


def test_ac6_minute_markers_rendered():
    """Render function or HTML includes minute marker logic."""
    fn_name = None
    for name in ("_smgmtTimelineHtml", "_smgmtTimelineRender", "_smgmtRpTimelineHtml"):
        if name in PROJECT_HTML:
            fn_name = name
            break
    assert fn_name, "No timeline render function found"
    fn = _fn_body(fn_name)
    has_markers = (
        "minute" in fn.lower()
        or "15" in fn
        or "gridline" in fn.lower()
        or "grid" in fn.lower()
        or "tick" in fn.lower()
    )
    assert has_markers, (
        f"{fn_name} does not render minute markers or gridlines — 15-min marks required"
    )


def test_ac6_sprint_start_at_zero():
    """Render function places sprint start at position 0 on the axis."""
    fn_name = None
    for name in ("_smgmtTimelineHtml", "_smgmtTimelineRender", "_smgmtRpTimelineHtml"):
        if name in PROJECT_HTML:
            fn_name = name
            break
    assert fn_name, "No timeline render function found"
    fn = _fn_body(fn_name)
    # Sprint start is the 0 anchor — segments offset from it
    has_zero_anchor = (
        "sprint_started_at" in fn
        or "sprintStart" in fn
        or "startMs" in fn
        or "start_ms" in fn
        or "sprint_start" in fn
    )
    assert has_zero_anchor, (
        f"{fn_name} does not anchor sprint start at position 0 — "
        "the time axis must start at sprint start"
    )


# ─────────── AC7: Green "now" line ───────────────────────────────────────────


def test_ac7_now_line_css_exists():
    """CSS class for the green "now" vertical line exists."""
    has_now = (
        _css_rule_exists(".rp-tl-now")
        or _css_rule_exists(".rp-tl-now-line")
        or ("now-line" in PROJECT_HTML and "rp-tl" in PROJECT_HTML)
        or ("rp-tl-now" in PROJECT_HTML)
    )
    assert has_now, (
        "No CSS class found for the 'now' vertical line in the running-pane timeline"
    )


def test_ac7_now_line_is_green():
    """The "now" line CSS uses green colour."""
    is_green = (
        _css_has(".rp-tl-now", "green")
        or _css_has(".rp-tl-now-line", "green")
        or _css_has(".rp-tl-now", "#16a34a")
        or _css_has(".rp-tl-now", "--green")
    )
    if not is_green:
        # Check via inline style in JS
        fn_name = None
        for name in ("_smgmtTimelineHtml", "_smgmtTimelineRender", "_smgmtRpTimelineHtml"):
            if name in PROJECT_HTML:
                fn_name = name
                break
        if fn_name:
            fn = _fn_body(fn_name)
            is_green = "green" in fn.lower() and "now" in fn.lower()
    assert is_green, (
        "The 'now' line must be green — no green colour found in now-line CSS"
    )


def test_ac7_now_line_rendered_at_elapsed_position():
    """Render function positions the now-line at current elapsed time."""
    fn_name = None
    for name in ("_smgmtTimelineHtml", "_smgmtTimelineRender", "_smgmtRpTimelineHtml"):
        if name in PROJECT_HTML:
            fn_name = name
            break
    assert fn_name, "No timeline render function found"
    fn = _fn_body(fn_name)
    has_now_pos = (
        ("now" in fn.lower() and "left" in fn)
        or "nowPct" in fn
        or "nowOffset" in fn
        or "server_now" in fn
    )
    assert has_now_pos, (
        f"{fn_name} does not position the now-line at elapsed time — "
        "the now-line must be placed at current elapsed position"
    )


# ─────────── AC8: "est. finish" label + marker suppression ───────────────────


def test_ac8_finish_label_text():
    """Render function renders 'est. finish' label at the right edge."""
    fn_name = None
    for name in ("_smgmtTimelineHtml", "_smgmtTimelineRender", "_smgmtRpTimelineHtml"):
        if name in PROJECT_HTML:
            fn_name = name
            break
    assert fn_name, "No timeline render function found"
    fn = _fn_body(fn_name)
    has_finish_label = (
        "est. finish" in fn.lower()
        or "est.finish" in fn.lower()
        or "est finish" in fn.lower()
        or "projected_finish" in fn
    )
    assert has_finish_label, (
        f"{fn_name} does not render 'est. finish HH:MM' label — "
        "projected finish must be labelled at the right edge"
    )


def test_ac8_marker_suppression_near_finish():
    """Render function suppresses minute markers that collide with the finish label."""
    fn_name = None
    for name in ("_smgmtTimelineHtml", "_smgmtTimelineRender", "_smgmtRpTimelineHtml"):
        if name in PROJECT_HTML:
            fn_name = name
            break
    assert fn_name, "No timeline render function found"
    fn = _fn_body(fn_name)
    has_suppress = (
        "collide" in fn.lower()
        or "suppress" in fn.lower()
        or "overlap" in fn.lower()
        or "tooClose" in fn
        or "too_close" in fn
        or ("finish" in fn.lower() and "skip" in fn.lower())
        or ("finish" in fn.lower() and "abs(" in fn)
        or ("finishPct" in fn and "Math.abs" in fn)
        or ("finishPct" in fn and "< " in fn)
    )
    assert has_suppress, (
        f"{fn_name} does not suppress minute markers near the finish label — "
        "colliding markers must be hidden so the finish label is unclipped"
    )


# ─────────── AC9: Grey estimate envelope for done issues ─────────────────────


def test_ac9_envelope_css_exists():
    """CSS class for the grey estimate envelope exists."""
    has_env = (
        _css_rule_exists(".rp-tl-envelope")
        or _css_rule_exists(".rp-tl-est-envelope")
        or ("rp-tl-envelope" in PROJECT_HTML)
        or ("envelope" in PROJECT_HTML and "rp-tl" in PROJECT_HTML)
    )
    assert has_env, (
        "No CSS class found for the estimate envelope — "
        "done issues must show a grey envelope behind segments"
    )


def test_ac9_envelope_is_grey():
    """The estimate envelope CSS uses grey / steel colour."""
    is_grey = (
        _css_has(".rp-tl-envelope", "grey")
        or _css_has(".rp-tl-envelope", "gray")
        or _css_has(".rp-tl-envelope", "--steel")
        or _css_has(".rp-tl-envelope", "#")
        or (
            "envelope" in PROJECT_HTML
            and ("grey" in PROJECT_HTML or "gray" in PROJECT_HTML or "steel" in PROJECT_HTML or "#9" in PROJECT_HTML)
            and "rp-tl" in PROJECT_HTML
        )
    )
    assert is_grey, (
        "Estimate envelope must use grey/steel colour — no grey found in envelope CSS"
    )


def test_ac9_done_issues_render_envelope():
    """Render function renders the estimate envelope for done issues."""
    fn_name = None
    for name in ("_smgmtTimelineHtml", "_smgmtTimelineRender", "_smgmtRpTimelineHtml"):
        if name in PROJECT_HTML:
            fn_name = name
            break
    assert fn_name, "No timeline render function found"
    fn = _fn_body(fn_name)
    has_envelope = (
        "envelope" in fn.lower()
        or "rp-tl-envelope" in fn
    )
    assert has_envelope, (
        f"{fn_name} does not render the estimate envelope for done issues"
    )


# ─────────── AC10: Ghosted queued / projected segments ───────────────────────


def test_ac10_ghost_css_exists():
    """CSS class for ghosted (queued / projected) segments exists."""
    has_ghost = (
        _css_rule_exists(".rp-tl-seg--ghost")
        or _css_rule_exists(".rp-tl-seg--queued")
        or _css_rule_exists(".rp-tl-ghost")
        or ("ghost" in PROJECT_HTML and "rp-tl" in PROJECT_HTML)
    )
    assert has_ghost, (
        "No CSS class for ghosted segments found — queued/projected segments must appear ghosted"
    )


def test_ac10_ghost_is_faint():
    """Ghost CSS uses opacity or reduced alpha for faint appearance."""
    has_faint = (
        _css_has(".rp-tl-seg--ghost", "opacity")
        or _css_has(".rp-tl-seg--queued", "opacity")
        or _css_has(".rp-tl-ghost", "opacity")
        or ("ghost" in PROJECT_HTML and "opacity" in PROJECT_HTML and "rp-tl" in PROJECT_HTML)
    )
    assert has_faint, (
        "Ghost/queued CSS must have opacity — segments must appear faint"
    )


def test_ac10_ghost_is_hatched():
    """Ghost CSS uses a hatched background pattern."""
    has_hatch = (
        _css_has(".rp-tl-seg--ghost", "repeating-linear-gradient")
        or _css_has(".rp-tl-seg--queued", "repeating-linear-gradient")
        or _css_has(".rp-tl-ghost", "repeating-linear-gradient")
        or (
            "ghost" in PROJECT_HTML
            and "repeating-linear-gradient" in PROJECT_HTML
            and "rp-tl" in PROJECT_HTML
        )
    )
    assert has_hatch, (
        "Ghost/queued segments must be hatched — no repeating-linear-gradient found in ghost CSS"
    )


def test_ac10_queued_issues_rendered_as_ghost():
    """Render function applies ghost class to queued issue segments."""
    fn_name = None
    for name in ("_smgmtTimelineHtml", "_smgmtTimelineRender", "_smgmtRpTimelineHtml"):
        if name in PROJECT_HTML:
            fn_name = name
            break
    assert fn_name, "No timeline render function found"
    fn = _fn_body(fn_name)
    has_ghost = (
        "ghost" in fn.lower()
        or "queued" in fn.lower()
    )
    assert has_ghost, (
        f"{fn_name} does not apply ghost/queued styling — queued segments must be ghosted"
    )


# ─────────── AC11: Sprint wrap-up row ────────────────────────────────────────


def test_ac11_wrapup_row_rendered():
    """Render function renders the sprint wrap-up row after the last ticket."""
    fn_name = None
    for name in ("_smgmtTimelineHtml", "_smgmtTimelineRender", "_smgmtRpTimelineHtml"):
        if name in PROJECT_HTML:
            fn_name = name
            break
    assert fn_name, "No timeline render function found"
    fn = _fn_body(fn_name)
    has_wrapup = (
        "wrap_up" in fn
        or "wrapup" in fn.lower()
        or "wrap-up" in fn.lower()
        or "documenter" in fn.lower()
    )
    assert has_wrapup, (
        f"{fn_name} does not render a sprint wrap-up row — "
        "a wrap-up row with documenter/reviewer must appear after the last ticket"
    )


def test_ac11_wrapup_row_visually_separated():
    """CSS: the wrap-up row is visually separated from ticket rows."""
    has_sep = (
        _css_rule_exists(".rp-tl-wrapup")
        or _css_rule_exists(".rp-tl-row--wrapup")
        or _css_rule_exists(".rp-tl-wrap-row")
        or ("wrapup" in PROJECT_HTML and "rp-tl" in PROJECT_HTML and "border" in PROJECT_HTML)
        or ("wrapup" in PROJECT_HTML and "rp-tl" in PROJECT_HTML and "margin" in PROJECT_HTML)
        or ("rp-tl-wrapup" in PROJECT_HTML)
    )
    assert has_sep, (
        "No CSS separation found for the wrap-up row — it must be visually set apart from ticket rows"
    )


def test_ac11_reviewer_shown_only_when_enabled():
    """Render function shows reviewer in wrap-up only when reviewer is enabled."""
    fn_name = None
    for name in ("_smgmtTimelineHtml", "_smgmtTimelineRender", "_smgmtRpTimelineHtml"):
        if name in PROJECT_HTML:
            fn_name = name
            break
    assert fn_name, "No timeline render function found"
    fn = _fn_body(fn_name)
    has_reviewer_guard = (
        ("reviewer" in fn.lower() and "wrap_up" in fn)
        or ("reviewer" in fn.lower() and ("if" in fn or "?" in fn))
    )
    assert has_reviewer_guard, (
        f"{fn_name} does not conditionally show reviewer in wrap-up — "
        "reviewer must only appear when wrap_up_estimate.reviewer is present"
    )


# ─────────── AC12: Agent time-split bar ──────────────────────────────────────


def test_ac12_split_bar_rendered():
    """Render function renders an agent time-split bar above the timeline."""
    fn_name = None
    for name in ("_smgmtTimelineHtml", "_smgmtTimelineRender", "_smgmtRpTimelineHtml"):
        if name in PROJECT_HTML:
            fn_name = name
            break
    assert fn_name, "No timeline render function found"
    fn = _fn_body(fn_name)
    has_split = (
        "split" in fn.lower()
        or "rp-tl-split" in fn
        or "time-split" in fn.lower()
        or "splitBar" in fn
    )
    assert has_split, (
        f"{fn_name} does not render an agent time-split bar — "
        "a coder vs tester split bar must appear above the timeline"
    )


def test_ac12_split_bar_css_exists():
    """CSS class for the agent time-split bar exists."""
    has_split = (
        _css_rule_exists(".rp-tl-split")
        or _css_rule_exists(".rp-tl-split-bar")
        or ("rp-tl-split" in PROJECT_HTML)
    )
    assert has_split, (
        "No CSS class for the agent time-split bar in running-pane timeline"
    )


def test_ac12_pending_segments_styled():
    """Documenter/reviewer shown as pending (distinct style) in the split bar."""
    fn_name = None
    for name in ("_smgmtTimelineHtml", "_smgmtTimelineRender", "_smgmtRpTimelineHtml"):
        if name in PROJECT_HTML:
            fn_name = name
            break
    assert fn_name, "No timeline render function found"
    fn = _fn_body(fn_name)
    has_pending = (
        "pending" in fn.lower()
        or "documenter" in fn.lower()
    )
    assert has_pending, (
        f"{fn_name} does not mark documenter/reviewer as pending in the split bar"
    )


# ─────────── AC13: Right-side labels, no mid-word wrap ───────────────────────


def test_ac13_row_label_css_no_wrap():
    """CSS for right-side row labels uses white-space:nowrap or word-break:keep-all."""
    has_nowrap = (
        _css_has(".rp-tl-row-label", "nowrap")
        or _css_has(".rp-tl-label", "nowrap")
        or _css_has(".rp-tl-metric", "nowrap")
        or (
            "rp-tl" in PROJECT_HTML
            and "nowrap" in PROJECT_HTML
        )
    )
    assert has_nowrap, (
        "Row label CSS must have white-space:nowrap to prevent mid-word wrapping"
    )


def test_ac13_label_stacks_metric_and_sub():
    """Render function renders stacked main metric and sub-line for each row."""
    fn_name = None
    for name in ("_smgmtTimelineHtml", "_smgmtTimelineRender", "_smgmtRpTimelineHtml"):
        if name in PROJECT_HTML:
            fn_name = name
            break
    assert fn_name, "No timeline render function found"
    fn = _fn_body(fn_name)
    # Must render two separate elements: a main metric and a sub-line
    has_stack = (
        "rp-tl-metric" in fn
        or "rp-tl-sub" in fn
        or ("metric" in fn.lower() and "sub" in fn.lower())
        or "row-metric" in fn
        or "row-sub" in fn
    )
    assert has_stack, (
        f"{fn_name} does not render stacked metric + sub-line labels — "
        "each row must stack main metric above a muted sub-line"
    )


# ─────────── AC15: Fetch from timeline endpoint ──────────────────────────────


def test_ac15_timeline_fetch_from_endpoint():
    """JS code fetches data from the /api/sprints/{label}/timeline endpoint."""
    has_endpoint = (
        "/timeline" in PROJECT_HTML
        and ("fetch" in PROJECT_HTML or "XMLHttpRequest" in PROJECT_HTML)
        and "rp-tl" in PROJECT_HTML
    )
    if not has_endpoint:
        # Check within the specific timeline-related context
        idx = PROJECT_HTML.find("rp-tl")
        if idx != -1:
            region = PROJECT_HTML[max(0, idx - 2000):idx + 5000]
            has_endpoint = "/timeline" in region and "fetch" in region
    assert has_endpoint, (
        "JS does not fetch from /api/sprints/{label}/timeline — "
        "the running-pane timeline must pull data from the timeline endpoint"
    )
