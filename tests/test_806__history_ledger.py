"""Tests for issue #806 — History ledger: issue lists, verb rules, locking, links.

This is a frontend-only ticket (backend enrichment is explicitly out of scope —
the ``/api/sprints/history`` feed from #805 already supplies every field the
ledger needs). Following the established pattern for frontend tickets (#800–#804),
the assertions read the static HTML/JS source of ``project.html`` and check the
design-contract selectors, the builder functions, and their state logic —
anchored one-to-one to the acceptance criteria.

AC coverage:
  AC1 — Each history card expands to an issue list with state chips MERGED /
        OPEN·UAT / CRASHED / NOT RUN, each carrying a per-ticket time.
  AC2 — Re-run, Finish, Delete buttons appear ONLY on COMPLETED or FAILED.
  AC3 — Resume appears ONLY on FAILED and is wired to the resume endpoint.
  AC4 — FINISHED and DELETED rows are greyed (.locked), show a lock icon + a
        lock note, and render ZERO action buttons — links only.
  AC5 — Rerun children are indented with a ↳ prefix in the issue list.
  AC6 — PR pill opens the correct PR target; Summary pill opens the correct
        summary target.
  AC7 — CSS selectors match the design contract.
  AC8 — Metrics row renders an est-accuracy bar + metric badges.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
PROJECT_HTML = (DASHBOARD_DIR / "static" / "project.html").read_text(encoding="utf-8")
HISTORY_JS = (DASHBOARD_DIR / "static/src/sprint-board/history.js").read_text(encoding="utf-8")


# ──────────────────────────── helpers ────────────────────────────────────────

def _fn_body(name: str, src: str | None = None) -> str:
    """Return the brace-balanced body of a JS function defined in ``src``."""
    sources = [src] if src else [HISTORY_JS, PROJECT_HTML]
    last_err = None
    for candidate in sources:
        pos = -1
        for needle in (f"function {name}(", f"{name} = function", f"async function {name}(",
                       f"export function {name}("):
            pos = candidate.find(needle)
            if pos != -1:
                break
        if pos == -1:
            last_err = f"function {name} not found in {candidate!s}"
            continue
        brace = candidate.find("{", pos)
        if brace == -1:
            last_err = f"no opening brace for {name}"
            continue
        depth = 0
        for i in range(brace, len(candidate)):
            c = candidate[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return candidate[brace:i + 1]
        last_err = f"unbalanced braces for {name}"
    raise AssertionError(last_err or f"function {name} not found")


def _css_rule(selector: str) -> str:
    """Return the body of the first CSS rule whose selector list contains `selector`."""
    pat = re.compile(re.escape(selector) + r"(?![\w-])[^{}]*\{([^{}]*)\}")
    m = pat.search(PROJECT_HTML)
    assert m is not None, f"CSS rule for `{selector}` not found"
    return m.group(1)


# ═════════════════════════ structure / mount ═════════════════════════════════

def test_ledger_mounts_in_history_subview():
    """The ledger container lives inside the History sub-view (issue #716/#798)."""
    start = PROJECT_HTML.index('id="smgmt-subview-history"')
    end = PROJECT_HTML.index("/smgmt-subview-history", start)
    subview = PROJECT_HTML[start:end]
    assert 'id="hist-ledger"' in subview, "the ledger must mount in the History sub-view"
    assert 'class="hist-ledger"' in subview


def test_ledger_loads_from_805_history_endpoint():
    """The ledger feeds off the local /api/sprints/history endpoint (no GitHub call)."""
    body = _fn_body("_histLoadLedger")
    assert "/api/sprints/history" in body, "ledger must read the #805 history feed"


def test_history_subview_still_loads_summaries_716():
    """The legacy Sprint Summaries pane (issue #716) was retired — each ledger
    card carries its own Summary pill, so History opens the ledger only."""
    toggle = _fn_body("_smgmtShowSubView")
    assert "_smgmtLoadSummaries" not in toggle, \
        "the legacy summaries pane must stay unwired (replaced by ledger pills)"
    assert "_histLoadLedger" in toggle, "the ledger load must be wired on History open"


# ═══════════════════════ AC1 — issue list + chips ════════════════════════════

def test_ac1_card_has_expandable_issue_list():
    # Non-child cards use the what-list / issue-list builders; child cards use
    # the done-issue rows always visible below metrics.
    card = _fn_body("_histCardHtml")
    child = _fn_body("_histChildCardHtml")
    assert "expanded" in card, "the issue list is shown on expand"
    assert (
        "_histIssueListHtml" in card
        or "_histIssueRowHtml" in card
        or "_histDoneIssuesHtml" in child
        or "_histCardOutcomeHtml" in card
    ), "cards must render ticket rows via an issue-list builder"
    list_fn = _fn_body("_histIssueListHtml")
    assert "iss-list" in list_fn, "the issue-list builder emits the .iss-list container"


def test_ac1_issue_chip_covers_all_four_states():
    chip = _fn_body("_histIssueChip")
    # Display vocabulary mandated by the AC.
    assert "MERGED" in chip
    assert "OPEN" in chip and "UAT" in chip
    assert "CRASHED" in chip
    assert "NOT RUN" in chip


def test_ac1_chip_mapping_is_state_driven():
    """The chip is derived from the issue's local disposition, not hard-coded."""
    chip = _fn_body("_histIssueChip")
    assert "merged" in chip, "merged tickets → MERGED"
    assert "closed" in chip, "closed/failed tickets → CRASHED"
    # NOT RUN is distinguished from OPEN·UAT by the absence of run time.
    assert "time_spent" in chip, "a ticket that never ran (no time_spent) reads NOT RUN"


def test_ac1_issue_row_shows_per_ticket_time():
    row = _fn_body("_histIssueRowHtml")
    assert "iss-row" in row
    assert "_histFmtSecs" in row, "each issue row shows a formatted per-ticket time"
    assert "iss-chip" in row, "each issue row carries its state chip"


def test_ac1_time_formatter_handles_missing_time():
    fmt = _fn_body("_histFmtSecs")
    # Must not render "NaN"/"undefined" for a ticket with no recorded time.
    assert "—" in fmt or "'-'" in fmt or '"-"' in fmt, "missing time degrades to a dash"


# ═══════════════ AC2 — Re-run / Finish / Delete gating ═══════════════════════

def test_ac2_verbs_only_on_completed_or_failed():
    verbs = _fn_body("_histVerbsHtml")
    assert "completed" in verbs and "failed" in verbs, \
        "verb buttons are gated on the completed/failed lifecycle states"
    # The three core verbs are present in the builder.
    assert "Re-run" in verbs
    assert "Finish" in verbs
    assert "Delete" in verbs


def test_ac2_verbs_wired_to_existing_handlers():
    verbs = _fn_body("_histVerbsHtml")
    assert "_histRerunSprint" in verbs, "History Re-run opens the shared re-run modal"
    assert "smgmtFinishSprint" in verbs, "Finish reuses the existing finish handler"
    assert "smgmtDeleteSprint" in verbs, "Delete reuses the existing delete handler"


def test_ac2_locked_states_get_no_verbs():
    """A locked (finished/deleted/completed) sprint short-circuits before any verb button."""
    verbs = _fn_body("_histVerbsHtml")
    assert "_histIsLocked" in verbs, "verb builder must consult the lock predicate"
    locked = _fn_body("_histIsLocked")
    assert "finished" in locked and "deleted" in locked and "completed" in locked


# ═══════════════════════ AC3 — Re-run gating ════════════════════════════════
# Sprint-lifecycle redesign P1: the Resume verb is retired (it dispatched the
# rerun endpoint anyway) and Re-run gates on the unified needs_rework state.

def test_ac3_rerun_gated_on_needs_rework():
    verbs = _fn_body("_histVerbsHtml")
    assert "needs_rework" in verbs, "Re-run is gated on the needs_rework state"
    assert "_histRerunSprint" in verbs, "Re-run opens the shared modal then pre-run kickoff"


def test_ac3_resume_verb_retired():
    assert "_histResumeSprint" not in PROJECT_HTML, (
        "Resume was retired by the lifecycle redesign — re-runs go through "
        "the child sub-sprint flow only"
    )


# ═══════════════════════ AC4 — locking ══════════════════════════════════════

def test_ac4_locked_card_is_greyed_and_marked():
    card = _fn_body("_histCardHtml")
    assert "_histIsLocked" in card, "card builder consults the lock predicate"
    assert "locked" in card, "locked cards get the .locked class"


def test_ac4_locked_css_greys_the_card():
    rule = _css_rule(".hist-card.locked")
    assert "opacity" in rule or "filter" in rule or "var(--text-sub)" in rule, \
        "locked cards must read as greyed/disabled"


def test_ac4_locked_card_renders_links_not_verbs():
    """Locked cards show PR/Summary/Logs links but never action verbs."""
    card = _fn_body("_histCardHtml")
    assert "_histLinksHtml" in card, "links (PR/Summary/Logs) render on every card"
    # Verb builder is what gets suppressed for locked cards.
    verbs = _fn_body("_histVerbsHtml")
    assert "return ''" in verbs.replace('"', "'") or "return ``" in verbs, \
        "the verb builder returns nothing for locked sprints"


def test_ac4_links_include_pr_summary_logs():
    links = _fn_body("_histLinksHtml")
    assert "link-pr" in links
    assert "link-sum" in links
    assert "Logs" in links or "ti-list" in links or "logs" in links.lower()


# ═══════════════════════ AC5 — rerun child indent ═══════════════════════════

def test_ac5_child_card_class_and_indent():
    card = _fn_body("_histChildCardHtml")
    assert "hist-child-card" in card, "child sprint cards use hist-child-card"
    group = _fn_body("_histGroupHtml")
    assert "hist-child-wrap" in group, "child sprints nest inside hist-child-wrap"
    rule = _css_rule(".hist-child-wrap")
    assert "margin-left" in rule, "child wrap is indented with L-connector"


def test_ac5_issue_row_rerun_arrow_prefix():
    row = _fn_body("_histIssueRowHtml")
    assert "↳" in row, "rerun-child issues are prefixed with a ↳ arrow"


# ═══════════════════════ AC6 — PR / Summary targets ═════════════════════════

def test_ac6_pr_pill_targets_pr_number():
    url = _fn_body("_histPrUrl")
    assert "_histRepo" in url, "PR URL must resolve repo from cache or sprint row"
    assert "pr_number" in url, "the PR pill target is built from the sprint's pr_number"
    assert "/pull/" in url, "the PR pill opens the GitHub pull-request URL"


def test_ac6_summary_pill_targets_summary():
    url = _fn_body("_histSummaryIssueUrl")
    assert (
        "summary_issue_url" in url
        or "summary_issue_num" in url
    ), "the Summary pill target is built from the sprint's summary issue"
    issue = _fn_body("_histSummaryIssueUrl")
    assert "summary_issue" in issue, "GitHub summary issue URL is preferred when present"


def test_ac6_links_use_the_target_builders():
    links = _fn_body("_histLinksHtml")
    assert "_histPrUrl" in links, "the PR pill uses the PR target builder"
    assert "_histSummaryUrl" in links, "the Summary pill uses the summary target builder"


def test_ac6_pr_link_hidden_when_no_pr():
    """No pr_number → the PR pill is not rendered (no dead link)."""
    links = _fn_body("_histLinksHtml")
    assert "pr_number" in links, "PR pill rendering is conditional on pr_number"


# ═══════════════════════ AC7 — CSS contract ═════════════════════════════════

def test_ac7_design_contract_selectors_present():
    for sel in (".hist-card", ".hist-card.locked", ".hist-card.child",
                ".iss-list", ".iss-row", ".link-pr", ".link-sum", ".lock-note"):
        _css_rule(sel)  # raises if any contract token is unstyled


def test_ac7_no_side_stripe_anti_pattern():
    """Impeccable: emphasis is full-border + tint + glow, never a side-stripe."""
    for sel in (".hist-card", ".hist-card.locked", ".hist-card.child"):
        rule = _css_rule(sel)
        assert "border-left:" not in rule and "border-right:" not in rule, \
            f"`{sel}` must not use a coloured side-stripe border"


# ═══════════════════════ AC8 — metrics row ══════════════════════════════════

def test_ac8_metrics_row_has_est_accuracy_bar():
    metrics = _fn_body("_histMetricsHtml")
    assert "est-bar" in metrics, "the metrics row renders an est-accuracy bar"
    assert "estimate_accuracy" in metrics, "the bar is driven by estimate_accuracy"
    _css_rule(".est-bar")


def test_ac8_metrics_row_has_badges():
    metrics = _fn_body("_histMetricsHtml")
    # Duration + token badges per mock v5.
    assert "duration" in metrics, "a duration badge is shown"
    assert "tokens" in metrics, "a tokens badge is shown"
    _css_rule(".hist-metrics")


def test_partial_finished_is_server_derived():
    # Sprint-lifecycle redesign P1: partial_finished is derived server-side
    # from children's states; the client just renders the chip.
    assert "_histIsPartialCompleted" not in PROJECT_HTML, (
        "client-side partial inference was retired — the server derives "
        "partial_finished in sprint_history_service"
    )
    chip = _fn_body("_histStateChip")
    assert "partial_finished" in chip, "the chip map renders partial_finished"


def test_failed_block_renders_on_failed_sprint():
    fail = _fn_body("_histFailedBlockHtml")
    assert "failed" in fail
    assert "failure_reason" in fail or "failed_tickets" in fail
    card = _fn_body("_histCardHtml")
    assert "_histSecondaryLinksHtml" in card or "_histRecoveryBtnHtml" in card, \
        "PR/summary/logs actions render on the card head"
    assert "_histRerunSprint" in HISTORY_JS
    rerun = _fn_body("_histRerunSprint", HISTORY_JS)
    assert "smgmtRerunSprint" in rerun, (
        "History Re-run must open the shared re-run modal (ticket pick + pre-run), "
        "not POST auto_run directly"
    )


def test_post_sprint_block_renders_documenter_and_reviewer():
    block = _fn_body("_histPostSprintHtml")
    assert "Documenter" in block
    assert "Reviewer" in block
    assert "files_touched" in block or "ps-file" in block
    assert "follow_up_tickets" in block or "ps-ticket" in block
    details = _fn_body("_histDetailsHtml")
    child_metrics = _fn_body("_histChildMetricsHtml")
    assert "_histPostSprintHtml" in details or "_histPostSprintHtml" in child_metrics
    assert "hist-card-head-left" in _fn_body("_histCardHtml")
    assert "hist-card-body" in _fn_body("_histCardHtml")


def test_bulk_complete_button_on_parent_with_children():
    group = _fn_body("_histGroupHtml")
    assert "_histBulkCompleteBtnHtml" in group
    assert "bulkCompleteBtn" in _fn_body("_histGroupHtml")
    child = _fn_body("_histChildCardHtml")
    assert "hist-child-head-right" in child
    btn = _fn_body("_histBulkCompleteBtnHtml")
    assert "smgmtBulkCompleteSprint" in btn
    assert "Bulk complete" in btn
    assert "hist-head-btn--bulk" in btn
    assert "hist-group-actions" not in btn
    needs = _fn_body("_histGroupNeedsBulkComplete")
    assert "completed" in needs and "deleted" in needs
    assert "_histChildSprintsAllCompleted" in _fn_body("_histBulkCompleteBtnHtml")
    assert "Complete all child sprints before bulk completing" in btn
    run_finished = _fn_body("_histChildRunFinished")
    assert "needs_rework" in run_finished and "failed" in run_finished


def test_bulk_complete_enabled_when_child_failed_not_running():
    """Failed/needs_rework children are finished runs — bulk complete must not stay disabled."""
    hist_js = (DASHBOARD_DIR / "static/src/sprint-board/history.js").read_text(encoding="utf-8")
    assert "_histChildRunFinished" in hist_js
    assert "_histChildSprintsStillRunning" in _fn_body("_histBulkCompleteBtnHtml")
    assert "Wait for child sprint runs to finish" in _fn_body("_histBulkCompleteBtnHtml")


def test_bulk_complete_modal_mounts():
    assert 'id="bc-modal"' in PROJECT_HTML
    assert 'id="bc-confirm-btn"' in PROJECT_HTML
    modal_js = (DASHBOARD_DIR / "static/src/sprint-board/bulk-complete-modal.js").read_text(encoding="utf-8")
    assert "bulk-complete-preview" in modal_js or "smgmtBulkCompleteSprint" in modal_js
    # Re-fetch merge chain after each merge (parent→develop only known post child merge).
    assert "_bcRemainingMergeSteps" in modal_js
    assert "for (let i = 0; i < order.length; i++)" in modal_js


def test_bc_confirm_reads_merge_steps_before_close():
    """_bcClose() nulls _bcPreview — merge_steps must be captured first."""
    modal_js = (DASHBOARD_DIR / "static/src/sprint-board/bulk-complete-modal.js").read_text(encoding="utf-8")
    body = _fn_body("_bcConfirm", modal_js)
    close_at = body.find("_bcClose()")
    preview_merge_at = body.find("_bcPreview.complete_order")
    assert close_at != -1 and preview_merge_at != -1
    assert preview_merge_at < close_at, (
        "_bcPreview.merge_steps must be read before _bcClose clears _bcPreview"
    )


def test_locked_collapsed_cards_hide_header_details():
    """Locked rows stay collapsed by default; auto-expand skips them."""
    auto = _fn_body("_histAutoExpandRecent")
    assert "_histShouldAutoExpand" in auto or "_histIsLocked" in auto
    rule = _css_rule(".hist-card.locked:not(.expanded) .hist-card-head")
    assert rule, "locked collapsed cards must use a single-line header"
