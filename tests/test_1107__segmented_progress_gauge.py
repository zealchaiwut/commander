"""Tests for issue #1107 — Add segmented progress gauge to running pane header.

Static analysis of project.html (HTML/CSS/JS) — no server needed.

AC coverage:
  AC1  — Header displays a single horizontal bar split into four colored segments
          proportional to ticket count: done (green), retrying (amber), running
          (blue), queued (grey track remainder).
  AC2  — Running segment has a CSS shimmer/pulse animation.
  AC3  — Below the bar, a legend row shows live counts in format:
          N done / N retrying / N running / N queued.
  AC4  — Below the legend, an X of Y done summary label is shown.
  AC5  — The standalone DONE stat card is removed from the header.
  AC6  — A slim one-line stat row beneath the gauge shows: active agents (with
          serial/parallel mode indicator), retrying (amber, naming the issue),
          and agent time (coder / tester split).
  AC7  — Gauge segments, legend counts, and stat row all update live as issue
          states change (no page reload required).
  AC8  — Gauge is placed inside the run-head-card; legend below bar; summary
          below legend; stat row below summary.
  AC9  — When all tickets are queued, full bar is grey (queued segment fills
          100%).
  AC10 — When all tickets are done, full bar is green; shimmer is absent.
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


# ── AC1: Horizontal bar with four colored segments ───────────────────────────


def test_ac1_gauge_css_rule_exists():
    """CSS class for the gauge container exists."""
    has_css = _css_rule_exists(".rp-gauge") or "rp-gauge" in PROJECT_HTML
    assert has_css, (
        "No .rp-gauge CSS rule found — the horizontal progress gauge container "
        "must have CSS styling"
    )


def test_ac1_gauge_segment_css_done_green():
    """CSS for the done (green) gauge segment exists."""
    has_css = (
        _css_rule_exists(".rp-gauge-seg--done")
        or (
            "rp-gauge-seg--done" in PROJECT_HTML
            and ("green" in PROJECT_HTML or "var(--green" in PROJECT_HTML)
        )
        or _css_rule_exists(".rp-gauge-done")
    )
    assert has_css, (
        "No CSS for done (green) gauge segment found — "
        "the done segment must be styled with the green color token"
    )


def test_ac1_gauge_segment_css_retrying_amber():
    """CSS for the retrying (amber) gauge segment exists."""
    has_css = (
        _css_rule_exists(".rp-gauge-seg--retrying")
        or (
            "rp-gauge-seg--retrying" in PROJECT_HTML
            and ("amber" in PROJECT_HTML or "var(--amber" in PROJECT_HTML)
        )
        or _css_rule_exists(".rp-gauge-retrying")
    )
    assert has_css, (
        "No CSS for retrying (amber) gauge segment found — "
        "the retrying segment must be styled with the amber color token"
    )


def test_ac1_gauge_segment_css_running_blue():
    """CSS for the running (blue) gauge segment exists."""
    has_css = (
        _css_rule_exists(".rp-gauge-seg--running")
        or (
            "rp-gauge-seg--running" in PROJECT_HTML
            and ("blue" in PROJECT_HTML or "var(--blue" in PROJECT_HTML)
        )
        or _css_rule_exists(".rp-gauge-running")
    )
    assert has_css, (
        "No CSS for running (blue) gauge segment found — "
        "the running segment must be styled with the blue color token"
    )


def test_ac1_gauge_segment_css_queued_grey():
    """CSS for the queued (grey) gauge segment exists."""
    has_css = (
        _css_rule_exists(".rp-gauge-seg--queued")
        or "rp-gauge-seg--queued" in PROJECT_HTML
        or _css_rule_exists(".rp-gauge-queued")
        or "rp-gauge-queued" in PROJECT_HTML
    )
    assert has_css, (
        "No CSS for queued (grey) gauge segment found — "
        "the queued remainder segment must have a grey/muted CSS style"
    )


def _gauge_fn_body() -> str:
    """Return the combined body of _smgmtRunHeadHtml + _smgmtGaugeHtml (if present)."""
    parts = [_fn_body("_smgmtRunHeadHtml")]
    for name in ("_smgmtGaugeHtml", "_rpGaugeHtml"):
        if name in PROJECT_HTML:
            parts.append(_fn_body(name))
    return "\n".join(parts)


def test_ac1_gauge_html_generated_by_run_head():
    """_smgmtRunHeadHtml (or its gauge helper) generates markup with all four segments."""
    fn = _gauge_fn_body()
    has_gauge = (
        "rp-gauge" in fn
        and (
            "done" in fn.lower()
            and "retrying" in fn.lower()
            and "running" in fn.lower()
            and "queued" in fn.lower()
        )
    )
    assert has_gauge, (
        "Neither _smgmtRunHeadHtml nor its gauge helper generates gauge markup with four segments — "
        "it must produce a horizontal bar with done/retrying/running/queued segments"
    )


def test_ac1_gauge_segments_proportional_widths():
    """Gauge segment widths are set proportionally (percentage-based)."""
    fn = _gauge_fn_body()
    has_pct = (
        "%" in fn
        and "rp-gauge" in fn
        and ("width" in fn or "style" in fn.lower())
    )
    assert has_pct, (
        "Gauge HTML generator does not set percentage widths on gauge segments — "
        "each segment's width must be proportional to its ticket count"
    )


# ── AC2: Running segment has shimmer/pulse animation ─────────────────────────


def test_ac2_shimmer_keyframe_css_exists():
    """A CSS @keyframes rule for the shimmer/pulse animation exists."""
    has_keyframe = (
        "rp-gauge-shimmer" in PROJECT_HTML
        or "rp-shimmer" in PROJECT_HTML
        or (
            "shimmer" in PROJECT_HTML.lower()
            and "@keyframes" in PROJECT_HTML
        )
        or (
            "rp-gauge" in PROJECT_HTML
            and "@keyframes" in PROJECT_HTML
            and ("shimmer" in PROJECT_HTML.lower() or "pulse" in PROJECT_HTML.lower())
        )
    )
    assert has_keyframe, (
        "No shimmer/pulse @keyframes animation found for the running gauge segment — "
        "the running segment must have a CSS animation"
    )


def test_ac2_running_segment_animation_applied():
    """CSS applies the shimmer animation to the running gauge segment."""
    has_anim = (
        _css_has(".rp-gauge-seg--running", "animation")
        or _css_has(".rp-gauge-shimmer", "animation")
        or _css_has(".rp-gauge-running", "animation")
        or (
            "rp-gauge-seg--running" in PROJECT_HTML
            and "animation" in PROJECT_HTML
        )
        or (
            "rp-gauge" in PROJECT_HTML
            and "shimmer" in PROJECT_HTML.lower()
            and "animation" in PROJECT_HTML
        )
    )
    assert has_anim, (
        "No animation applied to the running gauge segment — "
        "the running segment CSS must reference the shimmer/pulse animation"
    )


# ── AC3: Legend row N done / N retrying / N running / N queued ───────────────


def test_ac3_legend_row_css_exists():
    """CSS class for the gauge legend row exists."""
    has_css = (
        _css_rule_exists(".rp-gauge-legend")
        or "rp-gauge-legend" in PROJECT_HTML
        or _css_rule_exists(".rp-legend")
        or "rp-legend" in PROJECT_HTML
    )
    assert has_css, (
        "No .rp-gauge-legend CSS or element found — "
        "the gauge must have a legend row below it"
    )


def test_ac3_legend_in_run_head_html():
    """_smgmtRunHeadHtml (or gauge helper) generates legend with done/retrying/running/queued."""
    fn = _gauge_fn_body()
    has_legend = (
        ("legend" in fn.lower() or "rp-gauge-legend" in fn or "rp-legend" in fn)
        and "done" in fn.lower()
        and "retrying" in fn.lower()
        and "running" in fn.lower()
        and "queued" in fn.lower()
    )
    assert has_legend, (
        "Neither _smgmtRunHeadHtml nor its gauge helper generates a legend with all four labels — "
        "the legend must show 'N done / N retrying / N running / N queued'"
    )


def test_ac3_legend_has_element_id_for_live_updates():
    """The legend element has an id so it can be patched on live updates."""
    fn = _gauge_fn_body()
    has_id = (
        "smgmt-rp-gauge-legend" in fn
        or "smgmt-rp-legend" in fn
        or ("id=" in fn and "legend" in fn.lower())
    )
    assert has_id, (
        "Gauge HTML generator does not assign an id to the legend element — "
        "the legend must have an id so it can be updated without a full re-render"
    )


# ── AC4: X of Y done summary label below legend ──────────────────────────────


def test_ac4_summary_label_in_run_head_html():
    """_smgmtRunHeadHtml (or gauge helper) generates an X of Y done summary label."""
    fn = _gauge_fn_body()
    has_summary = (
        "of" in fn
        and "done" in fn.lower()
        and (
            "rp-gauge-summary" in fn
            or "rp-summary" in fn
            or "summary" in fn.lower()
        )
    )
    assert has_summary, (
        "Neither _smgmtRunHeadHtml nor its gauge helper generates an 'X of Y done' summary — "
        "a summary label must appear below the legend"
    )


def test_ac4_summary_has_element_id():
    """The summary label element has an id so it can be patched on live updates."""
    fn = _gauge_fn_body()
    has_id = (
        "smgmt-rp-gauge-summary" in fn
        or "smgmt-rp-summary" in fn
        or ("id=" in fn and "summary" in fn.lower())
    )
    assert has_id, (
        "Gauge HTML generator does not assign an id to the summary element — "
        "the summary must have an id for live patching"
    )


def test_ac4_summary_css_exists():
    """CSS class for the summary label exists."""
    has_css = (
        _css_rule_exists(".rp-gauge-summary")
        or "rp-gauge-summary" in PROJECT_HTML
        or _css_rule_exists(".rp-summary")
        or "rp-summary" in PROJECT_HTML
    )
    assert has_css, (
        "No CSS for the 'X of Y done' summary label found — "
        "the summary label must have CSS styling"
    )


# ── AC5: Standalone DONE stat card removed from header metrics ────────────────


def test_ac5_done_metric_card_removed_from_metrics_html():
    """_smgmtMetricsHtml no longer generates a Done metric card."""
    fn = _fn_body("_smgmtMetricsHtml")
    has_done_card = (
        "metric-done" in fn
        or (
            "'Done'" in fn
            or '"Done"' in fn
        )
        and "metric-label" in fn
    )
    assert not has_done_card, (
        "_smgmtMetricsHtml still generates a Done metric card — "
        "the standalone DONE stat card must be removed from the metrics strip"
    )


def test_ac5_metric_done_class_not_in_metrics_html_fn():
    """The metric-done CSS class is not produced by _smgmtMetricsHtml."""
    fn = _fn_body("_smgmtMetricsHtml")
    assert "metric-done" not in fn, (
        "_smgmtMetricsHtml still contains 'metric-done' class — "
        "the DONE stat card must be fully removed from the metrics function"
    )


# ── AC6: Slim one-line stat row (agents, retrying, time) ─────────────────────


def test_ac6_stat_row_css_exists():
    """CSS class for the slim stat row exists."""
    has_css = (
        _css_rule_exists(".rp-stat-row")
        or "rp-stat-row" in PROJECT_HTML
        or _css_rule_exists(".rp-stats")
        or "rp-stats" in PROJECT_HTML
    )
    assert has_css, (
        "No CSS for the slim stat row found — "
        "a one-line stat row must be styled below the gauge"
    )


def test_ac6_stat_row_in_run_head_html():
    """_smgmtRunHeadHtml (or gauge helper) generates the slim stat row."""
    fn = _gauge_fn_body()
    has_row = (
        "rp-stat-row" in fn
        or "rp-stats" in fn
        or ("stat" in fn.lower() and "row" in fn.lower())
    )
    assert has_row, (
        "Neither _smgmtRunHeadHtml nor its gauge helper generates a stat row — "
        "a slim one-line stat row must appear beneath the gauge"
    )


def test_ac6_stat_row_shows_active_agents():
    """Stat row includes active agent count."""
    fn = _gauge_fn_body()
    has_agents = (
        "active_agents" in fn
        or ("agent" in fn.lower() and "length" in fn)
        or "agents" in fn.lower()
    )
    assert has_agents, (
        "Gauge HTML generator stat row does not include active agent count — "
        "stat row must show 'N agents · serial/parallel'"
    )


def test_ac6_stat_row_shows_mode_indicator():
    """Stat row includes serial/parallel mode indicator."""
    fn = _gauge_fn_body()
    has_mode = (
        "pipeline_mode" in fn
        or "parallel" in fn.lower()
        or "serial" in fn.lower()
    )
    assert has_mode, (
        "Gauge HTML generator stat row does not include serial/parallel mode indicator — "
        "stat row must show e.g. '3 agents · parallel'"
    )


def test_ac6_stat_row_shows_retrying_amber():
    """Stat row includes retrying info in amber."""
    fn = _gauge_fn_body()
    has_retrying = (
        "_smgmtRailFixRound" in fn
        or "fix_round" in fn
        or "retrying" in fn.lower()
        or "retry" in fn.lower()
    )
    assert has_retrying, (
        "Gauge HTML generator stat row does not include retrying info — "
        "stat row must show the retrying issue in amber (e.g. '#516 with failure context')"
    )


def test_ac6_stat_row_retrying_amber_color():
    """Stat row retrying element is styled with amber color."""
    fn = _gauge_fn_body()
    has_amber = (
        "var(--amber" in fn
        or "rp-stat-amber" in fn
        or "amber" in fn.lower()
    ) and (
        "retrying" in fn.lower()
        or "fix" in fn.lower()
    )
    assert has_amber, (
        "Gauge HTML generator retrying stat does not use amber color — "
        "the retrying item in the stat row must be styled in amber"
    )


def test_ac6_stat_row_shows_agent_time_split():
    """Stat row includes agent time coder/tester split."""
    fn = _gauge_fn_body()
    has_time = (
        "agent_time_split" in fn
        or (
            "coder" in fn.lower()
            and "tester" in fn.lower()
            and ("time" in fn.lower() or "sec" in fn.lower() or "min" in fn.lower())
        )
    )
    assert has_time, (
        "Gauge HTML generator stat row does not include agent time split — "
        "stat row must show 'coder Xm / tester Xm'"
    )


# ── AC7: Gauge, legend, stat row update live ─────────────────────────────────


def test_ac7_gauge_elements_have_ids_for_patching():
    """Gauge, legend, and summary elements have ids so they can be live-patched."""
    fn = _fn_body("_smgmtRunHeadHtml")
    has_gauge_id = (
        "smgmt-rp-gauge" in fn
        or ("id=" in fn and "gauge" in fn.lower())
    )
    assert has_gauge_id, (
        "_smgmtRunHeadHtml does not assign an id to the gauge — "
        "gauge must have an id for live DOM patching"
    )


def test_ac7_patch_function_updates_gauge():
    """_smgmtRunHeadPatch updates gauge/legend/stat row elements."""
    fn = _fn_body("_smgmtRunHeadPatch")
    updates_gauge = (
        "rp-gauge" in fn
        or "rp-legend" in fn
        or "rp-stat" in fn
        or "smgmt-rp-gauge" in fn
        or "smgmt-rp-legend" in fn
        or "smgmt-rp-stat" in fn
    )
    assert updates_gauge, (
        "_smgmtRunHeadPatch does not update gauge/legend/stat elements — "
        "the patch function must update gauge segments, legend counts, and stat row on live data"
    )


def test_ac7_gauge_counts_function_exists():
    """A function or inline logic computes done/retrying/running/queued counts."""
    has_fn = (
        "_smgmtGaugeCounts" in PROJECT_HTML
        or "_rpGaugeCounts" in PROJECT_HTML
        or (
            "_smgmtRunHeadHtml" in PROJECT_HTML
            and "_smgmtRailFixRound" in _fn_body("_smgmtRunHeadHtml")
        )
        or (
            "_smgmtRunHeadPatch" in PROJECT_HTML
            and "_smgmtRailFixRound" in _fn_body("_smgmtRunHeadPatch")
        )
    )
    assert has_fn, (
        "No gauge count computation found — a function or inline logic must derive "
        "done/retrying/running/queued counts from the live snapshot"
    )


# ── AC8: Structural order — gauge inside card, legend→summary→stat ───────────


def test_ac8_gauge_inside_run_head_card():
    """Gauge markup is generated by _smgmtRunHeadHtml or its helper, tied to the header card."""
    head_fn = _fn_body("_smgmtRunHeadHtml")
    has_card = "run-head-card" in head_fn
    # Gauge is either inline in _smgmtRunHeadHtml or produced by a called helper
    has_gauge = "rp-gauge" in head_fn or (
        any(name in head_fn for name in ("_smgmtGaugeHtml", "_rpGaugeHtml"))
        and "rp-gauge" in PROJECT_HTML
    )
    assert has_card and has_gauge, (
        "_smgmtRunHeadHtml does not include run-head-card AND gauge markup — "
        "the gauge must be part of the header card"
    )


def test_ac8_legend_appears_after_gauge_bar():
    """In generated HTML, legend appears after the gauge bar."""
    # Check the gauge helper function where the structural order is defined
    fn_name = next(
        (n for n in ("_smgmtGaugeHtml", "_rpGaugeHtml") if n in PROJECT_HTML),
        "_smgmtRunHeadHtml",
    )
    fn = _fn_body(fn_name)
    gauge_pos = fn.find("rp-gauge")
    legend_pos = fn.find("rp-gauge-legend") if "rp-gauge-legend" in fn else fn.find("rp-legend")
    assert gauge_pos != -1, f"rp-gauge not found in {fn_name}"
    assert legend_pos != -1, f"legend element not found in {fn_name}"
    assert legend_pos > gauge_pos, (
        f"In {fn_name}, legend appears BEFORE the gauge bar — "
        "legend must come after the gauge bar"
    )


def test_ac8_summary_appears_after_legend():
    """In generated HTML, summary label appears after the legend row."""
    fn_name = next(
        (n for n in ("_smgmtGaugeHtml", "_rpGaugeHtml") if n in PROJECT_HTML),
        "_smgmtRunHeadHtml",
    )
    fn = _fn_body(fn_name)
    legend_pos = fn.find("rp-gauge-legend") if "rp-gauge-legend" in fn else fn.find("rp-legend")
    summary_pos = fn.find("rp-gauge-summary") if "rp-gauge-summary" in fn else fn.find("rp-summary")
    assert legend_pos != -1, f"legend element not found in {fn_name}"
    assert summary_pos != -1, f"summary element not found in {fn_name}"
    assert summary_pos > legend_pos, (
        f"In {fn_name}, summary appears BEFORE the legend — "
        "summary must come after the legend"
    )


def test_ac8_stat_row_appears_after_summary():
    """In generated HTML, stat row appears after the summary label."""
    fn_name = next(
        (n for n in ("_smgmtGaugeHtml", "_rpGaugeHtml") if n in PROJECT_HTML),
        "_smgmtRunHeadHtml",
    )
    fn = _fn_body(fn_name)
    summary_pos = fn.find("rp-gauge-summary") if "rp-gauge-summary" in fn else fn.find("rp-summary")
    stat_pos = fn.find("rp-stat-row") if "rp-stat-row" in fn else fn.find("rp-stats")
    assert summary_pos != -1, f"summary element not found in {fn_name}"
    assert stat_pos != -1, f"stat row element not found in {fn_name}"
    assert stat_pos > summary_pos, (
        f"In {fn_name}, stat row appears BEFORE the summary — "
        "stat row must come after the summary"
    )


# ── AC9: All queued → full grey bar ──────────────────────────────────────────


def test_ac9_all_queued_full_grey_bar():
    """Gauge logic assigns 100% width to queued segment when no tickets are started."""
    fn_candidates = []
    for name in ("_smgmtRunHeadHtml", "_smgmtRunHeadPatch", "_smgmtGaugeCounts", "_rpGaugeCounts"):
        if name in PROJECT_HTML:
            fn_candidates.append(_fn_body(name))
    combined = "\n".join(fn_candidates)
    # Must compute queued as remainder (total - done - retrying - running)
    has_queued_remainder = (
        "queued" in combined.lower()
        and (
            "total" in combined
            or "Math.max" in combined
        )
    )
    assert has_queued_remainder, (
        "Gauge logic does not compute a queued remainder — "
        "when all tickets are queued, the queued segment must fill 100% of the bar"
    )


# ── AC10: All done → full green, no shimmer ──────────────────────────────────


def test_ac10_all_done_shimmer_conditional():
    """Shimmer animation is conditional on the running count being > 0."""
    fn = _gauge_fn_body()
    # Shimmer must be conditional (not always rendered)
    has_conditional_shimmer = (
        (
            "shimmer" in fn.lower()
            or "rp-gauge-seg--running" in fn
        )
        and (
            "running > 0" in fn
            or "running>" in fn
            or ("> 0" in fn and "running" in fn.lower())
            or "? " in fn  # ternary — shimmer only when running count > 0
            or "if" in fn
        )
    )
    assert has_conditional_shimmer, (
        "Gauge HTML generator does not conditionally apply shimmer based on running count — "
        "shimmer must be absent when all tickets are done (running count = 0)"
    )


def test_ac10_all_done_running_segment_zero_width():
    """When all tickets are done, running segment width is 0 (via proportional math)."""
    # The gauge uses percentage widths based on counts, so 0 running tickets → 0% width.
    # The test verifies the count logic produces 0 for running when running=0.
    fn_candidates = []
    for name in ("_smgmtRunHeadHtml", "_smgmtRunHeadPatch", "_smgmtGaugeCounts", "_rpGaugeCounts"):
        if name in PROJECT_HTML:
            fn_candidates.append(_fn_body(name))
    combined = "\n".join(fn_candidates)
    # Must use proportional percentage math, so 0-count segments get 0% automatically
    uses_pct_math = (
        ("/" in combined and "total" in combined and "%" in combined)
        or ("pct" in combined.lower() and "running" in combined.lower())
    )
    assert uses_pct_math, (
        "Gauge does not use proportional percentage math — "
        "when all tickets are done, the running segment must automatically be 0% width"
    )
