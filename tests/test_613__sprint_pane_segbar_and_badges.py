"""Tests for issue #613: Sprint pane segmented bar, level separators, agent badges."""
import re
from pathlib import Path

PROJECT_HTML = Path(__file__).parent.parent / "apps" / "dashboard" / "static" / "project.html"


def _html() -> str:
    return PROJECT_HTML.read_text(encoding="utf-8")


# ── AC1: No progress-section div in running card ──────────────────────────────

def test_no_progress_section_div_in_running_card():
    src = _html()
    # progress-section was the proposed CODER/TESTER agent strip — must never be added
    assert 'class="progress-section' not in src and "progress-section" not in src, (
        "progress-section div found in project.html — CODER/TESTER agent strip must not exist"
    )


# ── AC2: Running state — segmented bar in outcome band ───────────────────────

def test_seg_bar_class_exists_in_running_card_html():
    src = _html()
    # The _smgmtRunningCardHtml function must reference smgmt-seg-bar
    running_match = re.search(
        r'function _smgmtRunningCardHtml\b.*?(?=\nfunction |\n\/\/ ──)',
        src, re.DOTALL
    )
    assert running_match, "_smgmtRunningCardHtml function not found"
    fn = running_match.group(0)
    assert "smgmt-seg-bar" in fn, (
        "smgmt-seg-bar not found in _smgmtRunningCardHtml — segmented bar not added to running state"
    )


def test_oc_spacer_in_running_card_html():
    src = _html()
    running_match = re.search(
        r'function _smgmtRunningCardHtml\b.*?(?=\nfunction |\n\/\/ ──)',
        src, re.DOTALL
    )
    assert running_match, "_smgmtRunningCardHtml function not found"
    fn = running_match.group(0)
    assert "oc-spacer" in fn, (
        "oc-spacer not found in _smgmtRunningCardHtml — spacer needed to right-align seg bar"
    )


def test_est_remaining_label_in_running_outcome_area():
    src = _html()
    running_match = re.search(
        r'function _smgmtRunningCardHtml\b.*?(?=\nfunction |\n\/\/ ──)',
        src, re.DOTALL
    )
    assert running_match, "_smgmtRunningCardHtml function not found"
    fn = running_match.group(0)
    assert "EST. REMAINING" in fn or "est-remaining" in fn or "Est. remaining" in fn, (
        "EST. REMAINING label not found in running card — outcome band label missing"
    )


def test_seg_pending_block_in_running_card():
    src = _html()
    # Running state must have pending blocks (empty/bordered)
    assert "seg-pending" in src, (
        "seg-pending block class not found — pending state blocks missing from segmented bar"
    )


def test_seg_running_block_in_running_card():
    src = _html()
    # In-flight block must be present
    assert "seg-running" in src, (
        "seg-running block class not found — in-flight animated block missing"
    )


def test_seg_done_block_exists():
    src = _html()
    assert "seg-done" in src, (
        "seg-done block class not found — done state blocks missing from segmented bar"
    )


# ── AC3: Rework state — segmented bar (green=done, red=failed) ───────────────

def test_seg_bar_in_rework_outcome_band():
    src = _html()
    # The outcome band HTML function or a rework-specific function must include smgmt-seg-bar
    # For rework state, seg bar is shown in the outcome band
    assert "smgmt-seg-bar" in src, (
        "smgmt-seg-bar not found anywhere in project.html"
    )


def test_seg_failed_block_exists():
    src = _html()
    assert "seg-failed" in src, (
        "seg-failed block class not found — failed state blocks (red) missing for rework state"
    )


def test_rework_seg_bar_function_or_template():
    src = _html()
    # The outcome band or rework rendering must include seg bar logic
    outcome_band_match = re.search(
        r'function _smgmtOutcomeBandHtml\b.*?(?=\nfunction |\n\/\/ ──)',
        src, re.DOTALL
    )
    if outcome_band_match:
        fn = outcome_band_match.group(0)
        assert "smgmt-seg-bar" in fn or "seg-bar" in fn, (
            "_smgmtOutcomeBandHtml does not include segmented bar for rework/finished states"
        )


# ── AC4: Finished state — seg bar (green=done, gray=skipped) + PR + summary ──

def test_seg_skipped_block_exists():
    src = _html()
    assert "seg-skipped" in src, (
        "seg-skipped block class not found — skipped state blocks (gray/faded) missing for finished state"
    )


def test_pr_link_in_finished_outcome_band():
    src = _html()
    # The outcome band for finished state must reference PR link icon and structure
    outcome_band_match = re.search(
        r'function _smgmtOutcomeBandHtml\b.*?(?=\nfunction |\n\/\/ ──)',
        src, re.DOTALL
    )
    assert outcome_band_match, "_smgmtOutcomeBandHtml function not found"
    fn = outcome_band_match.group(0)
    assert "ti-git-pull-request" in fn or "pr_number" in fn or "pr_url" in fn, (
        "PR link (git-pull-request icon or pr_number/pr_url) not found in _smgmtOutcomeBandHtml"
    )


def test_sprint_summary_link_in_finished_outcome_band():
    src = _html()
    outcome_band_match = re.search(
        r'function _smgmtOutcomeBandHtml\b.*?(?=\nfunction |\n\/\/ ──)',
        src, re.DOTALL
    )
    assert outcome_band_match, "_smgmtOutcomeBandHtml function not found"
    fn = outcome_band_match.group(0)
    assert "Sprint Summary" in fn or "summary_issue" in fn, (
        "Sprint Summary link not found in _smgmtOutcomeBandHtml — finished state links missing"
    )


# ── AC5: Level separator rows in running ticket list ─────────────────────────

def test_level_sep_class_in_running_card_html():
    src = _html()
    running_match = re.search(
        r'function _smgmtRunningCardHtml\b.*?(?=\nfunction |\n\/\/ ──)',
        src, re.DOTALL
    )
    assert running_match, "_smgmtRunningCardHtml function not found"
    fn = running_match.group(0)
    assert "level-sep" in fn, (
        "level-sep class not found in _smgmtRunningCardHtml — level separator rows missing"
    )


def test_level_sep_label_text():
    src = _html()
    # Separator must show "Level N" and "runs after" text
    assert "runs after" in src, (
        "'runs after' text not found — level separator description missing"
    )


def test_level_sep_data_used_from_dispatch_level():
    src = _html()
    # Level sep rendering must use dispatch_level field from live data
    assert "dispatch_level" in src, (
        "dispatch_level field not referenced — level separators need per-ticket level data"
    )


# ── AC6: Level separators only in running state ───────────────────────────────

def test_level_sep_absent_from_outcome_band_html():
    src = _html()
    # _smgmtOutcomeBandHtml (used for non-running states) must NOT render level-sep rows
    outcome_band_match = re.search(
        r'function _smgmtOutcomeBandHtml\b.*?(?=\nfunction |\n\/\/ ──)',
        src, re.DOTALL
    )
    if outcome_band_match:
        fn = outcome_band_match.group(0)
        assert "level-sep" not in fn, (
            "level-sep found in _smgmtOutcomeBandHtml — level separators must only appear in running state"
        )


def test_level_sep_absent_from_outcome_ticket_list():
    src = _html()
    ticket_list_match = re.search(
        r'function _smgmtOutcomeTicketListHtml\b.*?(?=\nfunction |\n\/\/ ──)',
        src, re.DOTALL
    )
    if ticket_list_match:
        fn = ticket_list_match.group(0)
        assert "level-sep" not in fn, (
            "level-sep found in _smgmtOutcomeTicketListHtml — separators must only appear in running state"
        )


# ── AC7: Inline agent badges on running tickets ───────────────────────────────

def test_agent_badge_coder_class_purple():
    src = _html()
    # coder-class badge must use --purple CSS variable
    assert "coder-class" in src, "coder-class not found — CODER agent badge class missing"
    # CSS for coder-class must use --purple
    assert "var(--purple)" in src, "var(--purple) not found — purple color token not used"
    coder_css_match = re.search(r'\.smgmt-ticket-agent-tag\.coder-class\s*\{[^}]+\}', src)
    if coder_css_match:
        rule = coder_css_match.group(0)
        assert "--purple" in rule, (
            "coder-class CSS rule does not use --purple — CODER badge must be purple"
        )


def test_agent_badge_tester_class_amber():
    src = _html()
    assert "tester-class" in src, "tester-class not found — TESTER agent badge class missing"
    assert "var(--amber)" in src, "var(--amber) not found — amber color token not used"
    tester_css_match = re.search(r'\.smgmt-ticket-agent-tag\.tester-class\s*\{[^}]+\}', src)
    if tester_css_match:
        rule = tester_css_match.group(0)
        assert "--amber" in rule, (
            "tester-class CSS rule does not use --amber — TESTER badge must be amber"
        )


def test_agent_badge_placed_after_title_in_running_card():
    src = _html()
    running_match = re.search(
        r'function _smgmtRunningCardHtml\b.*?(?=\nfunction |\n\/\/ ──)',
        src, re.DOTALL
    )
    assert running_match, "_smgmtRunningCardHtml function not found"
    fn = running_match.group(0)
    # The template return string must have smgmt-ticket-title before agentTagHtml variable
    # (the badge HTML is in agentTagHtml which is injected after the title in the template)
    template_match = re.search(r'return\s+[`"\'].*?smgmt-ticket', fn, re.DOTALL)
    assert "smgmt-ticket-title" in fn, "smgmt-ticket-title not found in running card"
    assert "smgmt-ticket-agent-tag" in fn, "smgmt-ticket-agent-tag not found in running card"
    # Verify in the template literal: title must come before agentTagHtml placeholder
    template_literal = re.search(r'`<div class="smgmt-ticket".*?</div>`', fn, re.DOTALL)
    if template_literal:
        tmpl = template_literal.group(0)
        title_pos = tmpl.find("smgmt-ticket-title")
        agent_pos = tmpl.find("agentTagHtml")
        assert title_pos != -1, "smgmt-ticket-title not in ticket template"
        assert agent_pos != -1, "agentTagHtml not in ticket template"
        assert agent_pos > title_pos, (
            "agentTagHtml appears BEFORE smgmt-ticket-title in template — badge must appear after title"
        )


# ── AC8: CSS uses design-system variables only ────────────────────────────────

def test_seg_done_uses_green_token():
    src = _html()
    seg_done_match = re.search(r'\.seg-done\s*\{[^}]+\}', src)
    assert seg_done_match, ".seg-done CSS rule not found"
    rule = seg_done_match.group(0)
    assert "var(--green)" in rule, (
        ".seg-done CSS must use var(--green) not a hardcoded colour"
    )


def test_seg_failed_uses_red_token():
    src = _html()
    seg_failed_match = re.search(r'\.seg-failed\s*\{[^}]+\}', src)
    assert seg_failed_match, ".seg-failed CSS rule not found"
    rule = seg_failed_match.group(0)
    assert "var(--red)" in rule, (
        ".seg-failed CSS must use var(--red) not a hardcoded colour"
    )


def test_seg_running_uses_blue_token():
    src = _html()
    seg_running_match = re.search(r'\.seg-running\s*\{[^}]+\}', src)
    assert seg_running_match, ".seg-running CSS rule not found"
    rule = seg_running_match.group(0)
    assert "var(--blue)" in rule, (
        ".seg-running CSS must use var(--blue) not a hardcoded colour"
    )


def test_seg_skipped_uses_surface_or_muted_token():
    src = _html()
    seg_skipped_match = re.search(r'\.seg-skipped\s*\{[^}]+\}', src)
    assert seg_skipped_match, ".seg-skipped CSS rule not found"
    rule = seg_skipped_match.group(0)
    assert "var(--" in rule, (
        ".seg-skipped CSS must use a CSS variable token, not a hardcoded colour"
    )


def test_no_hardcoded_hex_in_new_seg_css():
    src = _html()
    # Check seg-* CSS rules for hardcoded hex colors
    seg_rules = re.findall(r'\.seg-\w+\s*\{[^}]+\}', src)
    for rule in seg_rules:
        hex_matches = re.findall(r'#[0-9a-fA-F]{3,6}\b', rule)
        assert not hex_matches, (
            f"Hardcoded hex colour found in seg-* CSS rule: {rule} — use CSS variables instead"
        )


# ── AC9: seg-running animates with 1.1s ease-in-out pulse ────────────────────

def test_seg_pulse_animation_defined():
    src = _html()
    assert "seg-pulse" in src or "segPulse" in src, (
        "seg-pulse keyframe animation not defined — seg-running pulsing animation missing"
    )


def test_seg_running_uses_1_1s_duration():
    src = _html()
    # The animation duration must be 1.1s
    seg_running_match = re.search(r'\.seg-running\s*\{[^}]+\}', src)
    assert seg_running_match, ".seg-running CSS rule not found"
    rule = seg_running_match.group(0)
    assert "1.1s" in rule, (
        ".seg-running animation must use 1.1s duration — found different value or no animation"
    )


def test_seg_running_uses_ease_in_out():
    src = _html()
    seg_running_match = re.search(r'\.seg-running\s*\{[^}]+\}', src)
    if seg_running_match:
        rule = seg_running_match.group(0)
        assert "ease-in-out" in rule, (
            ".seg-running must use ease-in-out timing function"
        )


# ── AC10: .oc-spacer flex:1 ───────────────────────────────────────────────────

def test_oc_spacer_flex_1_css():
    src = _html()
    oc_spacer_match = re.search(r'\.oc-spacer\s*\{[^}]+\}', src)
    assert oc_spacer_match, ".oc-spacer CSS rule not found"
    rule = oc_spacer_match.group(0)
    assert "flex" in rule and "1" in rule, (
        ".oc-spacer CSS must set flex: 1 to right-align the seg-bar and time"
    )
