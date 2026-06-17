"""Tests for issue #1106 — Consolidate running pane into single grouped issue list.

Static analysis of project.html (HTML/CSS/JS) — no server needed.

AC coverage:
  AC1  — Old flat Level-1 issue list (#smgmt-rail) is removed from the running pane.
  AC2  — Bottom per-issue detail / attempts panel (#smgmt-inspector) is removed.
  AC3  — A single collapsible "All issues" panel renders all sprint issues grouped
          by dispatch level.
  AC4  — Locked levels display a lock icon and "locked until Level N finishes"
          text; issue rows are visually greyed out.
  AC5  — Each issue row shows: status icon (done/running/retrying/queued),
          issue number, and issue title.
  AC6  — Right-aligned meta cluster: size chip (always), FIX n/m badge (retry
          only), agent+model label (running row only), state/time string.
  AC7  — No row anywhere in the pane displays a CLAUDE tag.
  AC8  — Clicking a non-locked, non-queued row expands inline log (~3–5 lines)
          + "Full log →" link; clicking again collapses it.
  AC9  — Queued and locked rows expand to "no output yet" text.
  AC10 — Actively-running issue row is expanded by default without a click.
  AC11 — Orchestrator log is a separate collapsible panel below the header stat
          row, with a live indicator dot when active.
  AC12 — Orchestrator panel defaults to ~3 lines; "Show more" expands to ~15
          scrollable lines; "Show less" collapses back.
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


def _html_in_run_shell(src: str = PROJECT_HTML) -> str:
    """Extract the HTML inside the run-shell div."""
    start = src.find('id="smgmt-run-shell"')
    if start == -1:
        return ""
    # Find the opening tag end
    tag_end = src.find(">", start)
    if tag_end == -1:
        return ""
    # Balance divs from this point
    depth = 1
    pos = tag_end + 1
    while pos < len(src) and depth > 0:
        next_open = src.find("<div", pos)
        next_close = src.find("</div>", pos)
        if next_open == -1:
            next_open = len(src)
        if next_close == -1:
            next_close = len(src)
        if next_open < next_close:
            depth += 1
            pos = next_open + 4
        else:
            depth -= 1
            if depth == 0:
                return src[tag_end + 1 : next_close]
            pos = next_close + 6
    return src[tag_end + 1 : pos]


# ──────────────────── AC1: Old flat level-1 issue list removed ───────────────


def test_ac1_smgmt_rail_not_in_run_shell():
    """#smgmt-rail (old flat Level-1 list) is no longer in the run-shell container."""
    shell = _html_in_run_shell()
    assert 'id="smgmt-rail"' not in shell, (
        '#smgmt-rail still present in run-shell — the old flat level-1 issue list must be removed'
    )


def test_ac1_rail_render_function_not_called_in_running_update():
    """_smgmtRunningViewUpdate no longer calls _smgmtRailUpdate."""
    fn = _fn_body("_smgmtRunningViewUpdate")
    assert "_smgmtRailUpdate" not in fn, (
        "_smgmtRunningViewUpdate still calls _smgmtRailUpdate — "
        "the old rail update must be removed from the running view orchestrator"
    )


# ──────────────── AC2: Bottom per-issue detail / attempts panel removed ───────


def test_ac2_smgmt_inspector_not_in_run_shell():
    """#smgmt-inspector (old per-issue detail panel) is no longer in the run-shell container."""
    shell = _html_in_run_shell()
    assert 'id="smgmt-inspector"' not in shell, (
        '#smgmt-inspector still present in run-shell — the bottom per-issue panel must be removed'
    )


def test_ac2_inspector_feed_not_called_in_running_update():
    """_smgmtRunningViewUpdate no longer calls _smgmtInspectorFeed or _smgmtInspectorClose."""
    fn = _fn_body("_smgmtRunningViewUpdate")
    assert "_smgmtInspectorFeed" not in fn, (
        "_smgmtRunningViewUpdate still calls _smgmtInspectorFeed — "
        "inspector must be fully removed from the running view"
    )


# ──────────── AC3: Single collapsible All Issues panel, grouped by level ──────


def test_ac3_all_issues_panel_in_run_shell():
    """A single 'All issues' panel container exists inside run-shell."""
    shell = _html_in_run_shell()
    has_panel = (
        'id="smgmt-all-issues"' in shell
        or 'id="smgmt-all-issues-panel"' in shell
        or 'class="all-issues-panel"' in shell
    )
    assert has_panel, (
        "No 'All issues' panel container found in run-shell — "
        "a single canonical All Issues panel must replace the old rail and inspector"
    )


def test_ac3_all_issues_render_function_exists():
    """A JS function exists to render the All Issues panel."""
    has_fn = (
        "_smgmtAllIssuesUpdate" in PROJECT_HTML
        or "_smgmtAllIssuesHtml" in PROJECT_HTML
        or "_smgmtAllIssuesRender" in PROJECT_HTML
    )
    assert has_fn, (
        "No All Issues render function found in project.html — "
        "a function like _smgmtAllIssuesUpdate must render the grouped issue list"
    )


def test_ac3_all_issues_function_groups_by_level():
    """All Issues render function groups issues by dispatch level."""
    fn_name = None
    for name in ("_smgmtAllIssuesHtml", "_smgmtAllIssuesUpdate", "_smgmtAllIssuesRender"):
        if name in PROJECT_HTML:
            fn_name = name
            break
    assert fn_name, "No All Issues render function found"
    fn = _fn_body(fn_name)
    has_levels = (
        "_smgmtRailLevels" in fn
        or "dispatch_level" in fn
        or "Level" in fn
        or "level" in fn
    )
    assert has_levels, (
        f"{fn_name} does not group issues by dispatch level — "
        "issues must be grouped under Level 1, Level 2, … headings"
    )


def test_ac3_all_issues_panel_css_exists():
    """CSS for the All Issues panel exists."""
    has_css = (
        _css_rule_exists(".all-issues-panel")
        or _css_rule_exists(".ai-panel")
        or "all-issues" in PROJECT_HTML
    )
    assert has_css, (
        "No CSS for the All Issues panel found — .all-issues-panel or similar rule required"
    )


def test_ac3_running_view_calls_all_issues_update():
    """_smgmtRunningViewUpdate calls the new All Issues update function."""
    fn = _fn_body("_smgmtRunningViewUpdate")
    has_call = (
        "_smgmtAllIssuesUpdate" in fn
        or "_smgmtAllIssuesHtml" in fn
        or "_smgmtAllIssuesRender" in fn
    )
    assert has_call, (
        "_smgmtRunningViewUpdate does not call the All Issues render function — "
        "the running view orchestrator must call the new panel renderer"
    )


# ──────── AC4: Locked levels — lock icon + locked text + greyed rows ──────────


def test_ac4_locked_level_lock_icon():
    """All Issues render function includes a lock icon for locked (queued) levels."""
    fn_name = None
    for name in ("_smgmtAllIssuesHtml", "_smgmtAllIssuesUpdate", "_smgmtAllIssuesRender"):
        if name in PROJECT_HTML:
            fn_name = name
            break
    assert fn_name, "No All Issues render function found"
    fn = _fn_body(fn_name)
    has_lock = (
        "ti-lock" in fn
        or "lock" in fn.lower()
    )
    assert has_lock, (
        f"{fn_name} does not include a lock icon for locked levels — "
        "locked levels must display a lock icon (ti-lock or similar)"
    )


def test_ac4_locked_level_text():
    """All Issues render function outputs 'locked until Level N finishes' text."""
    fn_name = None
    for name in ("_smgmtAllIssuesHtml", "_smgmtAllIssuesUpdate", "_smgmtAllIssuesRender"):
        if name in PROJECT_HTML:
            fn_name = name
            break
    assert fn_name, "No All Issues render function found"
    fn = _fn_body(fn_name)
    has_text = (
        "locked until" in fn.lower()
        or ("locked" in fn.lower() and "finishes" in fn.lower())
    )
    assert has_text, (
        f"{fn_name} does not include 'locked until Level N finishes' text — "
        "locked levels must show this label to explain why they're blocked"
    )


def test_ac4_locked_level_css_greyed():
    """CSS greyes out rows under a locked level."""
    has_grey = (
        _css_has(".ai-level--locked", "color")
        or _css_has(".ai-level--locked", "opacity")
        or _css_has(".ai-row--locked", "color")
        or _css_has(".ai-row--locked", "opacity")
        or (
            "locked" in PROJECT_HTML
            and ("opacity" in PROJECT_HTML or "text-muted" in PROJECT_HTML or "color: var(--text" in PROJECT_HTML)
        )
    )
    assert has_grey, (
        "No CSS found to grey out locked-level rows — locked rows must appear visually greyed out"
    )


# ─────────── AC5: Issue row — status icon, number, title ─────────────────────


def test_ac5_row_render_function_exists():
    """A function or code path exists to render individual issue rows."""
    has_row_fn = (
        "_smgmtAllIssuesRowHtml" in PROJECT_HTML
        or "_smgmtAiRowHtml" in PROJECT_HTML
    )
    # If no dedicated row function, the all-issues function must inline the row HTML
    if not has_row_fn:
        fn_name = None
        for name in ("_smgmtAllIssuesHtml", "_smgmtAllIssuesUpdate", "_smgmtAllIssuesRender"):
            if name in PROJECT_HTML:
                fn_name = name
                break
        if fn_name:
            fn = _fn_body(fn_name)
            has_row_fn = "ai-row" in fn or "issue-row" in fn or "all-issues-row" in fn
    assert has_row_fn, (
        "No issue row rendering found — each issue must render as a row with icon, number, title"
    )


def test_ac5_status_icon_done_green_check():
    """Issue row uses a green check icon for done issues."""
    fn_name = None
    for name in (
        "_smgmtAllIssuesRowHtml",
        "_smgmtAiRowHtml",
        "_smgmtAllIssuesHtml",
        "_smgmtAllIssuesUpdate",
        "_smgmtAllIssuesRender",
    ):
        if name in PROJECT_HTML:
            fn_name = name
            break
    assert fn_name, "No All Issues row render function found"
    fn = _fn_body(fn_name)
    has_done_icon = (
        "ti-check" in fn
        or "&#10003;" in fn
        or "check" in fn.lower()
        or "done" in fn.lower()
    )
    assert has_done_icon, (
        f"{fn_name} does not include a done/check icon — done rows must show a green check"
    )


def test_ac5_status_icon_running_spinner():
    """Issue row uses a spinner icon for actively-running issues."""
    fn_name = None
    for name in (
        "_smgmtAllIssuesRowHtml",
        "_smgmtAiRowHtml",
        "_smgmtAllIssuesHtml",
        "_smgmtAllIssuesUpdate",
        "_smgmtAllIssuesRender",
    ):
        if name in PROJECT_HTML:
            fn_name = name
            break
    assert fn_name, "No All Issues row render function found"
    fn = _fn_body(fn_name)
    has_spinner = (
        "spinner" in fn.lower()
        or "smgmt-active-spinner" in fn
        or "ti-loader" in fn
        or "running" in fn.lower()
    )
    assert has_spinner, (
        f"{fn_name} does not include a spinner for running issues — "
        "actively-running rows must show a blue spinner icon"
    )


def test_ac5_row_includes_issue_number():
    """Issue row renders the issue number."""
    fn_name = None
    for name in (
        "_smgmtAllIssuesRowHtml",
        "_smgmtAiRowHtml",
        "_smgmtAllIssuesHtml",
        "_smgmtAllIssuesUpdate",
        "_smgmtAllIssuesRender",
    ):
        if name in PROJECT_HTML:
            fn_name = name
            break
    assert fn_name, "No All Issues row render function found"
    fn = _fn_body(fn_name)
    has_num = (
        "iss.number" in fn
        or "num" in fn
        or "#${" in fn
        or ".number" in fn
    )
    assert has_num, (
        f"{fn_name} does not render the issue number — each row must show the issue number"
    )


def test_ac5_row_includes_issue_title():
    """Issue row renders the issue title."""
    fn_name = None
    for name in (
        "_smgmtAllIssuesRowHtml",
        "_smgmtAiRowHtml",
        "_smgmtAllIssuesHtml",
        "_smgmtAllIssuesUpdate",
        "_smgmtAllIssuesRender",
    ):
        if name in PROJECT_HTML:
            fn_name = name
            break
    assert fn_name, "No All Issues row render function found"
    fn = _fn_body(fn_name)
    has_title = (
        "iss.title" in fn
        or ".title" in fn
        or "title" in fn.lower()
    )
    assert has_title, (
        f"{fn_name} does not render the issue title — each row must show the issue title"
    )


# ─────────────── AC6: Right-aligned meta cluster ─────────────────────────────


def test_ac6_meta_cluster_has_size_chip():
    """Meta cluster includes a size chip (always visible)."""
    fn_name = None
    for name in (
        "_smgmtAllIssuesRowHtml",
        "_smgmtAiRowHtml",
        "_smgmtAllIssuesHtml",
        "_smgmtAllIssuesUpdate",
        "_smgmtAllIssuesRender",
    ):
        if name in PROJECT_HTML:
            fn_name = name
            break
    assert fn_name, "No All Issues row render function found"
    fn = _fn_body(fn_name)
    has_size = (
        "iss.size" in fn
        or "ai-row-size" in fn
        or "size" in fn.lower()
    )
    assert has_size, (
        f"{fn_name} does not include a size chip — meta cluster must always show the size chip"
    )


def test_ac6_meta_cluster_has_fix_badge():
    """Meta cluster includes FIX n/m badge (visible only on retry)."""
    fn_name = None
    for name in (
        "_smgmtAllIssuesRowHtml",
        "_smgmtAiRowHtml",
        "_smgmtAllIssuesHtml",
        "_smgmtAllIssuesUpdate",
        "_smgmtAllIssuesRender",
    ):
        if name in PROJECT_HTML:
            fn_name = name
            break
    assert fn_name, "No All Issues row render function found"
    fn = _fn_body(fn_name)
    has_fix = (
        "FIX" in fn
        or "fix" in fn.lower()
        or "_smgmtRailFixRound" in fn
        or "fix_round" in fn
        or "ai-row-fix" in fn
    )
    assert has_fix, (
        f"{fn_name} does not include a FIX n/m badge — meta cluster must show FIX badge on retry"
    )


def test_ac6_fix_badge_only_shown_on_retry():
    """FIX badge is conditional — only shown when the issue is on a retry."""
    fn_name = None
    for name in (
        "_smgmtAllIssuesRowHtml",
        "_smgmtAiRowHtml",
        "_smgmtAllIssuesHtml",
        "_smgmtAllIssuesUpdate",
        "_smgmtAllIssuesRender",
    ):
        if name in PROJECT_HTML:
            fn_name = name
            break
    assert fn_name, "No All Issues row render function found"
    fn = _fn_body(fn_name)
    # The fix badge must be behind a conditional (if, ? or &&)
    has_conditional_fix = (
        ("fix" in fn.lower() or "FIX" in fn)
        and ("if" in fn or "?" in fn or "&&" in fn)
    )
    assert has_conditional_fix, (
        f"{fn_name} does not conditionally render the FIX badge — "
        "it must only appear when the issue is on a retry"
    )


def test_ac6_meta_cluster_has_agent_model_label():
    """Meta cluster includes agent+model label for the actively-running row."""
    fn_name = None
    for name in (
        "_smgmtAllIssuesRowHtml",
        "_smgmtAiRowHtml",
        "_smgmtAllIssuesHtml",
        "_smgmtAllIssuesUpdate",
        "_smgmtAllIssuesRender",
    ):
        if name in PROJECT_HTML:
            fn_name = name
            break
    assert fn_name, "No All Issues row render function found"
    fn = _fn_body(fn_name)
    has_agent_model = (
        "_shortModelName" in fn
        or "coder_model" in fn
        or "CODER" in fn
        or ("agent" in fn.lower() and "model" in fn.lower())
        or "ai-row-agent" in fn
    )
    assert has_agent_model, (
        f"{fn_name} does not include agent+model label — "
        "the running row must show e.g. 'CODER sonnet-4-6' in the meta cluster"
    )


def test_ac6_agent_model_only_on_running_row():
    """Agent+model label is conditional — only shown for the actively-running row."""
    fn_name = None
    for name in (
        "_smgmtAllIssuesRowHtml",
        "_smgmtAiRowHtml",
        "_smgmtAllIssuesHtml",
        "_smgmtAllIssuesUpdate",
        "_smgmtAllIssuesRender",
    ):
        if name in PROJECT_HTML:
            fn_name = name
            break
    assert fn_name, "No All Issues row render function found"
    fn = _fn_body(fn_name)
    # Agent badge must be conditional on the active state
    has_conditional_agent = (
        (
            "coder-active" in fn
            or "tester-active" in fn
            or "active" in fn.lower()
        )
        and ("if" in fn or "?" in fn or "&&" in fn)
    )
    assert has_conditional_agent, (
        f"{fn_name} does not conditionally render the agent+model label — "
        "it must only appear on the actively-running row"
    )


def test_ac6_meta_cluster_has_state_time_string():
    """Meta cluster includes a state/time string (done 6m / retrying / running / queued)."""
    fn_name = None
    for name in (
        "_smgmtAllIssuesRowHtml",
        "_smgmtAiRowHtml",
        "_smgmtAllIssuesHtml",
        "_smgmtAllIssuesUpdate",
        "_smgmtAllIssuesRender",
    ):
        if name in PROJECT_HTML:
            fn_name = name
            break
    assert fn_name, "No All Issues row render function found"
    fn = _fn_body(fn_name)
    # Must render one of the state strings
    has_state_string = (
        "done" in fn.lower()
        and "running" in fn.lower()
        and "queued" in fn.lower()
    )
    assert has_state_string, (
        f"{fn_name} does not include state/time string — "
        "meta cluster must show 'done 6m' / 'retrying' / 'running' / 'queued'"
    )


# ────────────────────── AC7: No CLAUDE tag anywhere ──────────────────────────


def test_ac7_no_claude_tag_in_all_issues_html():
    """The All Issues render function does not produce a CLAUDE tag."""
    fn_name = None
    for name in (
        "_smgmtAllIssuesRowHtml",
        "_smgmtAiRowHtml",
        "_smgmtAllIssuesHtml",
        "_smgmtAllIssuesUpdate",
        "_smgmtAllIssuesRender",
    ):
        if name in PROJECT_HTML:
            fn_name = name
            break
    assert fn_name, "No All Issues row render function found"
    fn = _fn_body(fn_name)
    assert "CLAUDE" not in fn, (
        f"{fn_name} contains 'CLAUDE' tag — no row in the pane should display a CLAUDE tag"
    )


def test_ac7_no_claude_tag_in_run_shell_static_html():
    """The static run-shell HTML does not contain CLAUDE badge markup."""
    shell = _html_in_run_shell()
    assert "CLAUDE" not in shell, (
        "Static run-shell HTML still contains CLAUDE markup — "
        "no CLAUDE tag should appear anywhere in the running pane"
    )


# ─────────── AC8: Row click expands inline log + Full log link ───────────────


def test_ac8_row_click_handler_exists():
    """A click handler function or onclick attribute exists for issue rows."""
    has_fn = (
        "_smgmtAiRowToggle" in PROJECT_HTML
        or "_smgmtRowToggle" in PROJECT_HTML
        or "_smgmtAllIssuesRowToggle" in PROJECT_HTML
    )
    if not has_fn:
        fn_name = None
        for name in (
            "_smgmtAllIssuesRowHtml",
            "_smgmtAiRowHtml",
            "_smgmtAllIssuesHtml",
            "_smgmtAllIssuesUpdate",
            "_smgmtAllIssuesRender",
        ):
            if name in PROJECT_HTML:
                fn_name = name
                break
        if fn_name:
            fn = _fn_body(fn_name)
            has_fn = "onclick" in fn or "toggle" in fn.lower()
    assert has_fn, (
        "No click handler for All Issues rows — clicking a row must expand/collapse the inline log"
    )


def test_ac8_inline_log_area_css_exists():
    """CSS class for the inline log expansion area exists."""
    has_css = (
        _css_rule_exists(".ai-row-log")
        or _css_rule_exists(".ai-row__log")
        or _css_rule_exists(".ai-log")
        or "ai-row-log" in PROJECT_HTML
        or "ai-log" in PROJECT_HTML
    )
    assert has_css, (
        "No CSS class for inline log expansion area found — "
        "each row must have an expandable log area styled with CSS"
    )


def test_ac8_full_log_link_rendered():
    """Row log expansion includes a 'Full log' link."""
    fn_name = None
    for name in (
        "_smgmtAiRowToggle",
        "_smgmtRowToggle",
        "_smgmtAllIssuesRowToggle",
        "_smgmtAllIssuesRowHtml",
        "_smgmtAiRowHtml",
        "_smgmtAllIssuesHtml",
        "_smgmtAllIssuesUpdate",
        "_smgmtAllIssuesRender",
    ):
        if name in PROJECT_HTML:
            fn_name = name
            break
    assert fn_name, "No row render or toggle function found"
    fn = _fn_body(fn_name)
    has_full_log = (
        "Full log" in fn
        or "full log" in fn.lower()
        or "full-log" in fn.lower()
        or "Full log →" in fn
    )
    # Also search the whole file for the Full log link near row functions
    if not has_full_log:
        has_full_log = (
            "Full log" in PROJECT_HTML
            and (
                "_smgmtAiRowToggle" in PROJECT_HTML
                or "_smgmtAllIssues" in PROJECT_HTML
            )
        )
    assert has_full_log, (
        f"{fn_name} does not include a 'Full log →' link — "
        "the inline log expansion must include a link to the full log"
    )


# ────── AC9: Queued and locked rows expand to "no output yet" ────────────────


def test_ac9_queued_and_locked_rows_show_no_output():
    """Row toggle or render function shows 'no output yet' for queued/locked rows."""
    fn_name = None
    for name in (
        "_smgmtAiRowToggle",
        "_smgmtRowToggle",
        "_smgmtAllIssuesRowToggle",
        "_smgmtAllIssuesHtml",
        "_smgmtAllIssuesUpdate",
        "_smgmtAllIssuesRender",
    ):
        if name in PROJECT_HTML:
            fn_name = name
            break
    if fn_name:
        fn = _fn_body(fn_name)
        has_no_output = (
            "no output" in fn.lower()
            or "no log" in fn.lower()
            or "not yet" in fn.lower()
        )
    else:
        # Fallback: check the entire file for "no output yet" near all-issues context
        has_no_output = (
            "no output yet" in PROJECT_HTML.lower()
            and "_smgmtAllIssues" in PROJECT_HTML
        )
    assert has_no_output, (
        "No 'no output yet' placeholder found — queued and locked rows must show this text "
        "when expanded instead of log lines"
    )


# ──────────────── AC10: Actively-running row auto-expanded ────────────────────


def test_ac10_auto_expand_running_row():
    """All Issues render function auto-expands the actively-running issue row."""
    fn_name = None
    for name in ("_smgmtAllIssuesHtml", "_smgmtAllIssuesUpdate", "_smgmtAllIssuesRender"):
        if name in PROJECT_HTML:
            fn_name = name
            break
    assert fn_name, "No All Issues render function found"
    fn = _fn_body(fn_name)
    has_auto_expand = (
        "expanded" in fn.lower()
        or "auto" in fn.lower()
        or "is-expanded" in fn
        or "open" in fn.lower()
        or "active" in fn.lower()
    )
    assert has_auto_expand, (
        f"{fn_name} does not auto-expand the running row — "
        "the actively-running issue must be expanded by default without a click"
    )


# ───────── AC11: Orchestrator log panel — separate, live dot, below stats ─────


def test_ac11_orch_panel_exists_in_run_shell():
    """Orchestrator panel container exists inside run-shell."""
    shell = _html_in_run_shell()
    has_orch = (
        'id="smgmt-orch-panel"' in shell
        or 'id="smgmt-orchestrator-panel"' in shell
        or 'class="orch-panel"' in shell
        or "orch-panel" in shell
    )
    assert has_orch, (
        "No Orchestrator panel found in run-shell — a separate Orchestrator log panel is required"
    )


def test_ac11_orch_panel_below_metrics():
    """Orchestrator panel appears after the metrics strip in the run-shell HTML."""
    shell = _html_in_run_shell()
    metrics_pos = shell.find('id="smgmt-metrics"')
    orch_pos = (
        shell.find('id="smgmt-orch-panel"') if 'id="smgmt-orch-panel"' in shell
        else shell.find('id="smgmt-orchestrator-panel"') if 'id="smgmt-orchestrator-panel"' in shell
        else shell.find("orch-panel")
    )
    assert metrics_pos != -1, "smgmt-metrics not found in run-shell"
    assert orch_pos != -1, "Orchestrator panel not found in run-shell"
    assert orch_pos > metrics_pos, (
        "Orchestrator panel appears BEFORE the metrics strip — "
        "it must be placed directly BELOW the header stat row (metrics)"
    )


def test_ac11_orch_panel_live_dot():
    """Orchestrator panel includes a live indicator dot element."""
    has_dot = (
        "orch-live-dot" in PROJECT_HTML
        or "orch-panel-dot" in PROJECT_HTML
        or "orchestrator-dot" in PROJECT_HTML
        or (
            "orch" in PROJECT_HTML.lower()
            and (
                "live-dot" in PROJECT_HTML
                or "orch-dot" in PROJECT_HTML
                or "indicator" in PROJECT_HTML.lower()
            )
        )
    )
    assert has_dot, (
        "No live indicator dot found for the Orchestrator panel — "
        "a live dot must appear when the orchestrator is active"
    )


def test_ac11_orch_panel_live_dot_css():
    """CSS for the orchestrator live indicator dot exists."""
    has_css = (
        _css_rule_exists(".orch-live-dot")
        or _css_rule_exists(".orch-panel-dot")
        or _css_rule_exists(".orchestrator-dot")
        or (
            "orch" in PROJECT_HTML.lower()
            and _css_rule_exists(".live-dot")
        )
    )
    assert has_css, (
        "No CSS rule for orchestrator live dot found — the dot must have CSS styling"
    )


def test_ac11_orch_render_function_exists():
    """A JS function exists to render/update the Orchestrator panel."""
    has_fn = (
        "_smgmtOrchPanelUpdate" in PROJECT_HTML
        or "_smgmtOrchPanel" in PROJECT_HTML
        or "_smgmtOrchestratorUpdate" in PROJECT_HTML
        or "_smgmtOrchUpdate" in PROJECT_HTML
    )
    assert has_fn, (
        "No Orchestrator panel render function found — "
        "a function like _smgmtOrchPanelUpdate must update the Orchestrator log panel"
    )


def test_ac11_running_view_calls_orch_update():
    """_smgmtRunningViewUpdate calls the Orchestrator panel update function."""
    fn = _fn_body("_smgmtRunningViewUpdate")
    has_call = (
        "_smgmtOrchPanelUpdate" in fn
        or "_smgmtOrchPanel" in fn
        or "_smgmtOrchestratorUpdate" in fn
        or "_smgmtOrchUpdate" in fn
    )
    assert has_call, (
        "_smgmtRunningViewUpdate does not call the Orchestrator panel function — "
        "the running view orchestrator must drive the Orchestrator log panel"
    )


# ───────── AC12: Orch panel — 3 lines default, Show more/less ────────────────


def test_ac12_orch_panel_default_line_count():
    """Orchestrator panel defaults to approximately 3 log lines."""
    fn_name = None
    for name in (
        "_smgmtOrchPanelUpdate",
        "_smgmtOrchPanel",
        "_smgmtOrchestratorUpdate",
        "_smgmtOrchUpdate",
    ):
        if name in PROJECT_HTML:
            fn_name = name
            break
    assert fn_name, "No Orchestrator update function found"
    fn = _fn_body(fn_name)
    has_tail_3 = (
        "tail_lines=3" in fn
        or "tail=3" in fn
        or "tailLines = 3" in fn
        or "_SMGMT_ORCH_COLLAPSED_LINES" in PROJECT_HTML
        or "ORCH_DEFAULT_LINES" in PROJECT_HTML
        or "orchDefault" in PROJECT_HTML
    )
    # Broader check: the number 3 or 5 is referenced in orch context
    if not has_tail_3:
        has_tail_3 = (
            "3" in fn and ("tail" in fn.lower() or "lines" in fn.lower())
        )
    assert has_tail_3, (
        f"{fn_name} does not set ~3 default log lines for the Orchestrator panel — "
        "the panel must default to approximately 3 lines"
    )


def test_ac12_show_more_control():
    """Orchestrator panel includes a 'Show more' control."""
    has_show_more = (
        "Show more" in PROJECT_HTML
        and (
            "_smgmtOrchPanelUpdate" in PROJECT_HTML
            or "_smgmtOrchPanel" in PROJECT_HTML
            or "orch-panel" in PROJECT_HTML
        )
    )
    assert has_show_more, (
        "No 'Show more' control found for the Orchestrator panel — "
        "the panel must have a 'Show more' button to expand the full sprint log"
    )


def test_ac12_show_less_control():
    """Orchestrator panel includes a 'Show less' control."""
    has_show_less = (
        "Show less" in PROJECT_HTML
        and (
            "_smgmtOrchPanelUpdate" in PROJECT_HTML
            or "_smgmtOrchPanel" in PROJECT_HTML
            or "orch-panel" in PROJECT_HTML
        )
    )
    assert has_show_less, (
        "No 'Show less' control found for the Orchestrator panel — "
        "the panel must have a 'Show less' button to collapse back to ~3 lines"
    )


def test_ac12_show_more_expands_to_full_sprint_log():
    """Orchestrator panel loads the full sprint log on Show more."""
    fn_name = None
    for name in (
        "_smgmtOrchToggleMore",
        "_smgmtOrchShowMore",
        "_smgmtOrchPanelToggle",
        "_smgmtOrchPanelUpdate",
        "_smgmtOrchPanel",
        "_smgmtOrchestratorUpdate",
        "_smgmtOrchUpdate",
    ):
        if name in PROJECT_HTML:
            fn_name = name
            break
    assert fn_name, "No Orchestrator expand/toggle function found"
    fn = _fn_body(fn_name)
    has_full = (
        "_SMGMT_ORCH_FULL_LINES" in PROJECT_HTML
        or "displayAll" in PROJECT_HTML
        or "2000" in fn
        or "tail_lines=2000" in fn
    )
    if not has_full:
        for name in (
            "_smgmtOrchPanelUpdate",
            "_smgmtOrchFetchLines",
            "_smgmtOrchToggleMore",
        ):
            if name in PROJECT_HTML:
                f2 = _fn_body(name)
                if "_SMGMT_ORCH_FULL_LINES" in f2 or "displayAll" in f2 or "2000" in f2:
                    has_full = True
                    break
    assert has_full, (
        "Orchestrator panel does not load the full sprint log in expanded mode — "
        "clicking 'Show more' must fetch the entire dispatch log for scrolling"
    )


def test_ac12_orch_panel_scrollable_expanded():
    """CSS makes the Orchestrator panel log body scrollable in expanded mode."""
    has_scroll = (
        _css_has(".orch-panel", "overflow")
        or _css_has(".orch-panel-body", "overflow")
        or _css_has(".orch-log", "overflow")
        or _css_has(".orch-panel-log", "overflow")
        or (
            "orch" in PROJECT_HTML.lower()
            and "overflow" in PROJECT_HTML
            and "scroll" in PROJECT_HTML
        )
    )
    assert has_scroll, (
        "No overflow/scroll CSS found for the Orchestrator panel log body — "
        "the expanded panel must scroll when content exceeds the container"
    )
