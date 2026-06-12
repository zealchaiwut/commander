"""Tests for issue #810 — run-stats block + gantt timeline (frontend half).

The expanded History card gains a ``.stats`` block: stat chips, an agent-time
``.split-bar``, and a per-ticket ``.gantt`` timeline, all fed by the
``GET /api/sprints/{label}/run-stats`` aggregation and lazily loaded the first
time a card is expanded. Following the pattern for the History frontend tickets
(#806/#807/#808) the assertions read the static HTML/JS source and check the
design-contract classes and the builder's field sourcing / guards.

AC coverage (frontend):
  AC1  — ``.stat-chips`` for wall time, agent time, tokens ≈ $, fix-round count
         (with ticket ref), slowest ticket, and parallel-saved time.
  AC2  — ``.split-bar`` renders agent-time percentage segments.
  AC3  — ``.gantt`` rows with purple coder + amber tester segments; fix-round
         segments are striped (``.g-fix``).
  AC4  — failed sprints paint a red ✕ crash marker at the crash offset.
  AC5  — only reached tickets get rows (driven by the endpoint's ``tickets``).
  AC6  — the stats block renders on locked rows (lock suppresses actions only).
  AC7  — with no agent_runs the split-bar and gantt are hidden; wall + token
         chips still render, with no JS errors (guarded access).
  AC10 — targets the ``.stats`` / ``.stat-chips`` / ``.split-bar`` / ``.gantt`` /
         ``.g-*`` design-contract classes and passes the impeccable bans.
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

PROJECT_HTML = (DASHBOARD_DIR / "static" / "project.html").read_text(encoding="utf-8")


def _fn_body(name: str, src: str = PROJECT_HTML) -> str:
    """Return the brace-balanced body of a JS function defined in ``src``."""
    for needle in (f"function {name}(", f"{name} = function", f"async function {name}("):
        pos = src.find(needle)
        if pos != -1:
            break
    assert pos != -1, f"function {name} not found"
    brace = src.find("{", pos)
    depth = 0
    for i in range(brace, len(src)):
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[brace:i + 1]
    raise AssertionError(f"unbalanced braces for {name}")


def _css_rule(selector: str) -> str:
    """Return the body of the first CSS rule whose selector list contains `selector`."""
    pat = re.compile(re.escape(selector) + r"(?![\w-])[^{}]*\{([^{}]*)\}")
    m = pat.search(PROJECT_HTML)
    assert m is not None, f"CSS rule for `{selector}` not found"
    return m.group(1)


# ───────────── AC10: design-contract classes are present + styled ────────────

def test_ac10_core_classes_styled():
    for cls in (".stats", ".stat-chips", ".split-bar", ".gantt"):
        _css_rule(cls)  # raises if missing


def test_ac10_gantt_segment_classes_present():
    for cls in (".g-coder", ".g-tester", ".g-fix", ".g-crash", ".g-track", ".g-row"):
        _css_rule(cls)


def test_ac3_coder_is_purple_tester_is_amber():
    assert "--purple" in _css_rule(".g-coder"), "coder gantt segment must be purple"
    assert "--amber" in _css_rule(".g-tester"), "tester gantt segment must be amber"
    # The split-bar swatches follow the same colour language.
    assert "--purple" in _css_rule(".split-seg--coder")
    assert "--amber" in _css_rule(".split-seg--tester")


def test_ac3_fix_round_segment_is_striped():
    rule = _css_rule(".g-fix")
    assert "repeating-linear-gradient" in rule, "fix-round segments must be striped/hatched"


def test_ac4_crash_marker_styled_red():
    assert "--red" in _css_rule(".g-crash"), "crash marker must use the red token"


# ───────────── impeccable bans: no side-stripe, no gradient text ─────────────

def test_no_side_stripe_borders_in_stats_styles():
    for cls in (".stats", ".stat-chip", ".split-bar", ".g-track"):
        rule = _css_rule(cls)
        assert "border-left" not in rule and "border-right" not in rule, \
            f"`{cls}` must not use a side-stripe border (impeccable ban)"


def test_no_gradient_text_in_stats_styles():
    for cls in (".stats", ".stat-chip", ".split-seg", ".stats-section-label"):
        try:
            rule = _css_rule(cls)
        except AssertionError:
            continue
        assert "background-clip" not in rule and "-webkit-background-clip" not in rule, \
            f"gradient text (background-clip) is banned in `{cls}`"


# ───────────── AC1: the stat chips and their sources ─────────────────────────

def test_ac1_builder_emits_all_chip_kinds():
    body = _fn_body("_histStatsHtml")
    assert "stat-chips" in body, "the block must contain a .stat-chips row"
    for token in ("wall", "tokens", "agent time", "fix round", "slowest", "parallel saved"):
        assert token in body, f"stat chips must include the `{token}` chip"


def test_ac1_fix_round_chip_references_tickets():
    body = _fn_body("_histStatsHtml")
    assert "fix_round_tickets" in body, "fix-round chip must reference the ticket id(s)"
    assert "fix_round_count" in body, "fix-round chip must use the fix-round count"


def test_ac1_token_cost_appended_when_present():
    body = _fn_body("_histStatsHtml")
    assert "token_cost_usd" in body, "tokens chip must show ≈ $ when a cost is present"
    assert "≈ $" in body


def test_ac1_slowest_and_parallel_sourced_from_stats():
    body = _fn_body("_histStatsHtml")
    assert "slowest_ticket" in body
    assert "parallel_saved_seconds" in body
    assert "agent_total_seconds" in body, "agent-time chip uses the agent total"


# ───────────── AC2: split-bar renders percentage segments ────────────────────

def test_ac2_split_bar_builder_uses_pct_segments():
    body = _fn_body("_histSplitBarHtml")
    assert "split-bar" in body and "split-seg" in body
    assert "pct" in body, "segments must be sized by their percentage"
    assert "width:" in body, "each segment width is driven by its pct"


# ───────────── AC3: gantt rows, coder/tester segments, fix stripes ───────────

def test_ac3_gantt_builder_positions_segments_on_a_scale():
    body = _fn_body("_histGanttHtml")
    assert "wall_seconds" in body, "segments are positioned on the 0–wall scale"
    assert "g-row" in body and "g-track" in body
    # coder/tester colour split + striped fix-round class
    assert "g-tester" in body and "g-fix" in body
    assert "fix_round" in body, "fix-round segments must be flagged striped"


def test_ac3_gantt_rows_come_from_endpoint_tickets():
    body = _fn_body("_histGanttHtml")
    # AC5: only reached tickets have rows — the builder iterates the endpoint's
    # `tickets` array, so unreached tickets (absent there) get no row.
    assert "stats.tickets" in body or ".tickets" in body


# ───────────── AC4: crash marker gated on a failed sprint ────────────────────

def test_ac4_crash_marker_only_on_failed_sprints():
    body = _fn_body("_histGanttHtml")
    assert "g-crash" in body, "a crash marker element must exist"
    assert "crash" in body
    assert re.search(r"lifecycle_state[^\n]*===\s*'failed'", body) or "'failed'" in body, \
        "the ✕ marker must be gated on the sprint being failed"


# ───────────── AC6: stats block renders on locked rows ───────────────────────

def test_ac6_card_body_includes_stats_block():
    body = _fn_body("_histCardHtml")
    assert "_histStatsHtml(s)" in body, "expanded card body must render the stats block"


def test_ac6_stats_block_is_independent_of_lock_state():
    body = _fn_body("_histStatsHtml")
    # The block must not gate any of its rendering on the lock state — locking
    # suppresses actions (verbs), not read rendering.
    assert "_histIsLocked" not in body and "locked" not in body, \
        "stats block must render identically on locked rows"


# ───────────── AC7: graceful degrade when no agent_runs ──────────────────────

def test_ac7_split_and_gantt_gated_on_has_runs():
    body = _fn_body("_histStatsHtml")
    assert "has_runs" in body, "agent-derived rendering must be gated on has_runs"
    # split-bar / gantt only when runs exist
    assert re.search(r"hasRuns\s*\?\s*_histSplitBarHtml", body), \
        "split-bar must render only when runs exist"
    assert re.search(r"hasRuns\s*\?\s*_histGanttHtml", body), \
        "gantt must render only when runs exist"


def test_ac7_wall_and_token_chips_fall_back_to_history_feed():
    body = _fn_body("_histStatsHtml")
    # When runs are absent the wall/token chips still render from the history
    # record's duration / tokens, so the block is never blank.
    assert "s.duration" in body and "s.tokens" in body, \
        "wall/token chips fall back to the history feed when runs are absent"


# ───────────── lazy load wiring ──────────────────────────────────────────────

def test_expand_triggers_run_stats_fetch():
    body = _fn_body("_histToggleCard")
    assert "_histLoadRunStats" in body, "expanding a card must lazy-load its run-stats"


def test_loader_hits_the_run_stats_endpoint_and_caches():
    body = _fn_body("_histLoadRunStats")
    assert "/run-stats" in body, "loader must call the run-stats endpoint"
    assert "_histRunStats[label]" in body, "loader must cache the result by label"


def test_run_stats_cache_declared():
    assert "const _histRunStats = {}" in PROJECT_HTML, "a run-stats cache must exist"
